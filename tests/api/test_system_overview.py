"""Authenticated contract tests for the tenant-scoped system overview."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from apps.api.auth import issue_api_key
from apps.api.db import create_session_factory, create_tables
from apps.api.main import create_app
from apps.api.models import Organization, Project


@pytest.fixture()
def client(tmp_path):
    factory = create_session_factory(f"sqlite:///{tmp_path / 'system-overview.db'}")
    create_tables(factory)
    with factory.begin() as session:
        organization = Organization(name="Overview test")
        session.add(organization)
        session.flush()
        project = Project(name="Research", slug="research", organization_id=organization.id)
        session.add(project)
        session.flush()
        secret, key = issue_api_key(organization_id=organization.id, project_id=project.id)
        session.add(key)
        session.flush()
        ids = (organization.id, project.id, secret)
    app = create_app(session_factory=factory)
    with TestClient(app) as test_client:
        test_client.test_tenant = ids
        yield test_client


def test_system_overview_is_authenticated(client: TestClient):
    response = client.get("/api/v1/system/overview")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"


def test_system_overview_is_tenant_scoped_and_metadata_only(client: TestClient):
    response = client.get(
        "/api/v1/system/overview",
        headers={"x-api-key": client.test_tenant[2]},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["scope"] == {
        "organizationId": client.test_tenant[0],
        "projectId": client.test_tenant[1],
    }
    assert body["summary"] == {"projectCount": 1, "versionCount": 0, "traceCount": 0}
    assert [project["id"] for project in body["projects"]] == [client.test_tenant[1]]
    assert body["capabilities"]["otlpIngestion"] is True
    assert body["capabilities"]["promptOutputStorage"] is False
    assert body["dependencies"]["database"] == "ready"
    # The overview must not become a side channel for credentials or content.
    assert "DATABASE_URL" not in body
    assert "promptTemplate" not in body
    assert "completion" not in str(body).lower()
