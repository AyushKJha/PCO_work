"""Connector contract tests for OTLP ingestion and profiling."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from apps.api.auth import issue_api_key
from apps.api.db import create_session_factory, create_tables
from apps.api.main import create_app
from apps.api.models import Organization, Project, Trace


@pytest.fixture()
def connector_client(tmp_path):
    factory = create_session_factory(f"sqlite:///{tmp_path / 'connector.db'}")
    create_tables(factory)
    with factory.begin() as session:
        first_org = Organization(name="First org")
        second_org = Organization(name="Second org")
        session.add_all([first_org, second_org])
        session.flush()
        first_project = Project(name="Research", slug="research", organization_id=first_org.id)
        second_project = Project(name="Other", slug="other", organization_id=second_org.id)
        session.add_all([first_project, second_project])
        session.flush()
        first_secret, first_key = issue_api_key(organization_id=first_org.id, project_id=first_project.id)
        second_secret, second_key = issue_api_key(organization_id=second_org.id, project_id=second_project.id)
        session.add_all([first_key, second_key])
        session.flush()
        ids = {
            "first_project": first_project.id,
            "second_project": second_project.id,
            "first_secret": first_secret,
            "second_secret": second_secret,
        }
    app = create_app(session_factory=factory)
    with TestClient(app) as client:
        client.connector_ids = ids
        yield client


def connector_payload(*, trace_id: str = "a" * 32, span_id: str = "b" * 16) -> dict:
    return {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": [
                        {"key": "service.name", "value": {"stringValue": "open-deep-research"}},
                        {"key": "deployment.environment", "value": {"stringValue": "test"}},
                    ]
                },
                "scopeSpans": [
                    {
                        "scope": {"name": "@agentpgo/sdk", "version": "1.0.0"},
                        "spans": [
                            {
                                "traceId": trace_id,
                                "spanId": span_id,
                                "name": "agent.node.researcher",
                                "kind": 1,
                                "startTimeUnixNano": "1720000000000000000",
                                "endTimeUnixNano": "1720000003810000000",
                                "attributes": [
                                    {"key": "agentpgo.node", "value": {"stringValue": "researcher"}},
                                    {"key": "gen_ai.system", "value": {"stringValue": "openai"}},
                                    {"key": "gen_ai.request.model", "value": {"stringValue": "gpt-5.6-sol"}},
                                    {"key": "gen_ai.response.model", "value": {"stringValue": "gpt-5.6-sol"}},
                                    {"key": "gen_ai.usage.input_tokens", "value": {"intValue": "18342"}},
                                    {"key": "gen_ai.usage.output_tokens", "value": {"intValue": "1282"}},
                                    {"key": "agentpgo.cost.usd", "value": {"doubleValue": 0.382}},
                                ],
                                "status": {"code": 1},
                            }
                        ],
                    }
                ],
            }
        ]
    }


def auth(client: TestClient, which: str = "first_secret") -> dict[str, str]:
    return {"x-api-key": client.connector_ids[which]}


def test_connector_otlp_payload_is_persisted_with_model_node_usage_and_latency(connector_client):
    response = connector_client.post("/v1/otlp/v1/traces", headers=auth(connector_client), json=connector_payload())

    assert response.status_code == 200
    assert response.json() == {"accepted": 1, "rejected": 0}

    with connector_client.app.state.session_factory() as session:
        span = session.query(Trace).one()
        assert span.service_name == "open-deep-research"
        assert span.attributes["agentpgo.node"] == "researcher"
        assert span.attributes["gen_ai.request.model"] == "gpt-5.6-sol"
        assert span.attributes["gen_ai.usage.input_tokens"] == "18342"
        assert span.attributes["gen_ai.usage.output_tokens"] == "1282"
        assert span.attributes["agentpgo.cost.usd"] == 0.382
        assert (span.end_time - span.start_time).total_seconds() * 1000 == pytest.approx(3810.0)


def test_connector_replay_is_idempotent_and_profile_counts_one_call(connector_client):
    payload = connector_payload()
    headers = auth(connector_client)

    first = connector_client.post("/v1/traces", headers=headers, json=payload)
    replay = connector_client.post("/v1/traces", headers=headers, json=payload)
    profile = connector_client.get(
        f"/v1/projects/{connector_client.connector_ids['first_project']}/profile",
        headers=headers,
    )

    assert first.json()["accepted"] == 1
    assert replay.json()["accepted"] == 0
    assert profile.status_code == 200
    assert profile.json()["runs_observed"] == 1
    assert profile.json()["model_calls"] == 1
    assert profile.json()["p50_latency_ms"] == pytest.approx(3810.0)
    assert profile.json()["p95_latency_ms"] == pytest.approx(3810.0)


def test_trace_list_is_paginated_and_metadata_only(connector_client):
    headers = auth(connector_client)
    project_id = connector_client.connector_ids["first_project"]
    first = connector_payload(trace_id="a" * 32, span_id="b" * 16)
    second = connector_payload(trace_id="c" * 32, span_id="d" * 16)
    assert connector_client.post("/v1/traces", headers=headers, json=first).json()["accepted"] == 1
    assert connector_client.post("/v1/traces", headers=headers, json=second).json()["accepted"] == 1

    page = connector_client.get(
        f"/api/v1/projects/{project_id}/traces?limit=1",
        headers=headers,
    )
    assert page.status_code == 200
    assert len(page.json()["data"]) == 1
    assert page.json()["page"]["nextCursor"]
    item = page.json()["data"][0]
    assert item["traceId"] in {"a" * 32, "c" * 32}
    assert item["nodeId"] == "researcher"
    assert item["model"] == "openai/gpt-5.6-sol"
    assert item["provider"] == "openai"
    assert item["inputTokens"] == 18342
    assert item["outputTokens"] == 1282
    assert item["cost"] == pytest.approx(0.382)
    assert item["durationMs"] == pytest.approx(3810.0)
    assert "rawSpan" not in item
    assert "attributes" not in item

    next_page = connector_client.get(
        f"/v1/projects/{project_id}/traces",
        params={"limit": 1, "cursor": page.json()["page"]["nextCursor"]},
        headers=headers,
    )
    assert next_page.status_code == 200
    assert len(next_page.json()["data"]) == 1
    assert next_page.json()["data"][0]["traceId"] != item["traceId"]
    assert next_page.json()["page"]["nextCursor"] is None


def test_trace_detail_groups_spans_without_exposing_content(connector_client):
    headers = auth(connector_client)
    project_id = connector_client.connector_ids["first_project"]
    trace_id = "e" * 32
    first = connector_payload(trace_id=trace_id, span_id="f" * 16)
    second = connector_payload(trace_id=trace_id, span_id="1" * 16)
    assert connector_client.post("/v1/traces", headers=headers, json=first).json()["accepted"] == 1
    assert connector_client.post("/v1/traces", headers=headers, json=second).json()["accepted"] == 1

    detail = connector_client.get(
        f"/api/v1/projects/{project_id}/traces/{trace_id}",
        headers=headers,
    )
    assert detail.status_code == 200
    assert detail.json()["traceId"] == trace_id
    assert detail.json()["spanCount"] == 2
    assert detail.json()["durationMs"] == pytest.approx(3810.0)
    assert {span["spanId"] for span in detail.json()["spans"]} == {"f" * 16, "1" * 16}
    assert all("rawSpan" not in span and "attributes" not in span for span in detail.json()["spans"])


def test_profile_reports_persisted_usage_cost_latency_and_breakdowns(connector_client):
    headers = auth(connector_client)
    project_id = connector_client.connector_ids["first_project"]
    assert connector_client.post("/v1/traces", headers=headers, json=connector_payload()).json()["accepted"] == 1
    second = connector_payload(trace_id="2" * 32, span_id="3" * 16)
    assert connector_client.post("/v1/traces", headers=headers, json=second).json()["accepted"] == 1

    profile = connector_client.get(
        f"/v1/projects/{project_id}/profile",
        headers=headers,
    )
    assert profile.status_code == 200
    payload = profile.json()
    assert payload["runs_observed"] == 2
    assert payload["model_calls"] == 2
    assert payload["input_tokens"] == 36684
    assert payload["output_tokens"] == 2564
    assert payload["total_tokens"] == 39248
    assert payload["total_cost_usd"] == pytest.approx(0.764)
    assert payload["cost_per_request_usd"] == pytest.approx(0.382)
    assert payload["avg_cost_per_call_usd"] == pytest.approx(0.382)
    assert payload["avg_latency_ms"] == pytest.approx(3810.0)
    assert payload["by_node"]["researcher"]["calls"] == 2
    assert payload["by_model"]["openai/gpt-5.6-sol"]["cost"] == pytest.approx(0.764)


def test_connector_project_key_cannot_ingest_or_profile_another_project(connector_client):
    headers = auth(connector_client)
    payload = connector_payload(trace_id="c" * 32, span_id="d" * 16)

    ingest = connector_client.post(
        f"/v1/traces?project_id={connector_client.connector_ids['second_project']}",
        headers=headers,
        json=payload,
    )
    profile = connector_client.get(
        f"/v1/projects/{connector_client.connector_ids['second_project']}/profile",
        headers=headers,
    )

    assert ingest.status_code == 403
    assert profile.status_code == 403
