"""Versioned provider/model pricing used by profiling and optimization.

Rates are expressed in USD per one million tokens.  A snapshot freezes rates
for a run so a later catalog update cannot change historical measurements.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Iterable, Mapping


@dataclass(frozen=True)
class CostRate:
    provider: str
    model: str
    input_usd_per_million: float
    output_usd_per_million: float
    effective_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.provider or not self.model:
            raise ValueError("provider and model are required")
        if self.input_usd_per_million < 0 or self.output_usd_per_million < 0:
            raise ValueError("cost rates cannot be negative")


@dataclass(frozen=True)
class CostCatalogSnapshot:
    version: str
    rates: Mapping[tuple[str, str], CostRate]
    created_at: datetime

    def rate_for(self, provider: str, model: str) -> CostRate:
        try:
            return self.rates[(provider, model)]
        except KeyError as exc:
            raise KeyError(f"no cost rate for {provider}/{model}") from exc

    def token_cost(self, provider: str, model: str, input_tokens: int, output_tokens: int) -> float:
        if input_tokens < 0 or output_tokens < 0:
            raise ValueError("token counts cannot be negative")
        rate = self.rate_for(provider, model)
        return (input_tokens * rate.input_usd_per_million + output_tokens * rate.output_usd_per_million) / 1_000_000

    # Alias useful to callers that use "estimate" terminology.
    estimate = token_cost


class CostCatalog:
    def __init__(self, rates: Iterable[CostRate] = ()) -> None:
        self._rates: dict[tuple[str, str], CostRate] = {}
        for rate in rates:
            self.add(rate)

    def add(self, rate: CostRate) -> None:
        self._rates[(rate.provider, rate.model)] = rate

    def get(self, provider: str, model: str) -> CostRate:
        return self.snapshot("live").rate_for(provider, model)

    def snapshot(self, version: str | None = None) -> CostCatalogSnapshot:
        if not version:
            version = datetime.now(timezone.utc).isoformat()
        return CostCatalogSnapshot(version, MappingProxyType(dict(self._rates)), datetime.now(timezone.utc))
