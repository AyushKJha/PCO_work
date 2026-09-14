"""Immutable, aggregateable trace snapshots."""

from dataclasses import dataclass
from typing import Iterable

from .cost_catalog import CostCatalogSnapshot


@dataclass(frozen=True)
class TraceEvent:
    id: str
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    latency_ms: float

    def __post_init__(self) -> None:
        if self.input_tokens < 0 or self.output_tokens < 0 or self.latency_ms < 0:
            raise ValueError("trace measurements cannot be negative")


@dataclass(frozen=True)
class TraceSnapshot:
    run_id: str
    events: tuple[TraceEvent, ...]

    def __init__(self, run_id: str, events: Iterable[TraceEvent] = ()) -> None:
        object.__setattr__(self, "run_id", run_id)
        object.__setattr__(self, "events", tuple(events))

    @property
    def total_tokens(self) -> int:
        return sum(e.input_tokens + e.output_tokens for e in self.events)

    @property
    def total_latency_ms(self) -> float:
        return sum(e.latency_ms for e in self.events)

    def cost(self, catalog: CostCatalogSnapshot) -> float:
        return sum(catalog.token_cost(e.provider, e.model, e.input_tokens, e.output_tokens) for e in self.events)
