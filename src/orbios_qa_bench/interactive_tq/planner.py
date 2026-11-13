"""Explicit entities and typed slots, with optional semantic matching of unresolved clauses."""
from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from ..tq_contract import Parameter, Query, TQError, Translation, timestamp, validate
from ..tq_language import extract_time
from .contract import Candidate, Decision, Manifest, Mode

RAILS = ("3v7_wl_sw", "3v3_sys", "1v8_sys", "ddr_vdd2", "ddr_vddq", "1v1_sys", "0v8_sw", "vdd_core", "0v8_aon", "hdmi")
ROLE_ALIASES: dict[str, list[str]] = {
    "runnable": ["runnable", "eligible", "eligibility", "authorized", "allowed to run", "can run"],
    "job": ["running", "run state", "job", "pbs", "exiting", "suspended", "stopped"],
    "status": ["activity", "busy", "idle", "status", "doing"],
    "sched": ["schedule", "scheduling", "waiting", "queued for cores"],
    "turn_used_s": ["runtime", "run time", "execution time", "budget", "seconds used"],
    "alert": ["alert", "alerts", "alarm", "alarms"],
    "false_alert": ["false alert", "false alerts", "false alarm", "false alarms"],
    "detected": ["detection", "detections", "detected"],
}
FIXED_ALIASES: dict[str, list[str]] = {
    "P001": ["thermal warning", "thermal critical", "overheated", "how warm", "how hot", "processor temperature", "soc temperature", "cpu temperature"],
    "P002": ["escalation phase", "thermal phase", "brainstem phase", "phase history", "escalation"],
    "P020": ["ext5v", "input voltage", "supply voltage", "undervoltage", "under voltage"],
    "P021": ["board wattage", "board draw", "board consumption", "board power", "total board watts"],
    "P022": ["soc core voltage", "core voltage"],
    "APPS": ["which applications", "which apps", "list apps", "list applications", "what apps", "tracked applications"],
}
PDU_TERMS = (
    ("P016", "power factor"), ("P014", "reactive power"), ("P015", "apparent power"),
    ("P013", "active power|real power"), ("P012", "frequency|hertz|hz"),
    ("P011", "current|amps|amperes|amperage"), ("P010", "voltage|volts"),
)
Semantic = Callable[[str, list[str]], list[Candidate]]
Status = Literal["answer", "clarification_required", "unavailable", "out_of_scope"]
TimeSlot = tuple[str | None, str | None, list[str]]


def normalized(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", text.lower()))


def has(text: str, phrase: str) -> bool:
    return bool(phrase) and f" {phrase} " in f" {text} "


def match_aliases(text: str, aliases: dict[str, list[str]]) -> list[str]:
    matches: list[tuple[int, int, str]] = []
    for mid, labels in aliases.items():
        for label in labels:
            term = normalized(label)
            if term:
                matches.extend((m.start(), m.end(), mid) for m in re.finditer(r"\b" + re.escape(term) + r"\b", text))
    return sorted({mid for a, b, mid in matches if not any(c <= a and b <= d and d-c > b-a for c, d, _ in matches)})


def split_clauses(question: str) -> list[str]:
    text = re.sub(r"((?:between|from) \d{1,2}:\d{2}) and (\d{1,2}:\d{2})", r"\1 to \2", question, flags=re.I)
    text = re.sub(r"\b(min(?:imum)?|max(?:imum)?|lowest|highest) and (min(?:imum)?|max(?:imum)?|lowest|highest)\b", r"\1 /\2", text, flags=re.I)
    return [x.strip() for x in re.split(r"\s+(?:and|plus)\s+|;", text, flags=re.I) if x.strip()]


@dataclass
class Binding:
    pool: list[str]
    exact: list[str]
    reason: str = ""
    explicit: bool = False
    error: Status | None = None


class Planner:
    def __init__(self, catalog: dict[str, Parameter], apps: dict[str, Manifest], now: datetime, semantic: Semantic | None) -> None:
        self.catalog, self.apps, self.now, self.semantic = catalog, apps, now, semantic
        self.app_labels = {app.id: [normalized(app.id), normalized(app.label)] for app in apps.values()}
        for mid in catalog:
            if mid.startswith("A."):
                name = mid.split(".")[1]
                self.app_labels.setdefault(name, [normalized(name)])

    def app_names(self, text: str) -> list[str]:
        return match_aliases(normalized(text), self.app_labels)

    def bind(self, clause: str, shared_app: str | None) -> Binding:
        text = normalized(clause)
        explicit = re.findall(r"\b(?:P\d{3}|APPS|A\.[a-z0-9_]+\.[a-z0-9_]+)\b", clause, flags=re.I)
        if explicit:
            ids = sorted({x.upper() if not x.lower().startswith("a.") else "A." + x[2:].lower() for x in explicit})
            if len(ids) != 1:
                return Binding(ids, [], "Use one explicit metric ID per clause.", error="clarification_required")
            if ids[0] not in self.catalog:
                return Binding(ids, [], "That metric ID is absent from this catalogue.", error="unavailable")
            return Binding(ids, ids, "Explicit catalogue ID.", explicit=True)

        if re.search(r"\b(battery|disk|cpu clock|gps|downlink|latitude|longitude|fan speed|orbital velocity|moving|payload temperature)\b", text):
            return Binding([], [], "That capability is not recorded by this simulator.", error="unavailable")

        apps = self.app_names(clause)
        if len(apps) > 1:
            return Binding([], [], "Name one application per clause so its metric can be linked.", error="clarification_required")
        rails = [i for i, name in enumerate(RAILS) if has(text, normalized(name))]
        app = apps[0] if apps else shared_app
        if app and (apps or not rails and not re.search(r"\b(temperature|voltage|watts|board power|mains|pdu|thermal)\b", text)):
            pool = sorted(mid for mid in self.catalog if mid.startswith(f"A.{app}."))
            stripped = text
            for label in sorted(set(self.app_labels[app]), key=len, reverse=True):
                if label:
                    stripped = re.sub(r"\b" + re.escape(label) + r"\b", " ", stripped)
            stripped = " ".join(stripped.split())
            stripped = re.sub(r"\bcan\s+(?:it\s+)?(?:be\s+)?run\b", "can run", stripped)
            stripped = re.sub(r"\bwas\s+(?:it\s+)?allowed to start\b", "allowed to run", stripped)
            aliases: dict[str, list[str]] = {}
            for mid in pool:
                role = mid.rsplit(".", 1)[1]
                p = self.catalog[mid]
                aliases[mid] = [role, *ROLE_ALIASES.get(role, [])]
                for label in p.aliases:
                    cleaned = normalized(label)
                    for app_label in self.app_labels[app]:
                        if app_label:
                            cleaned = re.sub(r"\b" + re.escape(app_label) + r"\b", " ", cleaned)
                    if cleaned.strip():
                        aliases[mid].append(cleaned.strip())
            if clause.lower().lstrip().startswith("why ") and "running" in stripped:
                required = [f"A.{app}.runnable", f"A.{app}.sched"]
                if all(mid in pool for mid in required):
                    return Binding(required, required, "Eligibility and scheduling evidence only.", explicit=True)
            found = match_aliases(stripped, aliases)
            if len(found) > 1:
                return Binding(found, [], "Several application metrics fit. Specify one per clause.", error="clarification_required")
            return Binding(pool, found, "Application entity linked before matching its metric.")
        if re.search(r"\b(?:for|from)\s+(?:the\s+)?(?:app|application)\b", text):
            return Binding([], [], "That application is not declared in the fictional catalogue.", error="unavailable")
        if rails:
            if len(rails) != 1:
                return Binding([], [], "Specify one rail per clause.", error="clarification_required")
            quantities = [i for i, pattern in enumerate((r"voltage|volts", r"current(?!\s+(?:reading|value|level|voltage|power))|amps|amperes|amperage", r"power|watts|wattage|drawing")) if re.search(r"\b(?:" + pattern + r")\b", text)]
            if len(quantities) > 1:
                return Binding([], [], "Specify voltage, current or power per rail clause.", error="clarification_required")
            mid = f"P1{rails[0]}{quantities[0] if quantities else 2}"
            return Binding([mid], [mid], "Named rail and quantity; watts if quantity is omitted.")
        if re.search(r"\b(pdu|mains)\b", text):
            pool = [mid for mid, _ in PDU_TERMS]
            quantity_text = text.replace("current voltage", "voltage").replace("current frequency", "frequency")
            ids = [mid for mid, terms in PDU_TERMS if re.search(r"\b(?:" + terms + r")\b", quantity_text)]
            if len(ids) > 1 or not ids and re.search(r"\bpower\b", text):
                return Binding(pool, [], "Specify the PDU quantity, such as active power or power factor.", error="clarification_required")
            return Binding(pool, ids, "Bench PDU linked independently of board sensors.")

        aliases = {
            mid: [*p.aliases, *FIXED_ALIASES.get(mid, [])]
            for mid, p in self.catalog.items() if mid in FIXED_ALIASES
        }
        aliases["P020"] = [label for label in aliases["P020"] if label != "supply"]
        aliases["P021"] = [label for label in aliases["P021"] if label != "consumption"]
        ids = match_aliases(text, aliases)
        if len(ids) > 1:
            return Binding(ids, [], "Multiple readings share this clause; give each its own operation.", error="clarification_required")
        if not ids and re.search(r"\b(power|voltage|watts|volts|wattage|amperage|amps|current)\b", text) and not re.search(r"\bcurrent (?:temperature|reading|state)\b", text):
            pool = [mid for mid, p in self.catalog.items() if not mid.startswith("A.") and p.unit in {"W", "V", "A", "VA", "VAR"}]
            # A bare electrical quantity has several documented scopes.
            if re.fullmatch(r"(?:what (?:is|was) (?:the )?|show (?:me )?(?:the )?|how much )?(?:current |latest |average )?(?:power|voltage|current|watts|volts|amps|wattage|amperage)(?: right now| now| today)?", text):
                return Binding(pool, [], "Specify board, rail or PDU and the quantity.", error="clarification_required")
        return Binding([mid for mid in self.catalog if not mid.startswith("A.")], ids, "Documented sensor alias.")

    def time_slot(self, clause: str) -> TimeSlot:
        text = re.sub(r"\b(?:last|past) half (?:an )?hour\b", "last 30 minutes", clause, flags=re.I)
        text = re.sub(r"\b(?:last|past) quarter (?:of an )?hour\b", "last 15 minutes", text, flags=re.I)
        text = re.sub(r"\b(?:previous|preceding) (?=\d|hour|day|week)", "last ", text, flags=re.I)
        text = re.sub(r"\b(last|past) minute\b", r"\1 1 minute", text, flags=re.I)
        if re.search(r"\b(?:since|ago|last (?:monday|tuesday|wednesday|thursday|friday|saturday|sunday))\b", text, re.I):
            raise TQError("Use a supported relative window or an explicit UTC ending time.")
        return extract_time(text, self.now)

    def query(self, clause: str, mid: str, slot: TimeSlot, inherited_stats: bool) -> tuple[Query, list[str]]:
        p = self.catalog[mid]
        text = clause.lower()
        norm = normalized(text)
        window, end, assumptions = slot
        notes = list(assumptions)
        now = bool(re.search(r"\b(now|currently|current|latest|present|at this instant|at the moment|right away)\b", norm))
        # "current" is an electrical quantity when measured on a named rail or PDU.
        if p.unit == "A" and not re.search(r"\b(now|currently|latest|present|at this instant|at the moment)\b", norm):
            now = False
        bins = re.search(r"\b(\d+)\s+(?:points?|bins?|samples?)\b", text)
        threshold = re.search(r"\b(above|below|under|over|exceeded|exceeding|higher than|lower than)\s*(-?(?:\d+(?:\.\d+)?|\.\d+))(?![\d.])", text)
        if threshold and threshold[1] == "over" and re.match(r"\s*(?:seconds?|minutes?|hours?|days?|[mhd])\b", text[threshold.end():]):
            threshold = None
        if re.search(r"(?:>=|<=|\bat least\b|\bat most\b|\bno (?:more|less) than\b)", text):
            raise TQError("Inclusive thresholds are not defined. Use strictly above or below.")
        if len(re.findall(r"\b(?:above|below|higher than|lower than)\s*-?\d", text)) > 1:
            raise TQError("Specify one threshold per clause.")
        stats = bool(re.search(r"\b(mean|averages?|averaged|minimum|maximum|min|max|highest|lowest|peak|range|stats|statistics)\b", norm)) or inherited_stats
        series = bool(bins or re.search(r"\b(trend|series|curve|plot|graph|profile)\b", norm))
        arg = None
        if p.kind == "apps":
            if window or end:
                raise TQError("Only the current application list is documented.")
            op, window = "list", "now"
        elif p.kind == "state":
            if threshold or series or stats:
                raise TQError("State metrics support transitions only.")
            op = "transitions"
            if now or re.match(r"(?:is|are|can|why)\b", norm):
                window = window or "1h"
        elif p.kind == "event":
            if threshold or series:
                raise TQError("Event queries support count, score statistics and last occurrence.")
            latest = re.search(r"\b(latest|most recent|last (?:alert|event|detection)|when .* last)\b", norm)
            op = "last" if latest else "stats" if stats or re.search(r"\b(scores?|confidence)\b", norm) else "count"
            if latest and not window:
                window = "30d"
                notes.append("Last occurrence searches retained history up to the selected end.")
        else:
            if re.search(r"\b(events?|alerts?|detections?|transitions?)\b", norm):
                raise TQError("This numeric parameter does not record events or transitions.")
            if sum((bool(threshold), series, stats)) > 1:
                raise TQError("Specify one operation for this metric per clause.")
            if threshold:
                op = "below" if threshold[1] in {"below", "under", "lower than"} else "above"
                arg = threshold[2]
                unit = re.match(r"\s*(?:degrees?\s+)?(°?[cf]\b|celsius\b|fahrenheit\b|volts?\b|v\b|watts?\b|w\b|amps?\b|amperes?\b|a\b|seconds?\b|s\b|%\b|percent\b)", text[threshold.end():])
                if unit:
                    supplied = unit[1]
                    expected = {"c":"°C", "°c":"°C", "celsius":"°C", "f":"°F", "°f":"°F", "fahrenheit":"°F",
                                "volts":"V", "volt":"V", "v":"V", "watts":"W", "watt":"W", "w":"W",
                                "amps":"A", "amp":"A", "amperes":"A", "ampere":"A", "a":"A", "seconds":"s",
                                "second":"s", "s":"s", "percent":"%", "%":"%"}[supplied]
                    if expected != p.unit:
                        raise TQError(f"Threshold unit {expected} does not match {p.unit or 'the unitless metric'}.")
            elif "budget" in norm and re.search(r"\b(over|above|exceed(?:ed|ing)?)\b", norm):
                op, arg = "above", self.limit(p, "budget")
            elif mid == "P001" and re.search(r"\b(warning|critical|overheated)\b", norm):
                op, arg = "above", self.limit(p, "critical" if "critical" in norm else "warning")
            elif mid == "P020" and re.search(r"\b(under voltage|undervoltage)\b", norm):
                op, arg = "below", self.limit(p, "low")
            elif series:
                op, arg = "series", bins[1] if bins else "24"
            elif now and not stats:
                op, window = "now", window or "1m"
            else:
                op = "stats"
        if window is None:
            window = "24h"
            notes.append("No time window supplied; using 24h.")
        if end and (timestamp(end) > self.now or (self.now - timestamp(end)).total_seconds() >= 30 * 86400):
            raise TQError("Requested end is outside the synthetic history.")
        q = Query.model_validate({"id": 1, "param": mid, "op": op, "window": window, "arg": arg, "end": end})
        validate(q, self.catalog)
        return q, notes

    @staticmethod
    def limit(parameter: Parameter, name: str) -> str:
        if name not in parameter.limits:
            raise TQError(f"No {name} limit is recorded for this metric.")
        return str(parameter.limits[name]).removesuffix(".0")

    def plan(self, question: str, mode: Mode) -> tuple[Translation, list[str], list[Decision], int]:
        selected: list[str] = []
        decisions: list[Decision] = []
        calls = 0

        def stop(status: Status, reason: str) -> tuple[Translation, list[str], list[Decision], int]:
            return Translation(status=status, explanation=reason), sorted(set(selected)), decisions, calls

        norm = normalized(question)
        if (re.search(r"\b(restart|reboot|delete|execute|freeze|revoke|shutdown|power cycle|ssh|ping|deploy|synchronize)\b", norm)
                or re.match(r"(?:(?:please|can you|could you) )?(?:stop|start|run|set|authorize|suspend|turn off|turn on)\b", norm)):
            return stop("out_of_scope", "This demo accepts read-only telemetry questions.")
        if re.search(r"\b(tomorrow|next week|correlat\w*|predict\w*|forecast|rate of change)\b", norm):
            return stop("unavailable", "The synthetic history cannot answer future, derivative or correlation queries.")
        if re.search(r"\b(?:when|while|whenever)\s+(?:the\s+)?(?:temperature|voltage|power)\b", norm) and re.search(r"\b(?:above|below|exceeded|under)\b", norm):
            return stop("unavailable", "Conditional joins across parameters are not supported; request two separate readings.")
        all_apps = self.app_names(question)
        if norm.startswith("why ") and not (all_apps and "running" in norm):
            return stop("unavailable", "Telemetry can provide observations, not a causal explanation.")
        known_underscores = set(RAILS) | set(self.app_labels) | {mid.rsplit(".", 1)[-1] for mid in self.catalog if mid.startswith("A.")}
        if any(word.lower() not in known_underscores for word in re.findall(r"\b[a-zA-Z][a-zA-Z0-9]*(?:_[a-zA-Z0-9]+)+\b", question)):
            return stop("unavailable", "An application or metric identifier is absent from the catalogue.")
        parts = split_clauses(question)
        if not parts or len(parts) > 6:
            return stop("clarification_required", "Ask for up to six telemetry clauses at a time.")
        try:
            slots = [self.time_slot(part) for part in parts]
        except ValueError as exc:
            return stop("unavailable" if "30d" in str(exc) else "clarification_required", str(exc))
        provided = [slot for slot in slots if slot[0] or slot[1]]
        shared = slots[-1] if len(provided) == 1 and provided[0] == slots[-1] else (None, None, [])
        inherited_stats = bool(re.match(r"(?:what (?:were|was|are) (?:the )?|show (?:the )?)?(?:average|mean)\b", norm))
        queries: list[Query] = []
        assumptions: list[str] = []

        for part, local in zip(parts, slots, strict=True):
            binding = self.bind(part, all_apps[0] if len(all_apps) == 1 else None)
            if binding.error:
                decisions.append(Decision(clause=part, method="guard", reason=binding.reason, candidates=[Candidate(id=x) for x in binding.pool[:8]]))
                return stop(binding.error, binding.reason)
            unavailable = [mid for mid in binding.exact if not self.catalog[mid].registered]
            if unavailable:
                selected.extend(unavailable)
                reason = "The capability is declared but unavailable in this simulation."
                decisions.append(Decision(clause=part, method="guard", reason=reason, candidates=[Candidate(id=x) for x in unavailable]))
                return stop("unavailable", reason)
            if binding.exact and (mode != "minilm" or binding.explicit):
                ids = binding.exact
                decisions.append(Decision(clause=part, method="symbolic", reason=binding.reason, candidates=[Candidate(id=x) for x in ids]))
            elif mode != "symbolic" and self.semantic:
                pool = [mid for mid in binding.pool if self.catalog[mid].registered]
                # Compare only compatible kinds when the operation is explicit.
                if re.search(r"\b(how many|count|most recent (?:event|alert|detection))\b", normalized(part)):
                    pool = [mid for mid in pool if self.catalog[mid].kind == "event"]
                elif re.search(r"\b(above|below|bins|points|trend|graph)\b", normalized(part)):
                    pool = [mid for mid in pool if self.catalog[mid].kind == "number"]
                ranked = self.semantic(part, pool) if pool else []
                calls += bool(pool)
                top = ranked[0] if ranked else None
                if top is None or top.score is None or top.score < 0.45:
                    reason = "No semantic candidate reached the similarity floor; name the metric explicitly."
                    decisions.append(Decision(clause=part, method="semantic", reason=reason, candidates=ranked[:5]))
                    return stop("unavailable", reason)
                if len(ranked) > 1 and ranked[1].score is not None and top.score - ranked[1].score < 0.035:
                    reason = "Several semantic candidates are close. Specify the metric or use a catalogue ID."
                    decisions.append(Decision(clause=part, method="semantic", reason=reason, candidates=ranked[:5]))
                    return stop("clarification_required", reason)
                ids = [top.id]
                decisions.append(Decision(clause=part, method="semantic", reason="Candidate passed the similarity and separation checks.", candidates=ranked[:5]))
            else:
                reason = "No documented metric was resolved for this clause. Name a catalogue metric."
                decisions.append(Decision(clause=part, method="symbolic", reason=reason))
                return stop("unavailable", reason)
            selected.extend(ids)
            for mid in ids:
                try:
                    q, notes = self.query(part, mid, local if local[0] or local[1] else shared, inherited_stats)
                except (ValueError, KeyError) as exc:
                    reason = str(exc)
                    return stop("unavailable" if "outside the synthetic history" in reason else "clarification_required", reason)
                queries.append(q)
                assumptions.extend(notes)
        if norm.startswith("why "):
            assumptions.append("Eligibility and scheduling states provide evidence, not a causal explanation.")
        unique = {(q.param, q.op, q.window, q.arg, q.end): q for q in queries}
        ordered = sorted(unique.values(), key=lambda q: (q.param, q.op, q.window, q.arg or "", q.end or ""))
        if len(ordered) > 6:
            return stop("clarification_required", "Narrow this question to at most six distinct queries.")
        ordered = [q.model_copy(update={"id": i}) for i, q in enumerate(ordered, 1)]
        return Translation(status="answer", queries=ordered, assumptions=list(dict.fromkeys(assumptions))), sorted(set(selected)), decisions, calls
