from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from apps.api.auth import issue_api_key
from apps.api.db import create_session_factory, create_tables
from apps.api.main import create_app
from apps.api.models import Organization, Project


@pytest.fixture()
def client(tmp_path):
    factory = create_session_factory(f"sqlite:///{tmp_path / 'project.db'}")
    create_tables(factory)
    with factory.begin() as session:
        organization = Organization(name="Acme")
        session.add(organization)
        session.flush()
        project = Project(name="Research", slug="research", organization_id=organization.id)
        session.add(project)
        session.flush()
        secret, key = issue_api_key(organization_id=organization.id, project_id=project.id, name="test")
        session.add(key)
        project_id = project.id
    app = create_app(session_factory=factory)
    with TestClient(app) as test_client:
        test_client.project_id = project_id
        test_client.headers.update({"x-api-key": secret})
        yield test_client


def test_project_version_graph_settings_and_layout_are_persisted(client: TestClient):
    body = {
        "version": "v1",
        "environment": "STAGING",
        "nodes": [
            {"id": "planner", "name": "Planner", "role": "plan", "baselineModel": "openai/gpt-5.6-sol", "currentModel": "openai/gpt-5.6-sol", "optimizedModel": "openai/gpt-5.6-luna"},
            {"id": "writer", "name": "Writer", "role": "write", "baselineModel": "openai/gpt-5.6-sol", "currentModel": "openai/gpt-5.6-sol", "optimizedModel": "openai/gpt-5.6-luna"},
        ],
        "edges": [{"id": "e1", "from": "planner", "to": "writer"}],
        "metrics": {"baselineCost": 0.382, "optimizedCost": 0.141, "baselineQuality": 92.4, "optimizedQuality": 92.7},
    }
    version = client.post(f"/api/v1/projects/{client.project_id}/versions", json=body)
    assert version.status_code == 201, version.text
    assert version.json()["nodes"][0]["id"] == "planner"
    assert "promptTemplate" in version.json()["nodes"][0]
    assert version.json()["nodes"][0]["promptTemplate"] is None

    detail = client.get(f"/api/v1/projects/{client.project_id}")
    assert detail.status_code == 200
    assert detail.json()["version"] == "v1"
    assert detail.json()["baselineCost"] == pytest.approx(0.382)
    assert detail.json()["edges"][0]["from"] == "planner"

    settings = client.get(f"/api/v1/projects/{client.project_id}/settings")
    assert settings.status_code == 200
    assert settings.json()["qualityTolerancePp"] == pytest.approx(1.0)
    patched = client.patch(f"/api/v1/projects/{client.project_id}/settings", json={"qualityTolerancePp": 0.5, "confidencePct": 97})
    assert patched.status_code == 200
    assert patched.json()["qualityTolerancePct"] == pytest.approx(0.5)
    assert patched.json()["confidencePct"] == pytest.approx(97)

    missing_revision = client.post(f"/api/v1/projects/{client.project_id}/layout", json={"nodes": {"planner": {"x": 10, "y": 20}}})
    assert missing_revision.status_code == 409
    layout = client.post(f"/api/v1/projects/{client.project_id}/layout", json={"revision": 0, "versionId": version.json()["id"], "nodes": {"planner": {"x": 10, "y": 20}}})
    assert layout.status_code == 200, layout.text
    assert layout.json()["revision"] == 1
    current = client.get(f"/api/v1/projects/{client.project_id}/layout")
    assert current.status_code == 200
    assert current.json()["nodes"]["planner"]["x"] == 10


def test_graph_rejects_dangling_edges(client: TestClient):
    response = client.post(
        f"/api/v1/projects/{client.project_id}/versions",
        json={
            "version": "bad",
            "nodes": [{"id": "planner", "baselineModel": "openai/gpt-5.6-sol"}],
            "edges": [{"id": "e1", "from": "planner", "to": "missing"}],
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
