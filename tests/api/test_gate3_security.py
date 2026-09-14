from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from apps.api.auth import issue_api_key
from apps.api.db import create_session_factory, create_tables
from apps.api.main import create_app
from apps.api.models import ApiKey, Organization, Project, Trace


@pytest.fixture()
def gate3_client(tmp_path):
    factory = create_session_factory(f"sqlite:///{tmp_path / 'gate3.db'}")
    create_tables(factory)
    with factory.begin() as session:
        organization = Organization(name="Acme")
        session.add(organization)
        session.flush()
        first = Project(name="First", slug="first", organization_id=organization.id)
        second = Project(name="Second", slug="second", organization_id=organization.id)
        session.add_all([first, second])
        session.flush()
        first_secret, first_key = issue_api_key(organization_id=organization.id, project_id=first.id)
        second_secret, second_key = issue_api_key(organization_id=organization.id, project_id=second.id)
        session.add_all([first_key, second_key])
        session.flush()
        ids = {"first": first.id, "second": second.id, "first_secret": first_secret, "second_secret": second_secret}
    app = create_app(session_factory=factory)
    with TestClient(app) as client:
        client.gate3_ids = ids
        yield client


def _span(span_id: str = "0123456789abcdef") -> dict:
    return {
        "traceId": "0123456789abcdef0123456789abcdef",
        "spanId": span_id,
        "name": "answer",
        "attributes": [
            {"key": "gen_ai.prompt", "value": {"stringValue": "do not retain this"}},
            {"key": "gen_ai.usage.input_tokens", "value": {"intValue": "2"}},
        ],
    }


def _payload(spans: list[dict]) -> dict:
    return {"resourceSpans": [{"resource": {"attributes": []}, "scopeSpans": [{"spans": spans}]}]}


def test_duplicate_spans_in_one_request_are_idempotent_and_content_is_redacted(gate3_client):
    response = gate3_client.post(
        "/v1/traces",
        headers={"x-api-key": gate3_client.gate3_ids["first_secret"]},
        json=_payload([_span(), _span()]),
    )
    assert response.status_code == 200
    assert response.json()["accepted"] == 1
    with gate3_client.app.state.session_factory() as session:
        row = session.query(Trace).one()
        assert row.raw_span == {}
        assert row.attributes["gen_ai.prompt"] == "[REDACTED]"
        assert row.attributes["gen_ai.usage.input_tokens"] == "2"


def test_project_key_cannot_read_or_write_another_project(gate3_client):
    headers = {"x-api-key": gate3_client.gate3_ids["first_secret"]}
    assert gate3_client.get(f"/v1/projects/{gate3_client.gate3_ids['second']}/profile", headers=headers).status_code == 403
    assert gate3_client.post(
        "/v1/baselines/run",
        headers=headers,
        json={"project_id": gate3_client.gate3_ids["second"], "config": {"candidates": []}},
    ).status_code == 403
