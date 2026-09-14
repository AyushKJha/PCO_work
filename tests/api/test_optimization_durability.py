"""Durable optimization run metadata, idempotency, cancellation, and events."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from apps.api.auth import issue_api_key
from apps.api.db import create_session_factory, create_tables
from apps.api.main import create_app
from apps.api.models import Job, JobCandidateResult, Organization, Project
from services.worker.queue import InMemoryQueue
from services.worker.runtime import WorkerRuntime


def _environment(tmp_path):
    factory = create_session_factory(f"sqlite:///{tmp_path / 'durability.db'}")
    create_tables(factory)
    with factory.begin() as session:
        organization = Organization(name="Durable optimization")
        session.add(organization)
        session.flush()
        project = Project(name="Research", slug="research", organization_id=organization.id)
        session.add(project)
        session.flush()
        secret, key = issue_api_key(organization_id=organization.id)
        session.add(key)
        ids = (project.id, secret)
    queue = InMemoryQueue()
    app = create_app(session_factory=factory, queue_publisher=queue)
    return factory, queue, app, ids


def test_optimization_start_is_idempotent_and_persists_metadata(tmp_path):
    factory, queue, app, (project_id, secret) = _environment(tmp_path)
    headers = {"x-api-key": secret}
    body = {
        "projectVersionId": "latest",
        "qualityTolerancePp": 1.0,
        "confidencePct": 95,
        "allowedModels": ["fake/cheap", "fake/frontier"],
        "objective": "cost_quality",
        "idempotencyKey": "same-start",
        "config": {"candidates": [{"id": "cheap", "provider": "fake", "model": "fake/cheap", "cost_usd": 0.1}]},
    }
    with TestClient(app) as client:
        first = client.post(f"/v1/projects/{project_id}/optimization-runs", headers=headers, json=body)
        second = client.post(f"/v1/projects/{project_id}/optimization-runs", headers=headers, json=body)
        assert first.status_code == 202, first.text
        assert second.status_code == 202, second.text
        assert first.json()["runId"] == second.json()["runId"]
        header_retry = {key: value for key, value in body.items() if key != "idempotencyKey"}
        third = client.post(
            f"/v1/projects/{project_id}/optimization-runs",
            headers={**headers, "Idempotency-Key": "same-start"},
            json=header_retry,
        )
        assert third.status_code == 202, third.text
        assert third.json()["runId"] == first.json()["runId"]
        assert len(queue._messages) == 1

        status = client.get(f"/v1/optimization-runs/{first.json()['runId']}", headers=headers)
        assert status.status_code == 200, status.text
        assert status.json()["objective"] == "cost_quality"
        assert status.json()["allowedModels"] == ["fake/cheap", "fake/frontier"]
        assert status.json()["projectVersionId"] is None
        assert status.json()["events"][0]["type"] == "INFO"

        changed = {**body, "config": {"candidates": []}}
        conflict = client.post(f"/v1/projects/{project_id}/optimization-runs", headers=headers, json=changed)
        assert conflict.status_code == 409, conflict.text

    del factory


def test_queued_optimization_can_be_cancelled_and_event_replayed(tmp_path):
    _factory, queue, app, (project_id, secret) = _environment(tmp_path)
    headers = {"x-api-key": secret}
    with TestClient(app) as client:
        started = client.post(
            f"/v1/projects/{project_id}/optimization-runs",
            headers=headers,
            json={"idempotencyKey": "cancel-me", "config": {"candidates": []}},
        )
        run_id = started.json()["runId"]
        cancelled = client.post(f"/v1/optimization-runs/{run_id}/cancel", headers=headers)
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.json()["status"] == "cancelled"
        events = client.get(f"/v1/optimization-runs/{run_id}/events", headers=headers)
        assert events.status_code == 200, events.text
        assert [event["type"] for event in events.json()["events"]] == ["INFO", "INFO"]
        assert events.json()["status"] == "CANCELLED"
        assert queue._messages


def test_candidate_selection_requires_completed_verified_worker_result(tmp_path):
    factory, queue, app, (project_id, secret) = _environment(tmp_path)
    headers = {"x-api-key": secret}
    with TestClient(app) as client:
        started = client.post(
            f"/v1/projects/{project_id}/optimization-runs",
            headers=headers,
            json={"idempotencyKey": "select-before-complete", "config": {"candidates": []}},
        )
        assert started.status_code == 202, started.text
        run_id = started.json()["runId"]
        with factory.begin() as session:
            session.add(JobCandidateResult(job_id=run_id, candidate_id="unverified", result={"quality": 1.0}))

        response = client.post(
            f"/v1/optimization-runs/{run_id}/select",
            headers=headers,
            json={"candidateId": "unverified"},
        )
        assert response.status_code == 409, response.text
        with factory.begin() as session:
            job = session.get(Job, run_id)
            assert job is not None
            assert job.status == "queued"
            assert job.optimization_result is None


def test_worker_persists_replayable_lifecycle_events(tmp_path):
    factory, queue, app, (project_id, secret) = _environment(tmp_path)
    headers = {"x-api-key": secret}
    with TestClient(app) as client:
        started = client.post(
            f"/v1/projects/{project_id}/optimization-runs",
            headers=headers,
            json={"idempotencyKey": "run-events", "config": {"candidates": [{"id": "one", "cost_usd": 0.1, "quality": 0.9}]}},
        )
        run_id = started.json()["runId"]

        def execute(candidate: dict[str, Any], _job: Any) -> dict[str, Any]:
            return {"cost_usd": float(candidate["cost_usd"]), "quality": 0.9, "latency_ms": 5.0}

        runtime = WorkerRuntime(factory, queue, worker_id="event-worker", candidate_executor=execute)
        assert runtime.process_once() is True
        events = client.get(f"/v1/optimization-runs/{run_id}/events", headers=headers)
        assert events.status_code == 200, events.text
        types = [event["type"] for event in events.json()["events"]]
        assert types[:3] == ["INFO", "INFO", "TESTING"]
        assert "SELECTED" in types
        assert events.json()["status"] == "COMPLETED"
