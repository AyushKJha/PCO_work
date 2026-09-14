from __future__ import annotations

from fastapi.testclient import TestClient

from apps.api.auth import issue_api_key
from apps.api.db import create_session_factory, create_tables
from apps.api.main import create_app
from apps.api.models import Organization, Project


def _client(tmp_path):
    factory = create_session_factory(f"sqlite:///{tmp_path / 'onboarding.db'}")
    create_tables(factory)
    with factory.begin() as session:
        organization = Organization(name="Onboarding Org")
        session.add(organization)
        session.flush()
        secret, key = issue_api_key(organization_id=organization.id, name="founder")
        session.add(key)
        session.flush()
        project = Project(organization_id=organization.id, name="Existing", slug="existing")
        session.add(project)
        session.flush()
        project_secret, project_key = issue_api_key(organization_id=organization.id, project_id=project.id, name="project")
        session.add(project_key)
        ids = {"secret": secret, "project": project.id, "project_secret": project_secret}
    app = create_app(session_factory=factory)
    client = TestClient(app)
    client.ids = ids
    return client


def test_organization_can_create_project_and_empty_project_reports_setup(tmp_path):
    client = _client(tmp_path)
    headers = {"x-api-key": client.ids["secret"]}
    created = client.post("/api/v1/projects", headers=headers, json={"name": "New Agent", "slug": "new-agent"})
    assert created.status_code == 201
    project_id = created.json()["id"]
    onboarding = client.get(f"/api/v1/projects/{project_id}/onboarding", headers=headers)
    assert onboarding.status_code == 200
    assert onboarding.json()["stage"] == "PROJECT_CREATED"
    assert onboarding.json()["nextAction"] == "DEFINE_VERSION"
    assert onboarding.json()["counts"] == {"versions": 0, "traces": 0, "evalSuites": 0, "baselineRuns": 0}


def test_project_key_cannot_create_or_manage_keys(tmp_path):
    client = _client(tmp_path)
    headers = {"x-api-key": client.ids["project_secret"]}
    assert client.post("/api/v1/projects", headers=headers, json={"name": "Denied", "slug": "denied"}).status_code == 403
    project_id = client.ids["project"]
    assert client.post(f"/api/v1/projects/{project_id}/api-keys", headers=headers, json={"name": "nested"}).status_code == 403
    assert client.get(f"/api/v1/projects/{project_id}/api-keys", headers=headers).status_code == 403


def test_key_secret_is_revealed_once_and_revoke_is_idempotent(tmp_path):
    client = _client(tmp_path)
    headers = {"x-api-key": client.ids["secret"]}
    project_id = client.ids["project"]
    created = client.post(f"/api/v1/projects/{project_id}/api-keys", headers=headers, json={"name": "connector"})
    assert created.status_code == 201
    payload = created.json()
    assert payload["secret"].startswith("agp_")
    listed = client.get(f"/api/v1/projects/{project_id}/api-keys", headers=headers)
    assert listed.status_code == 200
    assert all("secret" not in item and "keyHash" not in item for item in listed.json())
    revoked = client.post(f"/api/v1/projects/{project_id}/api-keys/{payload['id']}/revoke", headers=headers)
    assert revoked.status_code == 200
    assert revoked.json()["revoked"] is True
    assert client.post(f"/api/v1/projects/{project_id}/api-keys/{payload['id']}/revoke", headers=headers).status_code == 200
