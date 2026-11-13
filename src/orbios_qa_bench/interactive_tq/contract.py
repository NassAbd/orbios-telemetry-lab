"""Typed boundaries for the local demo. Only data, never executable manifest fields."""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, StringConstraints, TypeAdapter, model_validator

from ..tq_answers import Answer
from ..tq_contract import Closed, Translation

Mode = Literal["symbolic", "minilm", "hybrid"]
Slug = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$", max_length=32)]
Label = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=96)]
Version = Annotated[str, StringConstraints(pattern=r"^[0-9][a-zA-Z0-9.-]{0,23}$")]
Profile = Literal["complete", "partial", "stale", "no_data"]


class MetricSpec(Closed):
    id: Slug
    label: Label
    aliases: list[Label] = Field(default_factory=list, max_length=8)
    kind: Literal["number", "state", "event"]
    unit: Annotated[str, StringConstraints(pattern=r"^[a-zA-Z0-9°%/]{1,16}$")] | None = None
    registered: bool = True
    profile: Profile = "complete"

    @model_validator(mode="after")
    def compatible_fields(self) -> MetricSpec:
        if self.kind != "number" and (self.unit or self.profile == "stale"):
            raise ValueError("Only numeric metrics take units or a stale profile.")
        return self


class Manifest(Closed):
    id: Slug
    label: Label
    version: Version
    budget_s: int = Field(ge=1, le=86400)
    metrics: list[MetricSpec] = Field(min_length=1, max_length=12)

    @model_validator(mode="after")
    def unique_metrics(self) -> Manifest:
        if len({metric.id for metric in self.metrics}) != len(self.metrics):
            raise ValueError("Metric IDs must be unique within an application.")
        for metric in self.metrics:
            if metric.id == "turn_used_s" and (metric.kind != "number" or metric.unit != "s"):
                raise ValueError("turn_used_s must be a number in seconds.")
        return self


class Ask(Closed):
    action: Literal["ask"]
    question: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=600)]
    mode: Mode = "hybrid"


class Inspect(Closed):
    action: Literal["inspect"]


class Save(Closed):
    action: Literal["save"]
    manifest: Manifest


class Remove(Closed):
    action: Literal["remove"]
    id: Slug


Request = Annotated[Ask | Inspect | Save | Remove, Field(discriminator="action")]
REQUEST = TypeAdapter[Request](Request)


class Candidate(Closed):
    id: str
    score: float | None = None


class Decision(Closed):
    clause: str
    method: Literal["symbolic", "semantic", "guard"]
    reason: str
    candidates: list[Candidate] = Field(default_factory=list)


class Reading(Closed):
    packet: Answer
    text: str
    source: str
    completeness: Literal["complete", "partial", "nodata"]


class Reply(Closed):
    mode: Mode
    translation: Translation
    lines: list[str]
    selected: list[str]
    decisions: list[Decision]
    readings: list[Reading]
    semantic_calls: int
    elapsed_ms: float
