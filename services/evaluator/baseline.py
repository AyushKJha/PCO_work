"""Baseline evaluation and aggregate metrics."""
from dataclasses import dataclass
from statistics import mean, quantiles
import math
from numbers import Real
from typing import Any, Callable, Iterable

from .datasets import EvalDataset, EvalExample
from services.optimizer.staged import Candidate


def _metric(value: Any, name: str, *, minimum: float | None = None, maximum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{name} must be a finite number")
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite")
    if minimum is not None and value < minimum:
        raise ValueError(f"{name} cannot be negative")
    if maximum is not None and value > maximum:
        raise ValueError(f"{name} must be between 0 and 1")
    return value


@dataclass(frozen=True)
class BaselineMetrics:
    success_rate: float
    mean_cost_usd: float
    mean_latency_ms: float
    sample_count: int
    p50_latency_ms: float = 0.0
    p95_latency_ms: float = 0.0
    # Each entry is (example, executor outcome), retained for paired grader
    # analysis without requiring a second execution of the candidate.
    outcomes: tuple[tuple[EvalExample, Any], ...] = ()

    def __post_init__(self) -> None:
        _metric(self.success_rate, "success_rate", minimum=0, maximum=1)
        _metric(self.mean_cost_usd, "mean_cost_usd", minimum=0)
        _metric(self.mean_latency_ms, "mean_latency_ms", minimum=0)
        _metric(self.p50_latency_ms, "p50_latency_ms", minimum=0)
        _metric(self.p95_latency_ms, "p95_latency_ms", minimum=0)
        if not isinstance(self.sample_count, int) or self.sample_count < 1:
            raise ValueError("sample_count must be positive")

    @property
    def quality(self) -> float:
        return self.success_rate

    @property
    def paired_outcomes(self) -> tuple[tuple[EvalExample, Any], ...]:
        return self.outcomes


class BaselineRunner:
    def __init__(
        self,
        execute: Callable[[EvalExample, Candidate], Any],
        graders: Iterable[Callable[[Any, Any], Any]] | Callable[[Any, Any], Any] | None = None,
        *,
        grader: Callable[[Any, Any], Any] | None = None,
    ) -> None:
        if graders is not None and grader is not None:
            raise ValueError("provide graders or grader, not both")
        configured = grader if grader is not None else graders
        if configured is None:
            self.graders: tuple[Callable[[Any, Any], Any], ...] = ()
        elif callable(configured):
            self.graders = (configured,)
        else:
            self.graders = tuple(configured)
        if any(not callable(item) for item in self.graders):
            raise TypeError("graders must be callable")
        self.execute = execute

    def run(self, dataset: EvalDataset, candidate: Candidate) -> BaselineMetrics:
        if not dataset.examples:
            raise ValueError("cannot evaluate an empty dataset")
        outcomes = tuple(self.execute(example, candidate) for example in dataset.examples)
        successes: list[float] = []
        costs: list[float] = []
        latencies: list[float] = []
        for outcome, example in zip(outcomes, dataset.examples):
            actual = self._value(outcome, "output", outcome)
            if self.graders:
                scores = [_metric(grader(actual, example.expected), "grader score", minimum=0, maximum=1) for grader in self.graders]
                success = mean(scores)
            else:
                success = self._value(outcome, "success", actual == example.expected)
            if isinstance(success, bool):
                success = float(success)
            successes.append(_metric(success, "success", minimum=0, maximum=1))
            costs.append(_metric(self._value(outcome, "cost_usd", 0.0), "cost_usd", minimum=0))
            latencies.append(_metric(self._value(outcome, "latency_ms", 0.0), "latency_ms", minimum=0))
        p50 = quantiles(latencies, n=2, method="inclusive")[0] if len(latencies) >= 2 else latencies[0]
        p95 = quantiles(latencies, n=20, method="inclusive")[18] if len(latencies) >= 2 else latencies[0]
        paired = tuple(zip(dataset.examples, outcomes))
        return BaselineMetrics(mean(successes), mean(costs), mean(latencies), len(outcomes), p50, p95, paired)

    @staticmethod
    def _value(value: Any, key: str, default: Any) -> Any:
        if isinstance(value, dict):
            return value.get(key, default)
        return getattr(value, key, default)
