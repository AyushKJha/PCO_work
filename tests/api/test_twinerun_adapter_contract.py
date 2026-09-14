"""HTTP contracts for the paths called by the TwineRun browser adapter."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from apps.api.auth import issue_api_key
from apps.api.db import create_session_factory, create_tables
from apps.api.main import create_app
from apps.api.models import Organization, Project


@pytest.fixture()
def client(tmp_path):
    factory = create_session_factory(f"sqlite:///{tmp_path / 'twinerun-contract.db'}")
    create_tables(factory)
    with factory.begin() as session:
        organization = Organization(name="TwineRun contract")
        session.add(organization)
        session.flush()
        project = Project(name="Support agent", slug="support", organization_id=organization.id)
        session.add(project)
        session.flush()
        secret, key = issue_api_key(organization_id=organization.id, project_id=project.id)
        session.add(key)
        ids = (organization.id, project.id, secret)
    app = create_app(session_factory=factory)
    with TestClient(app) as test_client:
        test_client.test_tenant = ids
        yield test_client


def auth_header(client) -> dict[str, str]:
    return {"x-api-key": client.test_tenant[2]}


def queue_optimization(client) -> str:
    response = client.post(
        "/api/v1/optimizations",
        headers=auth_header(client),
        json={"project_id": client.test_tenant[1], "config": {"candidates": [{"id": "candidate-a", "provider": "fake", "model": "fake/model", "parameters": {}}]}},
    )
    assert response.status_code == 202, response.text
    return response.json()["run_id"]


def collection_payload(response, key: str) -> list[dict[str, Any]]:
    payload = response.json()
    if isinstance(payload, list):
        return payload
    assert isinstance(payload, dict)
    values = payload.get(key, [])
    assert isinstance(values, list)
    return values


def test_projects_list_is_available_at_frontend_api_v1_path(client):
    response = client.get("/api/v1/projects", headers=auth_header(client))
    assert response.status_code == 200, response.text
    projects = response.json()
    assert isinstance(projects, list)
    assert projects[0]["id"] == client.test_tenant[1]
    assert projects[0]["name"] == "Support agent"
    assert projects[0]["slug"] == "support"


def test_project_detail_returns_complete_adapter_shape(client):
    response = client.get(f"/api/v1/projects/{client.test_tenant[1]}", headers=auth_header(client))
    assert response.status_code == 200, response.text
    project = response.json()
    assert project["id"] == client.test_tenant[1]
    assert isinstance(project.get("nodes"), list)
    assert isinstance(project.get("edges"), list)


def test_frontend_optimization_start_accepts_adapter_payload(client):
    response = client.post(
        f"/api/v1/projects/{client.test_tenant[1]}/optimization-runs",
        headers=auth_header(client),
        json={"projectVersionId": "latest", "qualityTolerancePp": 1.0, "confidencePct": 95, "objective": "cost_quality", "idempotencyKey": "contract-test-start"},
    )
    assert response.status_code == 202, response.text
    payload = response.json()
    assert payload.get("runId") or payload.get("run_id") or payload.get("id")
    assert isinstance(payload.get("status"), str)


def test_frontend_optimization_events_returns_collection(client):
    run_id = queue_optimization(client)
    response = client.get(f"/api/v1/optimization-runs/{run_id}/events", headers=auth_header(client))
    assert response.status_code == 200, response.text
    for event in collection_payload(response, "events"):
        assert event.get("id") or event.get("eventId") or event.get("event_id")
        assert isinstance(event.get("type") or event.get("eventType") or event.get("event_type"), str)


def test_current_optimization_candidates_route_returns_adapter_collection(client):
    run_id = queue_optimization(client)
    response = client.get(f"/api/v1/optimizations/{run_id}/candidates", headers=auth_header(client))
    assert response.status_code == 200, response.text
    assert isinstance(response.json(), list)


def test_frontend_optimization_candidates_returns_collection(client):
    run_id = queue_optimization(client)
    response = client.get(f"/api/v1/optimization-runs/{run_id}/candidates", headers=auth_header(client))
    assert response.status_code == 200, response.text
    for candidate in collection_payload(response, "candidates"):
        assert candidate.get("id") or candidate.get("candidateId") or candidate.get("candidate_id")


def test_frontend_eval_cases_returns_collection(client):
    run_id = queue_optimization(client)
    response = client.get(f"/api/v1/eval-runs/{run_id}/cases", headers=auth_header(client))
    assert response.status_code == 200, response.text
    for case in collection_payload(response, "cases"):
        assert case.get("id") or case.get("caseId") or case.get("case_id")


def test_frontend_settings_get_returns_project_settings(client):
    response = client.get(f"/api/v1/projects/{client.test_tenant[1]}/settings", headers=auth_header(client))
    assert response.status_code == 200, response.text
    settings = response.json()
    assert settings["projectId"] == client.test_tenant[1]
    assert "qualityTolerancePct" in settings
    assert "confidencePct" in settings


def test_frontend_settings_patch_accepts_browser_settings(client):
    response = client.patch(
        f"/api/v1/projects/{client.test_tenant[1]}/settings",
        headers=auth_header(client),
        json={"qualityTolerancePct": 1.5, "confidencePct": 90},
    )
    assert response.status_code == 200, response.text
    settings = response.json()
    assert settings["qualityTolerancePct"] == 1.5
    assert settings["confidencePct"] == 90


def test_frontend_export_reports_unready_queued_run_or_returns_export(client):
    run_id = queue_optimization(client)
    response = client.get(f"/api/v1/optimization-runs/{run_id}/export", headers=auth_header(client))
    # A queued run cannot be exported; the implemented route may return 409 or
    # an export if the worker completed it before this request.
    assert response.status_code in {200, 409}, response.text
    if response.status_code == 200:
        assert response.headers.get("content-type", "").split(";", 1)[0] in {"application/json", "application/yaml", "text/yaml"}



def test_project_eval_runs_are_listed_in_a_tenant_scoped_collection(client):
    created = client.post(
        f"/api/v1/projects/{client.test_tenant[1]}/eval-suites",
        headers=auth_header(client),
        json={"name": "contract suite", "cases": [{"id": "case-1", "input": {}, "expected": {}}]},
    )
    assert created.status_code == 201, created.text
    started = client.post(
        f"/api/v1/eval-suites/{created.json()['id']}/runs",
        headers=auth_header(client),
        json={},
    )
    assert started.status_code == 202, started.text
    response = client.get(
        f"/api/v1/projects/{client.test_tenant[1]}/eval-runs",
        headers=auth_header(client),
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"][0]["runId"] == started.json()["runId"]
    assert response.json()["page"]["nextCursor"] is None


def test_direct_profile_retrieval_returns_the_queued_profile_run(client):
    started = client.post("/api/v1/profiles", headers=auth_header(client), json={"project_id": client.test_tenant[1]})
    assert started.status_code == 202, started.text
    response = client.get(f"/api/v1/profiles/{started.json()['run_id']}", headers=auth_header(client))
    assert response.status_code == 200, response.text
    assert response.json()["run_id"] == started.json()["run_id"]
    assert response.json()["project_id"] == client.test_tenant[1]


def test_recommendation_alias_preserves_server_readiness_conflict(client):
    run_id = queue_optimization(client)
    response = client.get(f"/api/v1/optimization-runs/{run_id}/recommendation", headers=auth_header(client))
    assert response.status_code == 409, response.text


def test_event_collection_accepts_cursor_and_returns_next_cursor(client):
    run_id = queue_optimization(client)
    cancelled = client.post(f"/api/v1/optimization-runs/{run_id}/cancel", headers=auth_header(client))
    assert cancelled.status_code == 200, cancelled.text
    response = client.get(
        f"/api/v1/optimization-runs/{run_id}/events",
        headers=auth_header(client),
        params={"limit": 1},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["events"]) == 1
    assert body["page"]["nextCursor"] is not None
    next_page = client.get(
        f"/api/v1/optimization-runs/{run_id}/events",
        headers=auth_header(client),
        params={"cursor": body["page"]["nextCursor"]},
    )
    assert next_page.status_code == 200, next_page.text
    assert next_page.json()["events"][0]["sequence"] > body["events"][0]["sequence"]
