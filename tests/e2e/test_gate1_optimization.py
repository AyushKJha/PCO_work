"""Gate 1: deterministic API, worker, evaluation, and recommendation flow."""

from __future__ import annotations

from statistics import mean
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient

from apps.api.auth import issue_api_key
from apps.api.db import create_session_factory, create_tables
from apps.api.main import create_app
from apps.api.models import EvalDataset, Job, OptimizationResult, Organization
from services.evaluator.graders import exact_match
from services.optimizer.gates import StatisticalGate
from services.optimizer.pareto import pareto_frontier, recommend
from services.optimizer.staged import Candidate
from services.optimizer.yaml_export import export_yaml
from services.worker.providers import ProviderExecutor, ProviderRequest, ProviderResponse, RetryPolicy
from services.worker.queue import InMemoryQueue
from services.worker.runtime import WorkerRuntime


@pytest.fixture()
def gate1_environment(tmp_path):
    factory = create_session_factory(f"sqlite:///{tmp_path / 'gate1-e2e.db'}")
    create_tables(factory)
    with factory.begin() as session:
        organization = Organization(name="Gate 1 Synthetic")
        session.add(organization)
        session.flush()
        secret, api_key = issue_api_key(organization_id=organization.id, name="gate1")
        session.add(api_key)
        session.flush()
        organization_id = organization.id

    queue = InMemoryQueue()
    app = create_app(session_factory=factory, queue_publisher=queue)
    with TestClient(app) as client:
        yield client, factory, queue, secret, organization_id


def _headers(secret: str) -> dict[str, str]:
    return {"x-api-key": secret}


def _cases() -> list[dict[str, Any]]:
    return [
        {"id": f"case-{index}", "input": f"question-{index}", "expected": f"answer-{index}"}
        for index in range(1, 6)
    ]


def _candidate(candidate_id: str, *, cost_usd: float) -> dict[str, Any]:
    return {
        "id": candidate_id,
        "provider": "fake",
        "model": candidate_id,
        "parameters": {"temperature": 0},
        "cost_usd": cost_usd,
    }


def test_gate1_fake_provider_optimization_persists_recommendation_and_exports_yaml(
    gate1_environment,
) -> None:
    client, factory, queue, secret, organization_id = gate1_environment
    headers = _headers(secret)

    project_response = client.post(
        "/v1/projects",
        headers=headers,
        json={"name": "Gate 1 Checkout", "slug": "gate1-checkout"},
    )
    assert project_response.status_code == 201, project_response.text
    project_id = project_response.json()["id"]

    cases = _cases()
    dataset_response = client.post(
        "/v1/evals",
        headers=headers,
        json={
            "project_id": project_id,
            "name": "gate1-smoke",
            "cases": cases,
            "graders": [{"name": "exact", "kind": "exact_match", "config": {}}],
        },
    )
    assert dataset_response.status_code == 201, dataset_response.text
    dataset_id = dataset_response.json()["id"]

    persisted_dataset = client.get(f"/v1/evals/{dataset_id}", headers=headers)
    assert persisted_dataset.status_code == 200, persisted_dataset.text
    assert persisted_dataset.json()["project_id"] == project_id
    assert [case["id"] for case in persisted_dataset.json()["cases"]] == [case["id"] for case in cases]
    with factory() as session:
        dataset = session.get(EvalDataset, dataset_id)
        assert dataset is not None
        assert dataset.project_id == project_id
        assert len(dataset.cases) == len(cases)
        assert dataset.graders[0].kind == "exact_match"

    provider_calls: list[ProviderRequest] = []

    def fake_transport(request: ProviderRequest) -> ProviderResponse:
        provider_calls.append(request)
        case_id = request.prompt
        if request.model == "baseline":
            return ProviderResponse("incorrect", latency_ms=80.0)
        if request.model == "fast-good":
            return ProviderResponse(f"answer-{case_id.removeprefix('case-')}", latency_ms=20.0)
        if request.model == "slow-good":
            return ProviderResponse(f"answer-{case_id.removeprefix('case-')}", latency_ms=100.0)
        return ProviderResponse("incorrect", latency_ms=10.0)

    provider = ProviderExecutor(
        transport=fake_transport,
        retry_policy=RetryPolicy(max_attempts=1, backoff_seconds=0, timeout_seconds=1),
    )

    def execute_candidate(candidate: dict[str, Any], _job: Job) -> dict[str, Any]:
        outputs: list[dict[str, Any]] = []
        quality_samples: list[float] = []
        total_latency_ms = 0.0
        for case in cases:
            response = provider.execute(
                ProviderRequest(
                    provider=str(candidate["provider"]),
                    model=str(candidate["model"]),
                    prompt=str(case["id"]),
                    temperature=float(candidate["parameters"]["temperature"]),
                )
            )
            score = exact_match(response.text, case["expected"])
            quality_samples.append(score)
            total_latency_ms += response.latency_ms
            outputs.append({"case_id": case["id"], "output": response.text, "quality": score})
        return {
            "quality": mean(quality_samples),
            "quality_samples": quality_samples,
            "latency_ms": total_latency_ms,
            "cost_usd": float(candidate["cost_usd"]),
            "outputs": outputs,
            "provider": candidate["provider"],
            "model": candidate["model"],
        }

    baseline_response = client.post(
        "/v1/baselines/run",
        headers=headers,
        json={
            "project_id": project_id,
            "dataset_id": dataset_id,
            "config": {"candidates": [_candidate("baseline", cost_usd=0.20)]},
            "max_experiment_cost_usd": 1.0,
        },
    )
    assert baseline_response.status_code == 202, baseline_response.text
    baseline_run_id = baseline_response.json()["run_id"]

    runtime = WorkerRuntime(
        factory,
        queue,
        worker_id="gate1-worker",
        candidate_executor=execute_candidate,
        max_receive_count=1,
    )
    assert runtime.process_once() is True

    baseline_run = client.get(f"/v1/baselines/{baseline_run_id}", headers=headers)
    assert baseline_run.status_code == 200, baseline_run.text
    assert baseline_run.json()["status"] == "completed"
    baseline_result = baseline_run.json()["result"]["candidates"][0]
    assert baseline_result["id"] == "baseline"
    assert baseline_result["quality"] == 0.0
    assert baseline_result["latency_ms"] == 400.0
    assert baseline_run.json()["result"]["spent_usd"] == pytest.approx(0.20)

    optimization_response = client.post(
        "/v1/optimizations",
        headers=headers,
        json={
            "project_id": project_id,
            "dataset_id": dataset_id,
            "config": {
                "beam_width": 3,
                "halving_rounds": 1,
                "initial_budget": 1,
                "max_quality_regression": 0.0,
                "candidates": [
                    _candidate("fast-good", cost_usd=0.10),
                    _candidate("slow-good", cost_usd=0.30),
                    _candidate("cheap-bad", cost_usd=0.05),
                ],
            },
            "max_experiment_cost_usd": 1.0,
        },
    )
    assert optimization_response.status_code == 202, optimization_response.text
    optimization_run_id = optimization_response.json()["run_id"]
    optimization_payload = client.get(f"/v1/optimizations/{optimization_run_id}", headers=headers)
    assert optimization_payload.status_code == 200, optimization_payload.text
    assert optimization_payload.json()["config"]["beam_width"] == 3
    assert {item["id"] for item in optimization_payload.json()["config"]["candidates"]} == {
        "fast-good",
        "slow-good",
        "cheap-bad",
    }

    assert runtime.process_once() is True
    optimization_run = client.get(f"/v1/optimizations/{optimization_run_id}", headers=headers)
    assert optimization_run.status_code == 200, optimization_run.text
    assert optimization_run.json()["status"] == "completed"
    candidate_rows = client.get(
        f"/v1/optimizations/{optimization_run_id}/candidates", headers=headers
    )
    assert candidate_rows.status_code == 200, candidate_rows.text
    results = {row["id"]: row for row in candidate_rows.json()}
    assert set(results) == {"fast-good", "slow-good", "cheap-bad"}
    assert optimization_run.json()["result"]["spent_usd"] == pytest.approx(0.45)
    assert len(provider_calls) == len(cases) * 4

    baseline_samples = baseline_result["quality_samples"]
    fast_result = results["fast-good"]
    fast_samples = fast_result["quality_samples"]
    statistical_gate = StatisticalGate(
        min_quality_delta=0.10,
        alpha=0.05,
        min_samples_for_significance=5,
        max_quality_regression=0.0,
    )
    statistical_result = statistical_gate.test(baseline_samples, fast_samples)
    assert statistical_result.accepted
    assert statistical_result.mean_delta == pytest.approx(1.0)
    assert fast_result["quality"] >= baseline_result["quality"]
    assert fast_result["latency_ms"] < baseline_result["latency_ms"]
    assert fast_result["cost_usd"] < baseline_result["cost_usd"]
    assert optimization_run.json()["result"]["spent_usd"] <= 1.0

    candidates = [
        Candidate(
            id=row["id"],
            cost_usd=row["cost_usd"],
            latency_ms=row["latency_ms"],
            quality=row["quality"],
            config={"provider": row["provider"], "model": row["model"], "parameters": {"temperature": 0}},
        )
        for row in results.values()
    ]
    eligible = pareto_frontier(candidates, max_latency_ms=200.0, max_cost_usd=0.20)
    recommended = recommend(eligible, max_latency_ms=200.0, max_cost_usd=0.20)
    assert recommended.id == "fast-good"
    assert recommended.quality >= baseline_result["quality"]
    assert recommended.latency_ms <= 200.0
    assert recommended.cost_usd <= 0.20

    recommendation = {
        "id": recommended.id,
        "config": recommended.config,
        "metrics": {
            "quality": recommended.quality,
            "latency_ms": recommended.latency_ms,
            "cost_usd": recommended.cost_usd,
        },
        "statistical_gate": {
            "accepted": statistical_result.accepted,
            "mean_delta": statistical_result.mean_delta,
            "p_value": statistical_result.p_value,
        },
    }

    # The worker persists the verified recommendation as part of completion.
    not_ready = client.get(
        f"/v1/optimizations/{optimization_run_id}/recommendation", headers=headers
    )
    assert not_ready.status_code == 200, not_ready.text

    recommendation_response = client.get(
        f"/v1/optimizations/{optimization_run_id}/recommendation", headers=headers
    )
    assert recommendation_response.status_code == 200, recommendation_response.text
    persisted_recommendation = recommendation_response.json()
    assert persisted_recommendation["id"] == "fast-good"
    assert persisted_recommendation["metrics"]["quality"] == pytest.approx(1.0)
    assert persisted_recommendation["metrics"]["cost_usd"] == pytest.approx(0.10)
    with factory() as session:
        persisted = session.query(OptimizationResult).filter_by(job_id=optimization_run_id).one()
        assert persisted.recommendation["id"] == "fast-good"

    export_response = client.get(
        "/v1/policy/export",
        headers=headers,
        params={"project_id": project_id, "run_id": optimization_run_id},
    )
    assert export_response.status_code == 200, export_response.text
    assert export_response.json()["recommendation"]["id"] == "fast-good"

    yaml_payload = yaml.safe_load(export_yaml(recommended))
    assert yaml_payload["apiVersion"] == "agentpgo/v1"
    assert yaml_payload["kind"] == "Recommendation"
    assert yaml_payload["metadata"]["name"] == "fast-good"
    assert yaml_payload["spec"]["metrics"]["quality"] == pytest.approx(1.0)
