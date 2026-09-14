"""Deterministic staged optimizer for candidate agent configurations."""

from dataclasses import dataclass, field
import math
from numbers import Real
from typing import Callable, Iterable, Any

def _metric(value: Any, name: str, *, minimum: float | None = None, maximum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, Real): raise ValueError(f"candidate {name} must be a finite number")
    numeric = float(value)
    if not math.isfinite(numeric): raise ValueError(f"candidate {name} must be finite")
    if minimum is not None and numeric < minimum: raise ValueError(f"candidate {name} cannot be less than {minimum}")
    if maximum is not None and numeric > maximum: raise ValueError(f"candidate {name} cannot be greater than {maximum}")
    return numeric

@dataclass(frozen=True)
class Candidate:
    id: str
    cost_usd: float
    latency_ms: float
    quality: float
    config: dict[str, Any] = field(default_factory=dict)
    def __post_init__(self) -> None:
        if not self.id: raise ValueError("candidate id is required")
        _metric(self.cost_usd, "cost_usd", minimum=0); _metric(self.latency_ms, "latency_ms", minimum=0); _metric(self.quality, "quality", minimum=0, maximum=1)
        if not isinstance(self.config, dict): raise ValueError("candidate config must be a mapping")

@dataclass(frozen=True)
class OptimizationStage:
    name: str
    candidate_ids: tuple[str, ...]
    budget: int

@dataclass(frozen=True)
class OptimizationResult:
    recommended: Candidate
    stages: tuple[OptimizationStage, ...]

class StagedOptimizer:
    def __init__(self, evaluate: Callable[[Candidate, int], float] | None = None) -> None:
        self.evaluate = evaluate or (lambda candidate, budget: candidate.quality)
    @staticmethod
    def _rank(candidates: Iterable[Candidate], scores: dict[str, float]) -> list[Candidate]:
        return sorted(candidates, key=lambda c: (-scores[c.id], c.cost_usd, c.latency_ms, c.id))
    def optimize(self, candidates: Iterable[Candidate], beam_width: int = 3, halving_rounds: int = 2, initial_budget: int = 1, *, baseline_quality: float | None = None, max_quality_regression: float = 0.0, max_latency_ms: float | None = None, max_cost_usd: float | None = None, max_spend_usd: float | None = None) -> OptimizationResult:
        pool=list(candidates)
        if not pool: raise ValueError("at least one candidate is required")
        if len({c.id for c in pool}) != len(pool): raise ValueError("candidate ids must be unique")
        if any(isinstance(v, bool) or not isinstance(v, int) or v < 1 for v in (beam_width, halving_rounds, initial_budget)): raise ValueError("beam width, rounds, and budget must be positive integers")
        if baseline_quality is not None: _metric(baseline_quality, "baseline quality", minimum=0, maximum=1)
        _metric(max_quality_regression, "quality regression", minimum=0, maximum=1)
        if max_spend_usd is not None and max_cost_usd is not None and max_spend_usd != max_cost_usd: raise ValueError("max_cost_usd and max_spend_usd disagree")
        spend_limit=max_cost_usd if max_spend_usd is None else max_spend_usd
        if max_latency_ms is not None: _metric(max_latency_ms, "latency limit", minimum=0)
        if spend_limit is not None: _metric(spend_limit, "spend limit", minimum=0)
        threshold=None if baseline_quality is None else baseline_quality-max_quality_regression
        pool=[c for c in pool if (threshold is None or c.quality >= threshold) and (max_latency_ms is None or c.latency_ms <= max_latency_ms) and (spend_limit is None or c.cost_usd <= spend_limit)]
        if not pool: raise ValueError("no candidates satisfy the optimization constraints")
        stages=[]; scores={c.id:self._score(self.evaluate(c, initial_budget)) for c in pool}; ordered=self._rank(pool,scores)
        stages.append(OptimizationStage("sensitivity",tuple(c.id for c in ordered),initial_budget)); beam=ordered[:beam_width]; stages.append(OptimizationStage("beam",tuple(c.id for c in beam),initial_budget)); budget=initial_budget
        for _ in range(halving_rounds):
            budget*=2; scores={c.id:self._score(self.evaluate(c,budget)) for c in beam}; keep=max(1,(len(beam)+1)//2); beam=self._rank(beam,scores)[:keep]; stages.append(OptimizationStage("successive_halving",tuple(c.id for c in beam),budget))
        recommended=self._rank(beam,{c.id:self._score(self.evaluate(c,budget)) for c in beam})[0]
        return OptimizationResult(recommended,tuple(stages))
    @staticmethod
    def _score(value: Any) -> float: return _metric(value,"evaluation score",minimum=0,maximum=1)
