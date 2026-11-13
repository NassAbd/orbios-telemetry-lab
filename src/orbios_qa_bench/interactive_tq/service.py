"""Local orchestration: typed preview, fixture lookup and atomic fictional catalogue edits."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import TypedDict

import numpy as np
from numpy.typing import NDArray
from pydantic import TypeAdapter

from ..encoder import EncoderPlanner, bridge
from ..tq_contract import Parameter, compile_translation
from .contract import Ask, Candidate, Inspect, Manifest, Mode, Remove, Reply, Request, Save
from .planner import ROLE_ALIASES, Planner, normalized
from .simulator import Scenario, Simulator

APPS = TypeAdapter(list[Manifest])
MODEL_DIR = "models/paraphrase-multilingual-MiniLM-L12-v2"
MAX_APPS = 16


class Update(TypedDict):
    revision: int
    index_ms: float | None
    update_ms: float
    capability_count: int


class Service:
    def __init__(self, root: Path, *, enable_semantic: bool = True) -> None:
        data = root / "benchmark/interactive_tq"
        source = json.loads((root / "benchmark/tq_poc/catalog.json").read_text())
        self.base = {row["id"]: Parameter.model_validate(row) for row in source["base"] if row["id"] in {"APPS", "A.wildfire.alert"} or row["id"].startswith("P")}
        self.initial = APPS.validate_json((data / "applications.json").read_bytes())
        self.apps = {app.id: app for app in self.initial}
        self.simulator = Simulator(Scenario.model_validate_json((data / "scenario.json").read_bytes()))
        self.catalog = self.make_catalog(self.apps)
        self.model_path = root / MODEL_DIR
        self.encoder: EncoderPlanner | None = None
        self.vectors: NDArray | None = None
        self.vector_ids: dict[str, int] = {}
        self.enable_semantic = enable_semantic
        self.revision = 1
        self.planner = self.make_planner(self.catalog, self.apps)

    def make_planner(self, catalog: dict[str, Parameter], apps: dict[str, Manifest]) -> Planner:
        return Planner(catalog, apps, self.simulator.now, self.rank if self.enable_semantic else None)

    def make_catalog(self, apps: dict[str, Manifest]) -> dict[str, Parameter]:
        if len(apps) > MAX_APPS:
            raise ValueError(f"The demo supports up to {MAX_APPS} fictional applications.")
        labels = [normalized(app.label) for app in apps.values()]
        keys = {normalized(a) for a in apps}
        if any(not label for label in labels) or len(set(labels)) != len(labels):
            raise ValueError("Use distinct application labels with English letters or numbers.")
        for app in apps.values():
            if normalized(app.label) in keys - {normalized(app.id)}:
                raise ValueError("An application label conflicts with another application ID.")
        catalog = dict(self.base)
        for app in apps.values():
            for spec in app.metrics:
                mid = f"A.{app.id}.{spec.id}"
                if mid in catalog or app.id == "wildfire":
                    raise ValueError("That identifier is reserved for an unavailable documentary capability.")
                catalog[mid] = Parameter(
                    id=mid, description=f"{app.label} · {spec.label}",
                    aliases=[spec.label, *spec.aliases, *ROLE_ALIASES.get(spec.id, [])],
                    kind=spec.kind, unit=spec.unit, every_s=10 if spec.kind == "number" else None,
                    registered=spec.registered, limits={"budget": float(app.budget_s)} if spec.id == "turn_used_s" else {},
                    provenance=f"Fictional application manifest {app.id} v{app.version}",
                )
        return catalog

    @staticmethod
    def build_index(encoder: EncoderPlanner, catalog: dict[str, Parameter]) -> tuple[dict[str, int], NDArray]:
        records = [p for p in catalog.values() if p.registered]
        texts = [p.description + "; " + "; ".join(p.aliases) for p in records]
        rows = np.vstack([encoder.encode(texts[start:start + 16]) for start in range(0, len(texts), 16)])
        return {p.id: i for i, p in enumerate(records)}, rows

    def warm_index(self) -> float:
        start = time.perf_counter()
        if self.encoder is None:
            if not self.enable_semantic:
                raise ValueError("Semantic inference is disabled in this worker.")
            encoder = EncoderPlanner(bridge({"P001": self.catalog["P001"]}), self.model_path, "embedding")
            ids, vectors = self.build_index(encoder, self.catalog)
            self.encoder, self.vector_ids, self.vectors = encoder, ids, vectors
        return (time.perf_counter() - start) * 1000

    def rank(self, clause: str, pool: list[str]) -> list[Candidate]:
        self.warm_index()
        if self.encoder is None or self.vectors is None:
            raise RuntimeError("The semantic index is not ready.")
        query_vector = self.encoder.encode([clause])[0]
        valid = [mid for mid in pool if mid in self.vector_ids]
        scores = self.vectors[[self.vector_ids[mid] for mid in valid]] @ query_vector
        return sorted((Candidate(id=mid, score=float(score)) for mid, score in zip(valid, scores, strict=True)),
                      key=lambda row: (-(row.score or 0.0), row.id))

    def replace(self, apps: dict[str, Manifest]) -> Update:
        start = time.perf_counter()
        catalog = self.make_catalog(apps)
        planner = self.make_planner(catalog, apps)
        index_ms = None
        if self.encoder is not None:
            indexed = time.perf_counter()
            ids, vectors = self.build_index(self.encoder, catalog)
            index_ms = (time.perf_counter() - indexed) * 1000
            self.vector_ids, self.vectors = ids, vectors
        self.catalog, self.apps, self.planner = catalog, apps, planner
        self.revision += 1
        return {"revision": self.revision, "index_ms": index_ms, "update_ms": (time.perf_counter() - start) * 1000, "capability_count": len(catalog)}

    def save(self, manifest: Manifest) -> Update:
        return self.replace(self.apps | {manifest.id: manifest})

    def remove(self, app_id: str) -> Update:
        if app_id not in self.apps:
            raise ValueError("No fictional application has that ID.")
        return self.replace({mid: app for mid, app in self.apps.items() if mid != app_id})

    def ask(self, question: str, mode: Mode) -> Reply:
        start = time.perf_counter()
        translation, selected, decisions, calls = self.planner.plan(question, mode)
        lines = compile_translation(translation, self.catalog)
        readings = [self.simulator.read(q, self.catalog, self.apps) for q in translation.queries]
        return Reply(mode=mode, translation=translation, lines=lines, selected=selected, decisions=decisions,
                     readings=readings, semantic_calls=calls, elapsed_ms=(time.perf_counter() - start) * 1000)

    def inspect(self) -> dict[str, object]:
        return {
            "scenario": self.simulator.scenario.id, "clock": self.simulator.now.isoformat(),
            "seed": self.simulator.scenario.seed, "revision": self.revision, "indexed": self.encoder is not None,
            "apps": [a.model_dump() for a in sorted(self.apps.values(), key=lambda a: a.id)],
            "catalogue": [p.model_dump() for p in sorted(self.catalog.values(), key=lambda p: p.id)],
        }

    def dispatch(self, request: Request) -> dict[str, object]:
        if isinstance(request, Ask):
            return self.ask(request.question, request.mode).model_dump()
        if isinstance(request, Inspect):
            return self.inspect()
        if isinstance(request, Save):
            return dict(self.save(request.manifest))
        if isinstance(request, Remove):
            return dict(self.remove(request.id))
        raise ValueError("Unsupported request.")
