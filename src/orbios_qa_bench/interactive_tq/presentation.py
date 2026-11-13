"""Expose runtime metadata and execution evidence to the loopback UI."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, TypeAdapter

from ..tq_contract import OPS, Closed, Parameter, Query, compile_query
from .contract import Ask, Inspect, Remove, Save
from .service import Service


class InspectMetric(Closed):
    action: Literal["metric"]
    id: Annotated[str, StringConstraints(min_length=1, max_length=80)]


class Health(Closed):
    action: Literal["health"]


LabRequest = Annotated[Ask | Inspect | Remove | Save | InspectMetric | Health, Field(discriminator="action")]
LAB_REQUEST = TypeAdapter[LabRequest](LabRequest)

GROUPS = (
    "Thermal measurements", "Bench PDU measurements", "Pi power supply", "Power rails",
    "Application states and execution budgets", "Application detection events",
    "Fictional custom application metrics", "Declared but unavailable",
    "Application inventory",
)


class Lab:
    def __init__(self, root: Path, service: Service) -> None:
        self.service = service
        source = json.loads((root / "benchmark/tq_poc/catalog.json").read_text())
        self.documented = {row["id"]: Parameter.model_validate(row) for row in source["base"]}

    def metadata(self, p: Parameter) -> dict[str, object]:
        service = self.service
        app_id = p.id.split(".")[1] if p.id.startswith("A.") else None
        app = service.apps.get(app_id) if app_id else None
        reference = self.documented.get(p.id)
        guide_match = reference is not None and (reference.kind, reference.unit) == (p.kind, p.unit)
        if not p.registered:
            group = GROUPS[7]
        elif p.id in {"P001", "P002"}:
            group = GROUPS[0]
        elif p.id.startswith("P01"):
            group = GROUPS[1]
        elif p.id.startswith("P02"):
            group = GROUPS[2]
        elif p.id.startswith("P1"):
            group = GROUPS[3]
        elif p.kind == "apps":
            group = GROUPS[8]
        elif guide_match:
            group = GROUPS[5] if p.kind == "event" else GROUPS[4]
        else:
            group = GROUPS[6]

        origin = "Fictional manifest"
        if guide_match:
            origin = "Derived from Ben’s guide" if app or p.kind == "apps" else "Documented by Ben"

        notes = []
        if guide_match and reference:
            if p.id.startswith("P01"):
                notes.append("The guide describes bench mains readings.")
            if p.id == "P022":
                notes.append("This is the SoC’s own voltage reading. P170 measures the VDD_CORE rail.")
            elif p.id == "P170":
                notes.append("This measures the VDD_CORE rail. P022 measures the SoC’s own voltage.")
            if app:
                notes.append(reference.description + ". The label, version, limits and history shown here come from the fictional session.")
            if p.kind == "event":
                notes.append("The guide describes an application result with an optional score.")
        if p.kind == "apps":
            notes.append("Lists the current session’s fictional applications and versions.")
        if not p.registered:
            notes.append("This declaration has no queryable synthetic history.")

        return {
            **p.model_dump(),
            "name": p.description.split(" · ", 1)[-1],
            "group": group,
            "operations": sorted(OPS[p.kind]),
            "origin": origin,
            "documented_cadence_s": reference.every_s if guide_match and reference else None,
            "application_id": app_id,
            "application_label": app.label if app else None,
            "application_version": app.version if app else None,
            "notes": notes,
        }

    def detail(self, metric_id: str) -> dict[str, object]:
        service = self.service
        p = service.catalog.get(metric_id)
        if p is None:
            raise ValueError("This capability is absent from the current catalogue.")
        result = self.metadata(p) | {"revision": service.revision, "preview": None, "example": None}
        if p.registered:
            op = {"number": "stats", "state": "transitions", "event": "count", "apps": "list"}[p.kind]
            query = Query.model_validate({"id": 1, "param": p.id, "window": "now" if p.kind == "apps" else "1h", "op": op})
            question = {
                "number": f"What are the statistics for {p.id} in the last hour?",
                "state": f"What were the state changes for {p.id} in the last hour?",
                "event": f"How many events did {p.id} record in the last hour?",
                "apps": "Which applications are tracked?",
            }[p.kind]
            result["example"] = {"question": question, "line": compile_query(query, service.catalog)}
            if p.kind != "apps":
                result["preview"] = service.simulator.read(query, service.catalog, service.apps).model_dump()
        return result

    def dispatch(self, request: LabRequest) -> dict[str, object]:
        service = self.service
        if isinstance(request, Health):
            return {"status": "ready"}
        if isinstance(request, InspectMetric):
            return self.detail(request.id)
        if isinstance(request, Inspect):
            return service.inspect() | {
                "catalogue": [self.metadata(service.catalog[mid]) for mid in sorted(service.catalog)],
                "groups": GROUPS,
            }
        if isinstance(request, Ask):
            reply = service.ask(request.question, request.mode)
            ids = set(reply.selected)
            ids.update(candidate.id for decision in reply.decisions for candidate in decision.candidates)
            return reply.model_dump() | {
                "question": request.question,
                "catalogue_revision": service.revision,
                "metrics": {mid: self.metadata(service.catalog[mid]) for mid in sorted(ids) if mid in service.catalog},
            }
        return service.dispatch(request)
