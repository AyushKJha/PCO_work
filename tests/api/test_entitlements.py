"""Free/Pro entitlement and server-side quota contracts."""

from __future__ import annotations

from copy import deepcopy

from fastapi.testclient import TestClient

from apps.api import main as api_main
from apps.api.auth import issue_api_key
from apps.api.db import create_session_factory, create_tables
from apps.api.main import create_app
from apps.api.models import Organization, Project


def _client(tmp_path):
    factory = create_session_factory(f"sqlite:///{tmp_path / 'entitlements.db'}")
    create_tables(factory)
    with factory.begin() as session:
        organization = Organization(name="Entitlements")
        session.add(organization)
        session.flush()
        secret, key = issue_api_key(organization_id=organization.id, name="org")
        session.add(key)
        session.flush()
        project = Project(name="Agent", slug="agent", organization_id=organization.id)
        session.add(project)
        session.flush()
        project_secret, project_key = issue_api_key(organization_id=organization.id, project_id=project.id, name="project")
        session.add(project_key)
        ids = {"organization": organization.id, "project": project.id, "org_secret": secret, "project_secret": project_secret}
    return TestClient(create_app(session_factory=factory)), ids


def _headers(ids):
    return {"x-api-key": ids["org_secret"]}


def _otlp_payload(span_id="0123456789abcdef"):
    return {"resourceSpans": [{"resource": {"attributes": [{"key": "service.name", "value": {"stringValue": "agent"}}]}, "scopeSpans": [{"scope": {"name": "agentpgo.sdk", "version": "1.0.0"}, "spans": [{"traceId": "0123456789abcdef0123456789abcdef", "spanId": span_id, "name": "answer", "kind": 1, "startTimeUnixNano": "1720000000000000000", "endTimeUnixNano": "1720000000100000000", "attributes": [{"key": "gen_ai.system", "value": {"stringValue": "openai"}}, {"key": "gen_ai.usage.input_tokens", "value": {"intValue": "12"}}], "status": {"code": 1}}]}]}]}


def test_entitlements_exposes_effective_plan_limits_and_usage(tmp_path):
    client, ids = _client(tmp_path)
    with client:
        response = client.get("/api/v1/entitlements", headers=_headers(ids))
    assert response.status_code == 200
    body = response.json()
    assert body["organizationId"] == ids["organization"]
    assert body["plan"] == "free"
    assert body["status"] == "active"
    assert body["limits"]["maxProjects"] == api_main.PLAN_LIMITS["free"]["maxProjects"]
    assert body["usage"]["projects"] == 1
    assert body["usage"]["traceSpansThisMonth"] == 0


def test_project_quota_returns_stable_entitlement_error(tmp_path, monkeypatch):
    monkeypatch.setitem(api_main.PLAN_LIMITS["free"], "maxProjects", 1)
    client, ids = _client(tmp_path)
    with client:
        response = client.post("/api/v1/projects", headers=_headers(ids), json={"name": "Another", "slug": "another"})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "ENTITLEMENT_LIMIT_REACHED"
    assert response.json()["error"]["fields"]["limit"] == "maxProjects"


def test_eval_case_quota_applies_to_new_and_existing_suites(tmp_path, monkeypatch):
    monkeypatch.setitem(api_main.PLAN_LIMITS["free"], "maxEvalCases", 1)
    client, ids = _client(tmp_path)
    headers = _headers(ids)
    case = {"id": "case-1", "input": {"q": "one"}, "expected": {"a": "one"}}
    with client:
        first = client.post("/api/v1/evals", headers=headers, json={"project_id": ids["project"], "name": "suite", "cases": [case]})
        second = client.post("/api/v1/evals", headers=headers, json={"project_id": ids["project"], "name": "suite-2", "cases": [case]})
    assert first.status_code == 201, first.text
    assert second.status_code == 403
    assert second.json()["error"]["code"] == "ENTITLEMENT_LIMIT_REACHED"


def test_trace_quota_counts_only_new_spans_and_rejects_overage_atomically(tmp_path, monkeypatch):
    monkeypatch.setitem(api_main.PLAN_LIMITS["free"], "maxTraceSpansPerMonth", 1)
    client, ids = _client(tmp_path)
    headers = {"x-api-key": ids["project_secret"], "content-type": "application/json"}
    first_payload = _otlp_payload()
    second_payload = deepcopy(first_payload)
    second_payload["resourceSpans"][0]["scopeSpans"][0]["spans"][0]["spanId"] = "fedcba9876543210"
    with client:
        first = client.post("/api/v1/traces", headers=headers, json=first_payload)
        duplicate = client.post("/api/v1/traces", headers=headers, json=first_payload)
        overage = client.post("/api/v1/traces", headers=headers, json=second_payload)
    assert first.status_code == 200
    assert duplicate.status_code == 200 and duplicate.json()["accepted"] == 0
    assert overage.status_code == 403
    assert overage.json()["error"]["code"] == "ENTITLEMENT_LIMIT_REACHED"


def test_optimization_quota_is_monthly_and_idempotent_retries_are_safe(tmp_path, monkeypatch):
    monkeypatch.setitem(api_main.PLAN_LIMITS["free"], "maxOptimizationRunsPerMonth", 1)
    client, ids = _client(tmp_path)
    headers = _headers(ids)
    body = {"project_id": ids["project"], "idempotencyKey": "same", "config": {"candidates": []}}
    with client:
        first = client.post("/api/v1/optimizations", headers=headers, json=body)
        retry = client.post("/api/v1/optimizations", headers=headers, json=body)
        second = client.post("/api/v1/optimizations", headers=headers, json={**body, "idempotencyKey": "different"})
    assert first.status_code == 202, first.text
    assert retry.status_code == 202 and retry.json()["run_id"] == first.json()["run_id"]
    assert second.status_code == 403
    assert second.json()["error"]["code"] == "ENTITLEMENT_LIMIT_REACHED"
