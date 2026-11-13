"""Minimal packet parser/renderer and canned simulator, not a telemetry-history engine."""
from __future__ import annotations

import re
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from .tq_contract import Closed, Parameter, TQError, duration, timestamp


class Answer(Closed):
    id: int
    param: str
    op: str
    fields: dict[str, str]
    raw: str
    completeness: str | None
    freshness: str | None


def parse_answer(raw: str) -> Answer:
    tokens = raw.split()
    if len(tokens) < 5 or tokens[0] != "A" or not tokens[1].isdigit():
        raise TQError("Malformed answer header")
    fields = {}
    for token in tokens[4:]:
        if "=" not in token:
            raise TQError("Malformed answer field")
        key, value = token.split("=", 1)
        if key in fields:
            raise TQError("Duplicate answer field: " + key)
        fields[key] = value
    if not {"end","w","res","cat","lim","st"} <= fields.keys():
        raise TQError("Missing documented answer metadata")
    timestamp(fields["end"])
    if fields["w"] != "now":
        duration(fields["w"])
    if not fields["res"].isdigit():
        raise TQError("Invalid resolution")
    status = fields["st"]
    if status not in {"complete","partial","nodata","fresh","stale"}:
        raise TQError("Unknown st value")
    if status in {"fresh","stale"} and tokens[3] != "now":
        raise TQError("Fresh/stale st only documented for numeric now")
    if tokens[2].startswith("A.") and "ver" not in fields:
        raise TQError("App answer requires its version")
    for key in ("T","min","max","mean","v","peak","low","secs","secs_le","gap","age","scale"):
        if key in fields:
            try:
                numeric_value = Decimal(fields[key])
            except (ValueError, InvalidOperation) as exc:
                raise TQError("Malformed numeric answer field") from exc
            if not numeric_value.is_finite():
                raise TQError("Nonfinite numeric answer field")
    for key in ("n","N","hits","scored","gaps"):
        if key in fields and not re.fullmatch(r"[0-9]+",fields[key]):
            raise TQError("Malformed count field")
    return Answer(id=int(tokens[1]), param=tokens[2], op=tokens[3], fields=fields, raw=raw, completeness=status if status in {"complete","partial","nodata"} else None, freshness=status if status in {"fresh","stale"} else None)


def relative_time(answer: Answer, offset: str) -> str:
    if not re.fullmatch(r"-?[0-9]+", offset):
        raise TQError("Relative time is not an integer number of seconds")
    return (timestamp(answer.fields["end"])+timedelta(seconds=int(offset))).strftime("%Y-%m-%d %H:%M:%SZ")


def render_answer(answer: Answer, catalog: dict[str, Parameter]) -> str:
    param = catalog[answer.param]
    f = answer.fields
    unit = " " + param.unit if param.unit else ""
    if answer.completeness == "nodata":
        result = "No usable data; a zero must not be interpreted as absence of events."
    elif answer.op == "now":
        result = f"Latest {param.description}: {f.get('v','not supplied')}{unit}."
        if "age" in f:
            result += f" Reading age: {f['age']} seconds."
        result += f" Freshness: {answer.freshness or 'not separately documented'}; completeness: {answer.completeness or 'not supplied by st'}."
        if "asked" in f:
            result += " Requested marker (opaque documented field): " + f["asked"] + "."
    elif answer.op == "stats" and param.kind == "number":
        result = f"{param.description}: minimum {f.get('min','unknown')}{unit}, maximum {f.get('max','unknown')}{unit}, mean {f.get('mean','unknown')}{unit}; {f.get('n','unknown')} samples."
    elif answer.op in {"above","below"}:
        time = "at most " + f["secs_le"] if "secs_le" in f else f.get("secs","unknown")
        result = f"{param.description}: {time} seconds {answer.op} {f.get('T','unknown')}{unit}; reported hits: {f.get('hits','unknown')}."
        for key in ("peak","low"):
            if key in f:
                result += f" {key}: {f[key]}{unit}."
    elif answer.op == "last":
        result = f"Latest recorded {param.description}: " + (relative_time(answer, f["at"]) if "at" in f else "no timestamp supplied") + "."
        if "v" in f:
            result += " Recorded score: " + f["v"] + "."
        if "at" in f and abs(int(f["at"])) > duration(f["w"]):
            result += " This event precedes the requested window; last is documented to search before it."
    elif answer.op in {"count","stats"}:
        n = f.get("n", "unknown")
        result = ("No events were observed" if n == "0" else f"{n} {param.description} events observed") + "."
        for key in ("scored","min","max","mean"):
            if key in f:
                result += f" {key} score: {f[key]}."
    elif answer.op == "transitions":
        result = f"{param.description}: start={f.get('start','unknown')}."
        for field in ("seen","tr"):
            if field in f:
                for item in f[field].split(","):
                    offset, state = item.split(":", 1)
                    result += f" {field}: {state} at {relative_time(answer,offset)}."
    elif answer.op == "series":
        scale = Decimal(f["scale"])
        values = ["missing" if item == "-" else str(Decimal(item)*scale) for item in f["s"].split(",")]
        result = f"{param.description}: {f['N']} bins, scaled values [{', '.join(values)}]{unit}; '-' means missing, never zero."
    elif answer.op == "list":
        result = f"Tracked applications ({f.get('n','unknown')}): {f.get('apps','not supplied')}."
    else:
        raise TQError("No documented renderer for this operation")
    if answer.completeness:
        result += " Data completeness: " + answer.completeness + "."
    if answer.completeness == "partial":
        result += " Unobserved periods prevent a claim that nothing happened."
    for field, label in (("gap","Longest gap in seconds"),("gaps","Material gap count"),("res","Resolution in seconds"),("cat","Catalog version"),("lim","Limits version"),("ver","Application version")):
        if field in f:
            result += f" {label}: {f[field]}."
    if "from" in f:
        result += " Available history starts at " + relative_time(answer, f["from"]) + "."
    result += f" Source: tq packet {answer.id}, {answer.param}, end={f['end']}, window={f['w']}."
    return result
