from __future__ import annotations

from fastapi.testclient import TestClient

from apps.api.auth import issue_api_key
from apps.api.db import create_session_factory, create_tables
from apps.api.main import create_app
from apps.api.models import Organization, Project


def _client(tmp_path):
    factory = create_session_factory(f"sqlite:///{tmp_path / 'eval-suites.db'}")
    create_tables(factory)
    with factory.begin() as session:
        first_org = Organization(name="First")
        second_org = Organization(name="Second")
        session.add_all([first_org, second_org])
        session.flush()
        first_project = Project(name="Research", slug="research", organization_id=first_org.id)
        second_project = Project(name="Private", slug="private", organization_id=second_org.id)
        session.add_all([first_project, second_project])
        session.flush()
        first_secret, first_key = issue_api_key(organization_id=first_org.id, name="first")
        second_secret, second_key = issue_api_key(organization_id=second_org.id, name="second")
        session.add_all([first_key, second_key])
        ids = {
            "first_secret": first_secret,
            "first_project": first_project.id,
            "second_secret": second_secret,
            "second_project": second_project.id,
        }
    client = TestClient(create_app(session_factory=factory))
    client.ids = ids
    return client


def test_eval_suite_lifecycle_persists_run_and_cases(tmp_path):
    client = _client(tmp_path)
    headers = {"x-api-key": client.ids["first_secret"]}
    project_id = client.ids["first_project"]

    created = client.post(
        f"/api/v1/projects/{project_id}/eval-suites",
        headers=headers,
        json={
            "name": "Research acceptance",
            "metadata": {"source": "fixture"},
            "cases": [
                {"id": "case-1", "input": {"question": "one"}, "expected": {"answer": "1"}},
                {"id": "case-2", "input": {"question": "two"}, "expected": {"answer": "2"}},
            ],
            "graders": [{"name": "exact", "kind": "exact_match", "config": {"path": "answer"}}],
        },
    )
    assert created.status_code == 201, created.text
    suite = created.json()
    assert suite["projectId"] == project_id
    assert suite["caseCount"] == 2
    assert suite["graderCount"] == 1
    # Content remains metadata-only unless explicitly enabled.
    assert "input" not in suite["cases"][0]

    listed = client.get(f"/api/v1/projects/{project_id}/eval-suites", headers=headers)
    assert listed.status_code == 200
    assert listed.json()["data"][0]["id"] == suite["id"]
    detailed = client.get(f"/api/v1/eval-suites/{suite['id']}", headers=headers)
    assert detailed.status_code == 200
    assert detailed.json()["metadata"] == {"source": "fixture"}

    started = client.post(
        f"/api/v1/eval-suites/{suite['id']}/runs",
        headers=headers,
        json={"candidateConfig": {"model": "fake/model"}, "sampleCount": 1},
    )
    assert started.status_code == 202, started.text
    run = started.json()
    assert run["status"] == "QUEUED"
    assert run["evalSuiteId"] == suite["id"]
    assert run["caseCount"] == 1

    status = client.get(f"/api/v1/eval-runs/{run['runId']}", headers=headers)
    assert status.status_code == 200
    assert status.json()["candidateConfig"] == {"model": "fake/model"}
    cases = client.get(f"/api/v1/eval-runs/{run['runId']}/cases", headers=headers)
    assert cases.status_code == 200
    assert len(cases.json()["data"]) == 1
    assert cases.json()["data"][0]["status"] == "PENDING"
    assert cases.json()["cases"] == cases.json()["data"]


def test_eval_suite_and_run_are_tenant_isolated(tmp_path):
    client = _client(tmp_path)
    first_headers = {"x-api-key": client.ids["first_secret"]}
    second_headers = {"x-api-key": client.ids["second_secret"]}
    project_id = client.ids["first_project"]
    created = client.post(
        f"/api/v1/projects/{project_id}/eval-suites",
        headers=first_headers,
        json={"name": "Private suite", "cases": [{"id": "only-case", "input": {}, "expected": {}}]},
    )
    assert created.status_code == 201
    suite_id = created.json()["id"]
    assert client.get(f"/api/v1/eval-suites/{suite_id}", headers=second_headers).status_code == 404
    assert client.post(f"/api/v1/eval-suites/{suite_id}/runs", headers=second_headers, json={}).status_code == 404
    assert client.get(f"/api/v1/projects/{project_id}/eval-suites", headers=second_headers).status_code == 403


def test_empty_eval_suite_cannot_start_run(tmp_path):
    client = _client(tmp_path)
    headers = {"x-api-key": client.ids["first_secret"]}
    created = client.post(
        f"/api/v1/projects/{client.ids['first_project']}/eval-suites",
        headers=headers,
        json={"name": "Empty"},
    )
    assert created.status_code == 201
    response = client.post(f"/api/v1/eval-suites/{created.json()['id']}/runs", headers=headers, json={})
    assert response.status_code == 422
