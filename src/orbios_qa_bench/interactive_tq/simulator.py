"""A seeded, read-only fixture source for typed queries; no operational engine or I/O."""
from __future__ import annotations

import hashlib
import math
from collections.abc import Sequence
from decimal import Decimal
from typing import Literal

from ..tq_answers import parse_answer, render_answer
from ..tq_contract import (
    Closed,
    Parameter,
    Query,
    TQError,
    decimal_arg,
    duration,
    timestamp,
    validate,
)
from .contract import Manifest, Profile, Reading

Coverage = Literal["complete", "partial", "nodata"]
Interval = tuple[int, int]


class Scenario(Closed):
    id: str
    seed: int
    clock: str
    retention_days: int
    profiles: dict[str, Profile]
    gaps: dict[str, list[Interval]]
    numeric_milli: dict[str, dict[str, int]]
    states: dict[str, list[tuple[int, str]]]
    events_milli: dict[str, list[tuple[int, int | None]]]


def fmt(milli: int | Decimal) -> str:
    return format((Decimal(milli) / 1000).quantize(Decimal("0.001")).normalize(), "f")


def clipped(intervals: Sequence[Interval], start: int, end: int) -> list[Interval]:
    return [(max(start, a), min(end, b)) for a, b in intervals if b > start and a < end]


def coverage(intervals: Sequence[Interval], start: int, end: int) -> tuple[Coverage, list[int]]:
    current = start
    gaps = []
    for a, b in clipped(intervals, start, end):
        if a > current:
            gaps.append(a - current)
        current = b
    if current < end:
        gaps.append(end - current)
    return ("nodata" if current == start else "partial" if gaps else "complete"), gaps


class Simulator:
    def __init__(self, scenario: Scenario) -> None:
        self.scenario = scenario
        self.now = timestamp(scenario.clock)
        self.start = -scenario.retention_days * 86400

    def salt(self, mid: str) -> int:
        return int.from_bytes(hashlib.sha256(f"{self.scenario.seed}:{mid}".encode()).digest()[:4], "big")

    def intervals(self, mid: str, profile: Profile) -> list[Interval]:
        if profile == "no_data":
            return []
        holes = list(self.scenario.gaps.get(mid, []))
        if profile == "stale":
            holes.append((-90, 0))
        elif profile == "partial":
            holes.append((-1200, 0))
        current = self.start
        result = []
        for a, b in sorted(holes):
            if current < a:
                result.append((current, a))
            current = max(current, b)
        if current < 0:
            result.append((current, 0))
        return result

    def numeric(self, p: Parameter, end: int, salt: int) -> int:
        override = self.scenario.numeric_milli.get(p.id, {}).get(str(end))
        if override is not None:
            return override
        cycle = (end // (p.every_s or 10) + salt) % 49
        if p.id == "P001":
            return 53500 + cycle * 625
        if p.id == "P020":
            return 4500 + cycle * 13
        if p.id == "P021":
            return 6500 + cycle * 60
        if p.id == "P022":
            return 880 + cycle * 2
        if p.id.startswith("P01"):
            base = {"P010": 229000, "P011": 200, "P012": 49970, "P013": 28500,
                    "P014": 6200, "P015": 30000, "P016": 930}[p.id]
            return base + cycle
        if p.id.startswith("P1"):
            rails = [3700, 3300, 1800, 1100, 600, 1100, 800, 800, 800, 5000]
            voltage, current = rails[int(p.id[2])] + cycle, 100 + cycle * 8
            return (voltage, current, voltage * current // 1000)[int(p.id[3])]
        if "budget" in p.limits:
            return int(p.limits["budget"] * (800 + 9 * cycle))
        return 20000 + salt % 40000 + cycle * 100

    def state(self, mid: str) -> tuple[str, list[tuple[int, str]]]:
        role = mid.rsplit(".", 1)[-1]
        sequences = {
            "P002": ("normal", "warning", "critical", "normal"),
            "runnable": ("not_authorized", "runnable", "not_runnable", "runnable"),
            "job": ("none", "running", "exiting", "none"),
            "status": ("idle", "busy", "idle", "busy"),
            "sched": ("not_wanted", "waiting_for_cores", "wanted", "not_wanted"),
        }
        initial, a, b, c = sequences.get(role, ("idle", "active", "idle", "active"))
        return initial, self.scenario.states.get(mid, [(-5700, a), (-2400, b), (-450, c)])

    def read(self, q: Query, catalog: dict[str, Parameter], apps: dict[str, Manifest]) -> Reading:
        validate(q, catalog)
        p = catalog[q.param]
        when = timestamp(q.end) if q.end else self.now
        end = int((when - self.now).total_seconds())
        if not self.start < end <= 0:
            raise TQError("Requested end is outside the synthetic history.")
        app_id = p.id.split(".")[1] if p.id.startswith("A.") else None
        app = apps.get(app_id) if app_id else None
        spec = next((m for m in app.metrics if p.id == f"A.{app.id}.{m.id}"), None) if app else None
        profile = self.scenario.profiles.get(p.id, "complete") if spec is None else spec.profile
        intervals = self.intervals(p.id, profile)
        window = duration(q.window) if q.window != "now" else 0
        start = end - window
        cadence = p.every_s or 10
        res = cadence if window <= 3600 else 60 if window <= 86400 else 600
        salt = self.salt(p.id)
        quality, gaps = coverage(intervals, start, end)
        fields: dict[str, str] = {}
        status: str = quality
        if q.op == "list":
            quality = status = "complete"
            res, gaps = 0, []
            fields = {"n": str(len(apps)), "apps": ",".join(f"{a.id}:{a.version}" for a in sorted(apps.values(), key=lambda a: a.id)) or "-"}
        elif q.op == "now":
            eligible = [min(b, end) // cadence * cadence for a, b in intervals if min(b, end) // cadence * cadence > a]
            if eligible:
                latest = max(eligible)
                age = end - latest
                status = "stale" if age > 3 * cadence else "fresh"
                res = cadence
                fields = {"v": fmt(self.numeric(p, latest, salt)), "age": str(age), "asked": when.strftime("%Y%m%dT%H%M%SZ")}
            else:
                status = "nodata"
        elif p.kind == "number":
            # A sample ending at t represents the cadence slot (t-cadence, t].
            points = [(t, self.numeric(p, t, salt)) for a, b in clipped(intervals, start, end)
                      for t in range((a // cadence + 1) * cadence, b + 1, cadence)]
            if not points:
                quality = status = "nodata"
            else:
                fields = self.numeric_fields(q, points, start, end, cadence, res)
        elif p.kind == "state" and quality != "nodata":
            initial, changes = self.state(p.id)
            before = [value for t, value in changes if t <= start]
            start_seen = any(a <= start < b for a, b in intervals)
            fields["start"] = (before[-1] if before else initial) if start_seen else "unknown"
            observed = clipped(intervals, start, end)
            if not start_seen and observed:
                seen = observed[0][0]
                value = next((v for t, v in reversed(changes) if t <= seen), initial)
                fields["seen"] = f"{seen - end}:{value}"
            transitions = [f"{t - end}:{v}" for t, v in changes if start < t <= end and any(a < t <= b for a, b in observed)]
            if transitions:
                fields["tr"] = ",".join(transitions)
        elif p.kind == "event":
            fields, quality, gaps = self.event_fields(q, intervals, start, end, salt)
            status = quality

        # st remains wire-compatible. Numeric-now freshness and window coverage are separate in the demo.
        common = {"end": when.strftime("%Y%m%dT%H%M%SZ"), "w": q.window, "res": str(res),
                  "cat": "4", "lim": "2", "st": status, "gap": str(max(gaps, default=0)),
                  "gaps": str(len(gaps))}
        if app:
            common["ver"] = app.version
        if intervals and start < intervals[0][0] < end and q.op != "list":
            common["from"] = str(intervals[0][0] - end)
        raw = " ".join([f"A {q.id} {q.param} {q.op}", *(f"{k}={v}" for k, v in (common | fields).items())])
        packet = parse_answer(raw)
        source = f"Synthetic fixture {self.scenario.id} · seed {self.scenario.seed} · {p.id}"
        if q.op == "last" and "at" not in fields:
            text = f"No {p.description} event was observed in retained synthetic history. Observation coverage: {quality}. {source}."
            if quality != "complete":
                text += " Unobserved periods prevent a claim that nothing happened."
        else:
            text = render_answer(packet, catalog)
        if q.op == "now" and packet.freshness:
            text += f" Requested window coverage: {quality}."
        if q.op == "list" and not apps:
            text = "No fictional applications are registered."
        return Reading(packet=packet, text=text, source=source, completeness=quality)

    def numeric_fields(self, q: Query, points: list[tuple[int, int]], start: int, end: int, cadence: int, res: int) -> dict[str, str]:
        values = [v for _, v in points]
        if q.op == "stats":
            return {"min": fmt(min(values)), "max": fmt(max(values)), "mean": fmt(Decimal(sum(values)) / len(values)), "n": str(len(values))}
        if q.op == "series":
            n = int(q.arg or "24")
            groups: list[list[int]] = [[] for _ in range(n)]
            for t, v in points:
                groups[min(n - 1, ((t - start) * n - 1) // (end - start))].append(v)
            return {"N": str(n), "scale": "0.001", "s": ",".join(str(round(Decimal(sum(g)) / len(g))) if g else "-" for g in groups)}
        threshold = decimal_arg(q.arg or "") * 1000
        matches = [(t, v) for t, v in points if (v > threshold if q.op == "above" else v < threshold)]
        coarse = end - start > 3600
        if coarse:
            # Whole touched bins are an upper bound on qualifying observed slots, not unobserved time.
            buckets = {(t - start - 1) // res for t, _ in matches}
            # Include any preceding bin crossed by a sample's cadence interval.
            buckets.update(b for t, _ in matches for b in range(max(0, (t - cadence - start) // res), (t - start - 1) // res))
            secs = sum(min(res, end - start - b * res) for b in buckets)
        else:
            secs = sum(min(cadence, t - start) for t, _ in matches)
        fields = {"T": q.arg or "", "hits": str(len(matches)), "secs_le" if coarse else "secs": str(secs)}
        if matches:
            fields |= {"first": str(matches[0][0] - end), "last": str(matches[-1][0] - end),
                       "peak" if q.op == "above" else "low": fmt(max(v for _, v in matches) if q.op == "above" else min(v for _, v in matches))}
        return fields

    def event_fields(self, q: Query, intervals: list[Interval], start: int, end: int, salt: int) -> tuple[dict[str, str], Coverage, list[int]]:
        if q.param in self.scenario.events_milli:
            events = self.scenario.events_milli[q.param]
        else:
            phase = 180 + salt % 450
            events = [(t, 550 + (t // 7200 + salt) % 350 if t // 7200 % 3 else None)
                      for t in range(-phase - math.ceil(-self.start / 7200) * 7200, 0, 7200) if t > self.start]
        observed = [(t, v) for t, v in events if t <= end and any(a < t <= b for a, b in intervals)]
        if q.op == "last":
            last = observed[-1] if observed else None
            quality, gaps = coverage(intervals, min(start, last[0] - 1) if last else self.start, end)
            fields = {"at": str(last[0] - end)} if last else {}
            if last and last[1] is not None:
                fields["v"] = fmt(last[1])
            return fields, quality, gaps
        quality, gaps = coverage(intervals, start, end)
        selected = [(t, v) for t, v in observed if t > start]
        fields = {} if quality == "nodata" else {"n": str(len(selected))}
        if selected and q.op == "count":
            fields |= {"first": str(selected[0][0] - end), "last": str(selected[-1][0] - end)}
        if q.op == "stats" and quality != "nodata":
            scores = [v for _, v in selected if v is not None]
            fields["scored"] = str(len(scores))
            if scores:
                fields |= {"min": fmt(min(scores)), "max": fmt(max(scores)), "mean": fmt(Decimal(sum(scores)) / len(scores))}
        return fields, quality, gaps
