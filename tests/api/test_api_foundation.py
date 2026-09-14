"""Focused contract tests for the browser-facing API foundation."""

from __future__ import annotations

import apps.api.auth as auth_module
import pytest
from fastapi.testclient import TestClient

from apps.api.auth import issue_api_key, issue_demo_token, revoke_demo_token
from apps.api.db import create_session_factory, create_tables
from apps.api.main import DEFAULT_APP_ORIGIN, create_app
from apps.api.models import Organization, Project


def _client(tmp_path, monkeypatch, *, demo: bool = False, production: bool = False):
    monkeypatch.setenv("APP_ENV", "production" if production else "test")
    monkeypatch.delenv("DEMO_AUTH_ENABLED", raising=False)
    monkeypatch.delenv("DEMO_AUTH_SECRET", raising=False)
    monkeypatch.delenv("DEMO_AUTH_ORGANIZATION_ID", raising=False)
    monkeypatch.delenv("DEMO_AUTH_PROJECT_ID", raising=False)
    factory = create_session_factory(f"sqlite:///{tmp_path / 'foundation.db'}")
    create_tables(factory)
    with factory.begin() as session:
        organization = Organization(name="TwineRun test")
        session.add(organization)
        session.flush()
        project = Project(name="Research", slug="research", organization_id=organization.id)
        session.add(project)
        session.flush()
        secret, key = issue_api_key(organization_id=organization.id, project_id=project.id)
        session.add(key)
        ids = {"organization": organization.id, "project": project.id, "api_key": secret}
    if demo:
        monkeypatch.setenv("DEMO_AUTH_ENABLED", "true")
        monkeypatch.setenv("DEMO_AUTH_SECRET", "test-demo-secret")
        monkeypatch.setenv("DEMO_AUTH_ORGANIZATION_ID", ids["organization"])
        monkeypatch.setenv("DEMO_AUTH_PROJECT_ID", ids["project"])
    app = create_app(session_factory=factory)
    client = TestClient(app)
    client.foundation_ids = ids
    return client


@pytest.fixture()
def foundation_client(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    with client:
        yield client


def test_every_response_has_request_id_and_errors_use_stable_envelope(foundation_client):
    response = foundation_client.get("/api/v1/health", headers={"x-request-id": "browser-123"})
    assert response.status_code == 200
    assert response.headers["x-request-id"] == "browser-123"

    unauthorized = foundation_client.get("/api/v1/projects")
    assert unauthorized.status_code == 401
    assert unauthorized.headers.get("x-request-id", "").startswith("req_")
    body = unauthorized.json()
    assert body["error"]["code"] == "UNAUTHORIZED"
    assert body["error"]["message"] == "Missing API key"
    assert body["error"]["requestId"] == unauthorized.headers["x-request-id"]
    # Existing SDK callers can continue reading detail during migration.
    assert body["detail"] == "Missing API key"


def test_api_v1_path_preserves_existing_api_key_compatibility(foundation_client):
    response = foundation_client.get(
        "/api/v1/projects",
        headers={"x-api-key": foundation_client.foundation_ids["api_key"]},
    )
    assert response.status_code == 200
    assert response.json()[0]["slug"] == "research"


def test_cors_allows_only_configured_frontend_origin(foundation_client):
    allowed = foundation_client.options(
        "/api/v1/health",
        headers={
            "Origin": DEFAULT_APP_ORIGIN,
            "Access-Control-Request-Method": "GET",
        },
    )
    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == DEFAULT_APP_ORIGIN
    assert allowed.headers["access-control-allow-credentials"] == "true"
    assert "*" not in allowed.headers["access-control-allow-origin"]

    denied = foundation_client.options(
        "/api/v1/health",
        headers={
            "Origin": "https://attacker.invalid",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert "access-control-allow-origin" not in denied.headers


def test_wildcard_app_origin_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("APP_ORIGIN", "*")
    factory = create_session_factory(f"sqlite:///{tmp_path / 'wildcard.db'}")
    create_tables(factory)
    app = create_app(session_factory=factory)
    client = TestClient(app)
    response = client.options(
        "/api/v1/health",
        headers={"Origin": "https://attacker.invalid", "Access-Control-Request-Method": "GET"},
    )
    assert "access-control-allow-origin" not in response.headers


def test_demo_token_maps_to_configured_tenant(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch, demo=True)
    with client:
        token = issue_demo_token(
            organization_id=client.foundation_ids["organization"],
            project_id=client.foundation_ids["project"],
            secret="test-demo-secret",
        )
        response = client.get("/api/v1/me", headers={"authorization": f"Bearer {token}"})
        assert response.status_code == 200
        assert response.json()["authType"] == "demo"
        assert response.json()["projectId"] == client.foundation_ids["project"]


def test_demo_token_is_rejected_when_production(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch, demo=True, production=True)
    with client:
        token = issue_demo_token(
            organization_id=client.foundation_ids["organization"],
            project_id=client.foundation_ids["project"],
            secret="test-demo-secret",
        )
        response = client.get("/api/v1/me", headers={"authorization": f"Bearer {token}"})
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "UNAUTHORIZED"


def test_expired_and_revoked_demo_tokens_are_rejected(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch, demo=True)
    with client:
        token = issue_demo_token(
            organization_id=client.foundation_ids["organization"],
            project_id=client.foundation_ids["project"],
            secret="test-demo-secret",
        )
        current_time = auth_module.time
        monkeypatch.setattr(auth_module, "time", lambda: current_time() + 7200)
        expired = client.get("/api/v1/me", headers={"authorization": f"Bearer {token}"})
        assert expired.status_code == 401

        # Restore verification time so revocation is tested independently of expiry.
        monkeypatch.setattr(auth_module, "time", current_time)
        fresh = issue_demo_token(
            organization_id=client.foundation_ids["organization"],
            project_id=client.foundation_ids["project"],
            secret="test-demo-secret",
        )
        assert revoke_demo_token(fresh) is True
        revoked = client.get("/api/v1/me", headers={"authorization": f"Bearer {fresh}"})
        assert revoked.status_code == 401
