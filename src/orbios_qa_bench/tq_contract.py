"""Document-derived tq grammar. This module never launches a command or performs I/O."""
from __future__ import annotations

import re
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

OPS = {"number": {"now", "stats", "above", "below", "series"}, "state": {"transitions"}, "event": {"count", "stats", "last"}, "apps": {"list"}}


class TQError(ValueError):
    pass


class Closed(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Parameter(Closed):
    id: str
    description: str
    aliases: list[str]
    kind: Literal["number", "state", "event", "apps"]
    unit: str | None = None
    every_s: int | None = None
    limits: dict[str, float] = {}
    registered: bool = True
    provenance: str


class Query(Closed):
    id: int = Field(ge=1)
    param: str
    window: str
    op: Literal["now", "stats", "above", "below", "series", "transitions", "count", "last", "list"]
    arg: str | None = None
    end: str | None = None


class Translation(Closed):
    status: Literal["answer", "clarification_required", "unavailable", "out_of_scope"]
    queries: list[Query] = []
    assumptions: list[str] = []
    explanation: str = ""


def duration(window: str) -> int:
    match = re.fullmatch(r"([1-9][0-9]*)([smhd])", window)
    if not match:
        raise TQError("A positive integer s/m/h/d window is required")
    seconds = int(match[1]) * {"s": 1, "m": 60, "h": 3600, "d": 86400}[match[2]]
    if seconds > 30*86400:
        raise TQError("Window exceeds 30d")
    return seconds


def timestamp(value: str) -> datetime:
    if not re.fullmatch(r"[0-9]{8}T[0-9]{6}Z", value):
        raise TQError("UTC compact timestamp required")
    try:
        return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
    except ValueError as exc:
        raise TQError("Invalid UTC date") from exc


def decimal_arg(value: str) -> Decimal:
    try:
        number = Decimal(value)
    except InvalidOperation as exc:
        raise TQError("Numeric argument required") from exc
    if not number.is_finite() or not re.fullmatch(r"-?(?:[0-9]+(?:\.[0-9]+)?|\.[0-9]+)", value):
        raise TQError("Finite decimal argument required")
    return number


def validate(query: Query, catalog: dict[str, Parameter]) -> None:
    if query.param not in catalog or not catalog[query.param].registered:
        raise TQError("Parameter is not registered in this catalog")
    param = catalog[query.param]
    if query.op not in OPS[param.kind]:
        raise TQError("Operation is not allowed for parameter kind")
    if query.end:
        timestamp(query.end)
    if param.kind == "apps":
        if query.window != "now" or query.end is not None or query.arg is not None:
            raise TQError("Only documented APPS now list form is accepted")
        return
    duration(query.window)
    if query.op in {"above", "below"}:
        if query.arg is None:
            raise TQError("Threshold is required")
        decimal_arg(query.arg)
    elif query.op == "series":
        if query.arg is not None and (not re.fullmatch(r"[0-9]+", query.arg) or not 1 <= int(query.arg) <= 60):
            raise TQError("Series N must be 1..60")
    elif query.arg is not None:
        raise TQError("Unexpected argument")


def compile_query(query: Query, catalog: dict[str, Parameter]) -> str:
    validate(query, catalog)
    parts = ["Q", str(query.id), query.param, query.window, query.op]
    if query.arg is not None:
        parts.append(query.arg)
    if query.end:
        parts.append("end=" + query.end)
    return " ".join(parts)


def parse_query(line: str, catalog: dict[str, Parameter]) -> Query:
    parts = line.split()
    if len(parts) < 5 or parts[0] != "Q" or not re.fullmatch(r"[1-9][0-9]*", parts[1]):
        raise TQError("Expected Q <id> <param> <window> <op> [arg] [end=...]")
    tail = parts[5:]
    end = tail.pop()[4:] if tail and tail[-1].startswith("end=") else None
    if len(tail) > 1:
        raise TQError("Unexpected query tokens")
    query = Query.model_validate({"id": int(parts[1]), "param": parts[2], "window": parts[3], "op": parts[4], "arg": tail[0] if tail else None, "end": end})
    validate(query, catalog)
    return query


def compile_translation(value: Translation, catalog: dict[str, Parameter]) -> list[str]:
    if value.status != "answer":
        if value.queries:
            raise TQError("Abstention cannot contain queries")
        return []
    if not value.queries or len({q.id for q in value.queries}) != len(value.queries):
        raise TQError("Answer requires uniquely numbered queries")
    return [compile_query(query, catalog) for query in value.queries]
