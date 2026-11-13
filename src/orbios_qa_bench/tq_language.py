"""Shared English operation/time extraction; deliberately no parameter-value generation."""
from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from .tq_contract import TQError, duration


def compact(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def window_from_seconds(seconds: int) -> str:
    if seconds <= 0:
        raise TQError("A positive time interval is required")
    for unit, factor in (("d",86400),("h",3600),("m",60),("s",1)):
        if seconds % factor == 0:
            result = str(seconds//factor)+unit
            duration(result)
            return result
    raise TQError("Invalid interval")


def local_time(value: datetime, zone: ZoneInfo) -> datetime:
    alternatives = {compact(value.replace(tzinfo=zone, fold=fold)) for fold in (0,1)}
    if len(alternatives) != 1 or value.replace(tzinfo=zone).astimezone(UTC).astimezone(zone).replace(tzinfo=None) != value:
        raise TQError("Ambiguous/nonexistent local time; provide explicit UTC")
    return value.replace(tzinfo=zone)


def extract_time(question: str, now: datetime, timezone: str = "UTC") -> tuple[str | None, str | None, list[str]]:
    text = question.lower()
    assumptions: list[str] = []
    timezone = "UTC" if re.search(r"\butc\b", text) else timezone
    zone = ZoneInfo(timezone)
    if "yesterday" in text:
        day = now.astimezone(zone).date()-timedelta(days=1)
        end_local = local_time(datetime.combine(day, datetime.max.time()).replace(microsecond=0), zone)
        assumptions.append(f"Yesterday interpreted in {timezone}; end converted to UTC.")
        return "24h", compact(end_local), assumptions
    interval = re.search(r"(?:between|from) (\d{1,2}:\d{2}) (?:and|to) (\d{1,2}:\d{2})", text)
    if interval:
        date_match = re.search(r"on (\d{4}-\d{2}-\d{2})", text)
        date = datetime.strptime(date_match[1], "%Y-%m-%d").date() if date_match else now.astimezone(zone).date()
        start = local_time(datetime.combine(date, datetime.strptime(interval[1], "%H:%M").time()), zone)
        interval_end = local_time(datetime.combine(date, datetime.strptime(interval[2], "%H:%M").time()), zone)
        assumptions.append(f"Clock interval interpreted in {timezone} and converted to UTC.")
        return window_from_seconds(int((interval_end.astimezone(UTC)-start.astimezone(UTC)).total_seconds())), compact(interval_end), assumptions
    absolute = re.search(r"ending (\d{4}-\d{2}-\d{2}t\d{2}:\d{2}:\d{2}z)", text)
    end = compact(datetime.fromisoformat(absolute[1].upper().replace("Z", "+00:00"))) if absolute else None
    match = re.search(r"(?:last|past|over|for) (\d+)\s*(seconds?|minutes?|hours?|days?|[smhd])\b", text)
    if match:
        window = match[1]+match[2][0]
        duration(window)
        return window, end, assumptions
    for phrase, window in (("last hour","1h"),("past hour","1h"),("today","24h"),("last day","24h"),("this week","7d"),("last week","7d")):
        if phrase in text:
            return window, end, assumptions
    return None, end, assumptions
