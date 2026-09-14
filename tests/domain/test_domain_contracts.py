import json

import pytest

from services.profiler.cost_catalog import CostCatalog, CostRate
from services.profiler.snapshots import TraceEvent, TraceSnapshot
from services.evaluator.datasets import EvalDataset, EvalExample
from services.evaluator.graders import exact_match, json_subset, contains
from services.evaluator.baseline import BaselineRunner
from services.worker.providers import (
    ProviderExecutor,
    ProviderRequest,
    ProviderResponse,
    classify_provider_error,
    RetryPolicy,
)
from services.optimizer.staged import Candidate, StagedOptimizer
from services.optimizer.gates import StatisticalGate
from services.optimizer.pareto import pareto_frontier, recommend
from services.optimizer.yaml_export import export_yaml


def test_catalog_snapshot_is_immutable_and_computes_token_cost():
    catalog = CostCatalog([CostRate("openai", "gpt-4o-mini", 0.15, 0.60)])
    snapshot = catalog.snapshot("catalog-1")
    assert snapshot.token_cost("openai", "gpt-4o-mini", 1_000, 500) == pytest.approx(0.00045)
    with pytest.raises(KeyError):
        snapshot.token_cost("openai", "missing", 1, 1)


def test_trace_snapshot_aggregates_cost_and_latency():
    rates = CostCatalog([CostRate("openai", "gpt-4o-mini", 0.15, 0.60)]).snapshot("v1")
    trace = TraceSnapshot("run-1", [TraceEvent("e1", "openai", "gpt-4o-mini", 100, 50, 250)])
    assert trace.total_tokens == 150
    assert trace.total_latency_ms == 250
    assert trace.cost(rates) == pytest.approx(0.000045)


def test_dataset_and_deterministic_graders_are_stable():
    dataset = EvalDataset("demo", [EvalExample("a", "Say hi", "hi")])
    assert dataset.examples[0].id == "a"
    assert exact_match("hi", "hi") == 1.0
    assert contains("The answer is hi", "hi") == 1.0
    assert json_subset('{"answer":"hi","score":1}', '{"answer":"hi"}') == 1.0
    assert json_subset("not-json", "{}") == 0.0


def test_baseline_runner_returns_mean_success_cost_and_latency():
    dataset = EvalDataset("demo", [EvalExample("a", "x", "ok"), EvalExample("b", "y", "ok")])

    def execute(example, candidate):
        return {"output": "ok", "cost_usd": candidate.cost_usd, "latency_ms": 10}

    metrics = BaselineRunner(execute).run(dataset, Candidate("base", cost_usd=0.01, latency_ms=1, quality=1))
    assert metrics.success_rate == 1.0
    assert metrics.mean_cost_usd == pytest.approx(0.01)
    assert metrics.mean_latency_ms == 10


def test_provider_executor_retries_transient_and_is_injectable():
    attempts = []

    def transport(request):
        attempts.append(request)
        if len(attempts) == 1:
            raise TimeoutError("network timeout")
        return ProviderResponse("ok", 2, 3, 4)

    executor = ProviderExecutor(transport=transport, retry_policy=RetryPolicy(max_attempts=2, backoff_seconds=0))
    response = executor.execute(ProviderRequest("openai", "gpt", "hello"))
    assert response.text == "ok"
    assert len(attempts) == 2


def test_error_classification_marks_auth_as_non_retryable():
    error = classify_provider_error(Exception("401 invalid api key"))
    assert error.category == "authentication"
    assert not error.retryable


def test_optimizer_runs_sensitivity_beam_and_halving_deterministically():
    candidates = [Candidate("a", 0.5, 100, 0.80), Candidate("b", 0.2, 120, 0.78), Candidate("c", 0.8, 80, 0.82)]
    optimizer = StagedOptimizer(evaluate=lambda c, budget: c.quality)
    result = optimizer.optimize(candidates, beam_width=2, halving_rounds=2)
    assert result.recommended.id == "c"
    assert result.stages[0].name == "sensitivity"
    assert result.stages[-1].name == "successive_halving"


def test_gate_requires_quality_non_regression_and_significance():
    gate = StatisticalGate(min_quality_delta=0.01, alpha=0.05, min_samples_for_significance=3)
    assert gate.accept(baseline=[0.8, 0.8, 0.8], candidate=[0.9, 0.9, 0.9])
    assert not gate.accept(baseline=[0.9, 0.9, 0.9], candidate=[0.8, 0.8, 0.9])


def test_pareto_recommendation_and_yaml_export():
    candidates = [Candidate("cheap", 0.1, 100, 0.80), Candidate("best", 0.2, 110, 0.90), Candidate("dominated", 0.3, 120, 0.70)]
    frontier = pareto_frontier(candidates)
    assert {c.id for c in frontier} == {"cheap", "best"}
    assert recommend(frontier).id == "best"
    parsed = json.loads(json.dumps(export_yaml(recommend(frontier)))) if False else export_yaml(recommend(frontier))
    assert "id: best" in parsed
