"""Full browser contract flow over the real API and durable worker runtime."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from apps.api.auth import issue_api_key
from apps.api.db import create_session_factory, create_tables
from apps.api.main import create_app
from apps.api.models import Organization, Project
from services.worker.queue import InMemoryQueue
from services.worker.runtime import WorkerRuntime


def _trace_payload() -> dict[str, Any]:
    return {
        "resourceSpans": [
            {
                "resource": {"attributes": [{"key": "service.name", "value": {"stringValue": "checkout-agent"}}]},
                "scopeSpans": [
                    {
                        "scope": {"name": "@agentpgo/sdk", "version": "1.0.0"},
                        "spans": [
                            {
                                "traceId": "a" * 32,
                                "spanId": "b" * 16,
                                "name": "answer",
                                "startTimeUnixNano": "1720000000000000000",
                                "endTimeUnixNano": "1720000000100000000",
                                "attributes": [
                                    {"key": "agentpgo.node", "value": {"stringValue": "answer"}},
                                    {"key": "gen_ai.request.model", "value": {"stringValue": "fake/baseline"}},
                                    {"key": "gen_ai.usage.input_tokens", "value": {"intValue": "10"}},
                                    {"key": "gen_ai.usage.output_tokens", "value": {"intValue": "5"}},
                                ],
                            }
                        ],
                    }
                ],
            }
        ]
    }


def _candidate(candidate_id: str, cost_usd: float) -> dict[str, Any]:
    return {
        "id": candidate_id,
        "provider": "fake",
        "model": f"fake/{candidate_id}",
        "parameters": {"temperature": 0},
        "cost_usd": cost_usd,
    }


def test_full_authenticated_flow_preserves_browser_shapes_and_tenancy(tmp_path) -> None:
    factory = create_session_factory(f"sqlite:///{tmp_path / 'full-flow.db'}")
    create_tables(factory)
    with factory.begin() as session:
        first_org = Organization(name="First tenant")
        second_org = Organization(name="Second tenant")
        session.add_all([first_org, second_org])
        session.flush()
        first_project = Project(name="Research agent", slug="research-agent", organization_id=first_org.id)
        second_project = Project(name="Private agent", slug="private-agent", organization_id=second_org.id)
        session.add_all([first_project, second_project])
        session.flush()
        first_secret, first_key = issue_api_key(organization_id=first_org.id)
        second_secret, second_key = issue_api_key(organization_id=second_org.id)
        session.add_all([first_key, second_key])
        ids = {
            "first_org": first_org.id,
            "first_project": first_project.id,
            "second_project": second_project.id,
            "first_secret": first_secret,
            "second_secret": second_secret,
        }

    queue = InMemoryQueue()
    app = create_app(session_factory=factory, queue_publisher=queue)
    with TestClient(app) as client:
        first_headers = {"authorization": f"Bearer {ids['first_secret']}"}
        second_headers = {"x-api-key": ids["second_secret"]}

        identity = client.get("/api/v1/me", headers=first_headers)
        assert identity.status_code == 200
        assert identity.json()["authType"] == "api_key"
        assert identity.json()["organizationId"] == ids["first_org"]

        projects = client.get("/api/v1/projects", headers=first_headers)
        assert projects.status_code == 200
        assert [project["id"] for project in projects.json()] == [ids["first_project"]]

        version = client.post(
            f"/api/v1/projects/{ids['first_project']}/versions",
            headers=first_headers,
            json={
                "version": "v1",
                "environment": "STAGING",
                "nodes": [{"id": "answer", "name": "Answer", "role": "respond", "baselineModel": "fake/baseline"}],
                "edges": [],
            },
        )
        assert version.status_code == 201, version.text
        assert version.json()["nodes"][0]["id"] == "answer"

        traces = client.post(
            f"/api/v1/traces?project_id={ids['first_project']}",
            headers=first_headers,
            json=_trace_payload(),
        )
        assert traces.status_code == 200
        assert traces.json() == {"accepted": 1, "rejected": 0}
        profile = client.get(f"/api/v1/projects/{ids['first_project']}/profile", headers=first_headers)
        assert profile.status_code == 200
        assert profile.json()["model_calls"] == 1
        assert profile.json()["runs_observed"] == 1

        evaluation = client.post(
            "/api/v1/evals",
            headers=first_headers,
            json={
                "project_id": ids["first_project"],
                "name": "checkout-eval",
                "cases": [
                    {"id": "case-1", "input": "question", "expected": "answer", "metadata": {"category": "smoke"}}
                ],
                "graders": [{"name": "exact", "kind": "exact_match", "config": {}}],
            },
        )
        assert evaluation.status_code == 201, evaluation.text
        dataset_id = evaluation.json()["id"]
        fetched_eval = client.get(f"/api/v1/evals/{dataset_id}", headers=first_headers)
        assert fetched_eval.status_code == 200
        assert fetched_eval.json()["cases"][0]["id"] == "case-1"

        baseline = client.post(
            "/api/v1/baselines/run",
            headers=first_headers,
            json={
                "project_id": ids["first_project"],
                "dataset_id": dataset_id,
                "config": {"candidates": [_candidate("baseline", 0.20)]},
                "max_experiment_cost_usd": 1.0,
            },
        )
        assert baseline.status_code == 202
        baseline_run_id = baseline.json()["run_id"]

        def execute(candidate: dict[str, Any], _job: Any) -> dict[str, Any]:
            candidate_id = str(candidate["id"])
            return {
                "provider": "fake",
                "model": f"fake/{candidate_id}",
                "quality": 1.0,
                "quality_samples": [1.0, 1.0],
                "latency_ms": 20.0 if candidate_id == "cheap" else 40.0,
                "cost_usd": float(candidate.get("cost_usd", 0.20)),
            }

        runtime = WorkerRuntime(factory, queue, worker_id="full-flow-worker", candidate_executor=execute, max_receive_count=1)
        assert runtime.process_once() is True
        baseline_result = client.get(f"/api/v1/baselines/{baseline_run_id}", headers=first_headers)
        assert baseline_result.status_code == 200
        assert baseline_result.json()["status"] == "completed"

        optimization = client.post(
            f"/api/v1/projects/{ids['first_project']}/optimization-runs",
            headers=first_headers,
            json={
                "evalSuiteId": dataset_id,
                "projectVersionId": version.json()["id"],
                "qualityTolerancePp": 1.0,
                "confidencePct": 95,
                "config": {
                    "baseline": {"quality": 0.0, "quality_samples": [0.0, 0.0]},
                    "statistical_gate": {"min_samples_for_significance": 2},
                    "candidates": [_candidate("cheap", 0.10), _candidate("slow", 0.30)],
                },
                "maxExperimentCostUsd": 1.0,
            },
        )
        assert optimization.status_code == 202, optimization.text
        run_id = optimization.json()["runId"]
        assert optimization.json()["status"] == "queued"

        assert runtime.process_once() is True
        run = client.get(f"/api/v1/optimizations/{run_id}", headers=first_headers)
        assert run.status_code == 200
        assert run.json()["status"] == "completed"

        events = client.get(f"/api/v1/optimization-runs/{run_id}/events", headers=first_headers)
        assert events.status_code == 200
        assert isinstance(events.json()["events"], list)
        assert events.json()["page"]["nextCursor"] is None

        candidates = client.get(f"/api/v1/optimization-runs/{run_id}/candidates", headers=first_headers)
        assert candidates.status_code == 200
        assert {row["id"] for row in candidates.json()["candidates"]} == {"cheap", "slow"}

        eval_cases = client.get(f"/api/v1/eval-runs/{run_id}/cases", headers=first_headers)
        assert eval_cases.status_code == 200
        assert [case["id"] for case in eval_cases.json()["cases"]] == ["case-1"]
        assert eval_cases.json()["cases"][0]["prompt"] is None

        exported = client.get(f"/api/v1/optimization-runs/{run_id}/export", headers=first_headers)
        assert exported.status_code == 200, exported.text
        assert exported.json()["run_id"] == run_id
        assert exported.json()["recommendation"]["id"] == "cheap"

        denied = client.get(f"/api/v1/projects/{ids['first_project']}", headers=second_headers)
        assert denied.status_code == 403

