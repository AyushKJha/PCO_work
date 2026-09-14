"""Contract tests for the AgentPGO OTLP API.

These tests intentionally exercise the public HTTP contract rather than the
implementation details.  The fixture uses SQLite so it can run in CI without
a PostgreSQL server; the production models remain PostgreSQL-compatible.
"""

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from apps.api.auth import issue_api_key
from apps.api.db import create_session_factory, create_tables
from apps.api.main import create_app
from apps.api.models import ApiKey, Organization, Project


@pytest.fixture()
def client(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'api.db'}"
    session_factory = create_session_factory(database_url)
    create_tables(session_factory)
    with session_factory() as session:
        organization = Organization(name="Acme")
        session.add(organization)
        session.flush()
        project = Project(name="Support agent", slug="support", organization_id=organization.id)
        session.add(project)
        session.flush()
        secret, key = issue_api_key(
            organization_id=organization.id,
            project_id=project.id,
            name="test key",
        )
        session.add(key)
        session.commit()
        ids = (organization.id, project.id, secret)
    app = create_app(session_factory=session_factory)
    with TestClient(app) as test_client:
        test_client.test_tenant = ids
        yield test_client


def otlp_payload(trace_id="0123456789abcdef0123456789abcdef", span_id="0123456789abcdef"):
    return {
        "resourceSpans": [
            {
                "resource": {"attributes": [{"key": "service.name", "value": {"stringValue": "agent"}}]},
                "scopeSpans": [
                    {
                        "scope": {"name": "agentpgo.sdk", "version": "1.0.0"},
                        "spans": [
                            {
                                "traceId": trace_id,
                                "spanId": span_id,
                                "name": "answer",
                                "kind": 1,
                                "startTimeUnixNano": "1720000000000000000",
                                "endTimeUnixNano": "1720000000100000000",
                                "attributes": [
                                    {"key": "gen_ai.system", "value": {"stringValue": "openai"}},
                                    {"key": "gen_ai.usage.input_tokens", "value": {"intValue": "12"}},
                                ],
                                "status": {"code": 1},
                            }
                        ],
                    }
                ],
            }
        ]
    }


def auth_header(client):
    return {"x-api-key": client.test_tenant[2]}


def test_health_is_public(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_otlp_json_ingestion_requires_api_key(client):
    response = client.post("/v1/traces", json=otlp_payload())
    assert response.status_code == 401
    assert response.json()["detail"] == "Missing API key"


def test_otlp_json_ingestion_accepts_bearer_and_returns_count(client):
    response = client.post(
        "/v1/traces",
        headers={"authorization": f"Bearer {client.test_tenant[2]}"},
        json=otlp_payload(),
    )
    assert response.status_code == 200
    assert response.json()["accepted"] == 1


def test_otlp_ingestion_rejects_invalid_trace_identifiers(client):
    payload = otlp_payload(trace_id="not-a-trace", span_id="bad")
    response = client.post("/v1/traces", headers=auth_header(client), json=payload)
    assert response.status_code == 422


def test_otlp_ingestion_is_idempotent_for_same_span(client):
    headers = auth_header(client)
    first = client.post("/v1/traces", headers=headers, json=otlp_payload())
    second = client.post("/v1/traces", headers=headers, json=otlp_payload())
    assert first.status_code == second.status_code == 200
    assert first.json()["accepted"] == 1
    assert second.json()["accepted"] == 0


def test_project_id_is_tenant_scoped(client):
    payload = otlp_payload()
    response = client.post(
        "/v1/traces?project_id=00000000-0000-0000-0000-000000000001",
        headers=auth_header(client),
        json=payload,
    )
    assert response.status_code == 403


def test_invalid_api_key_is_rejected(client):
    response = client.post("/v1/traces", headers={"x-api-key": "agp_invalid"}, json=otlp_payload())
    assert response.status_code == 401


def test_otlp_content_type_must_be_json(client):
    response = client.post(
        "/v1/traces",
        headers={**auth_header(client), "content-type": "text/plain"},
        content="{}",
    )
    assert response.status_code == 415
