"""Held-out question and fixture comparison. Gold fields never reach the planner."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import math
import sys
from pathlib import Path
from typing import Literal

from pydantic import TypeAdapter

from ..tq_contract import Closed
from .contract import Manifest, Mode, Reply
from .sandbox import CGROUP, require_isolation
from .service import Service, Update

Phase = Literal["base", "added", "edited", "removed"]


class Fact(Closed):
    id: str
    fields: dict[str, str]
    completeness: Literal["complete", "partial", "nodata"]
    contains: list[str]


class Case(Closed):
    id: str
    phase: Phase
    group: str
    question: str
    status: Literal["answer", "clarification_required", "unavailable", "out_of_scope"]
    lines: list[str]
    selected: list[str]
    facts: list[Fact]


class EvaluationApps(Closed):
    added: Manifest
    edited: Manifest


def facts_match(reply: Reply, expected: list[Fact]) -> bool:
    if len(reply.readings) != len(expected):
        return False
    for reading, fact in zip(reply.readings, expected, strict=True):
        if (reading.packet.param != fact.id or reading.completeness != fact.completeness
                or not all(reading.packet.fields.get(k) == v for k, v in fact.fields.items())
                or not all(text in reading.text for text in fact.contains)
                or not reading.source.startswith("Synthetic fixture")):
            return False
    return True


def percentile(values: list[float], p: float) -> float:
    return sorted(values)[max(0, math.ceil(len(values) * p) - 1)]


def evaluate(root: Path, mode: Mode) -> dict[str, object]:
    limits = require_isolation()
    data = root / "benchmark/interactive_tq"
    freeze = json.loads((data / "EVALUATION_FREEZE.json").read_text())
    for name, expected in freeze["sha256"].items():
        if hashlib.sha256((root / name).read_bytes()).hexdigest() != expected:
            raise ValueError(f"Frozen input changed: {name}")
    scenarios = EvaluationApps.model_validate_json((data / "eval_apps.json").read_bytes())
    cases = TypeAdapter(list[Case]).validate_json((data / "cases.json").read_bytes())
    service = Service(root, enable_semantic=mode != "symbolic")
    warm_ms = service.warm_index() if mode != "symbolic" else None
    original_catalogue_size = len(service.catalog)

    updates: dict[str, Update] = {}
    rows = []
    status_ok = exact_total = safe_abstention = unjustified_abstention = false_answer = fidelity = translated_answers = 0
    tp = fp = fn = parameter_exact = 0
    multi_coverage = []
    multi_exact = 0
    calls = semantic_requests = 0
    latencies = []
    phase: Phase = "base"
    for case in cases:
        if case.phase != phase:
            phase = case.phase
            if phase == "added":
                updates[phase] = service.save(scenarios.added)
            elif phase == "edited":
                updates[phase] = service.save(scenarios.edited)
            elif phase == "removed":
                updates[phase] = service.remove(scenarios.added.id)
            else:
                raise ValueError("Evaluation phases must progress from base to added/edited/removed.")
        reply = service.ask(case.question, mode)
        status_correct = reply.translation.status == case.status
        exact = status_correct and reply.lines == case.lines
        checks = facts_match(reply, case.facts) if case.status == "answer" else not reply.readings
        gold, predicted = set(case.selected), set(reply.selected)
        parameter_exact += gold == predicted
        hits = len(gold & predicted)
        tp += hits
        fp += len(predicted - gold)
        fn += len(gold - predicted)
        status_ok += status_correct
        exact_total += exact
        if case.status != "answer":
            safe_abstention += status_correct
            false_answer += reply.translation.status == "answer"
        else:
            unjustified_abstention += reply.translation.status != "answer"
            fidelity += exact and checks
            translated_answers += exact
        if len(gold) > 1:
            multi_coverage.append(hits / len(gold))
            multi_exact += exact
        calls += reply.semantic_calls
        semantic_requests += reply.semantic_calls > 0
        latencies.append(reply.elapsed_ms)
        rows.append({"gold": case.model_dump(), "reply": reply.model_dump(), "exact": exact, "facts_match": checks,
                     "full_success": exact and checks, "revision": service.revision})

    answers = sum(case.status == "answer" for case in cases)
    return {
        "mode": mode, "cases": len(cases), "initial_catalogue_size": original_catalogue_size,
        "answerable": answers, "exact": exact_total, "status_correct": status_ok,
        "correct_abstention": safe_abstention, "unjustified_abstention": unjustified_abstention, "false_answer": false_answer,
        "param_tp": tp, "param_fp": fp, "param_fn": fn, "parameter_exact": parameter_exact,
        "precision": tp / max(1, tp + fp), "recall": tp / max(1, tp + fn), "f1": 2 * tp / max(1, 2 * tp + fp + fn),
        "multi_cases": len(multi_coverage), "multi_recall": sum(multi_coverage) / max(1, len(multi_coverage)), "multi_exact": multi_exact,
        "fidelity": fidelity, "translated_answers": translated_answers,
        "fidelity_conditional": fidelity / translated_answers if translated_answers else None,
        "latency_p50_ms": percentile(latencies, 0.5), "latency_p95_ms": percentile(latencies, 0.95),
        "semantic_calls": calls, "semantic_requests": semantic_requests,
        "initial_load_and_index_ms": warm_ms, "catalogue_updates": updates,
        "peak_bytes": int((CGROUP / "memory.peak").read_text()), "memory_events": (CGROUP / "memory.events").read_text(),
        "cpu_stat": (CGROUP / "cpu.stat").read_text(), "isolation": limits,
        "runtime": {"python": sys.version.split()[0], **{name: importlib.metadata.version(name) for name in ("onnxruntime", "numpy", "pydantic", "tokenizers")}},
        "rows": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("symbolic", "minilm", "hybrid"))
    args = parser.parse_args()
    mode = TypeAdapter[Mode](Mode).validate_python(args.mode)
    print(json.dumps(evaluate(Path("/bench"), mode), ensure_ascii=True, allow_nan=False, indent=2))


if __name__ == "__main__":
    main()
