import math

import pytest
import yaml

from services.evaluator.baseline import BaselineRunner
from services.evaluator.datasets import EvalDataset, EvalExample
from services.optimizer.gates import StatisticalGate
from services.optimizer.pareto import pareto_frontier, recommend
from services.optimizer.search import AssignmentCandidate, search_assignments
from services.optimizer.staged import Candidate, StagedOptimizer
from services.optimizer.yaml_export import export_yaml


def test_candidate_rejects_non_finite_and_out_of_range_metrics():
    with pytest.raises(ValueError):
        Candidate("nan", math.nan, 10, 0.5)
    with pytest.raises(ValueError):
        Candidate("quality", 0.1, 10, 1.1)


def test_baseline_rejects_non_finite_execution_metrics():
    dataset = EvalDataset("demo", [EvalExample("a", "x", "ok")])

    with pytest.raises(ValueError):
        BaselineRunner(lambda _example, _candidate: {"output": "ok", "cost_usd": math.inf, "latency_ms": 10}).run(
            dataset, Candidate("base", 0.01, 1, 1.0)
        )


def test_statistical_gate_rejects_underpowered_sample():
    gate = StatisticalGate(min_quality_delta=0.01, min_samples_for_significance=5)
    result = gate.test([0.8, 0.8, 0.8], [0.8, 0.8, 0.9])
    assert not result.accepted
    assert result.reason == "insufficient samples"


def test_search_accepts_model_generator_and_reports_baseline_relative_delta():
    baseline = {"planner": "small", "critic": "small"}

    def evaluate(config):
        quality = 0.8 + (0.1 if config["planner"] == "large" else 0.0)
        return AssignmentCandidate(config, quality, 0.1, 100)

    results = search_assignments(baseline, (m for m in ["small", "large"]), evaluate, beam_width=4)
    large = next(item for item in results if item.config["planner"] == "large")
    assert large.quality_delta == pytest.approx(0.1)


def test_search_filters_latency_and_spend():
    baseline = {"planner": "small"}

    def evaluate(config):
        if config["planner"] == "large":
            return AssignmentCandidate(config, 0.95, 2.0, 500)
        return AssignmentCandidate(config, 0.8, 0.1, 100)

    results = search_assignments(
        baseline,
        ["small", "large"],
        evaluate,
        max_latency_ms=200,
        max_cost_usd=1.0,
    )
    assert {item.config["planner"] for item in results} == {"small"}


def test_pareto_and_recommendation_apply_filters_and_reject_invalid_weights():
    candidates = [Candidate("cheap", 0.1, 100, 0.8), Candidate("slow", 0.2, 500, 0.95)]
    assert [item.id for item in pareto_frontier(candidates, max_latency_ms=200)] == ["cheap"]
    with pytest.raises(ValueError):
        recommend(candidates, quality_weight=math.nan)


def test_yaml_export_is_an_agentpgo_document():
    payload = yaml.safe_load(export_yaml(Candidate("best", 0.2, 100, 0.9, {"model": "x"})))
    assert payload["apiVersion"] == "agentpgo/v1"
    assert payload["kind"] == "Recommendation"
    assert payload["spec"]["id"] == "best"
