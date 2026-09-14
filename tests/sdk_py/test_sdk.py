from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

# The Python SDK is a src-layout package inside the monorepo.  This keeps the
# tests runnable from a checkout while the editable package is installed.
sys.path.insert(0, str(Path(__file__).parents[2] / "packages" / "sdk-py" / "src"))

from agentpgo import AgentPGOClient, ExportResult, sanitize_attributes  # noqa: E402


def run(coro):
    return asyncio.run(coro)


def test_sync_and_async_contexts_export_canonical_otlp_and_preserve_parent() -> None:
    requests: list[tuple[str, dict, dict]] = []

    async def transport(endpoint, headers, payload):
        requests.append((endpoint, headers, payload))
        return 200

    client = AgentPGOClient(
        api_key="project-key",
        endpoint="https://collector.example/v1/traces",
        service_name="research-agent",
        service_version="1.2.0",
        environment="test",
        project_id="project-id",
        transport=transport,
    )

    with client.trace(node="outer", model="openai/gpt-5.6-sol", provider="openai"):
        with client.trace(node="inner", model="anthropic/claude-sonnet", provider="anthropic"):
            pass

    async def operation() -> None:
        async with client.trace(node="async", model="google/gemini", provider="google"):
            pass

    run(operation())
    result = run(client.flush())

    assert result == ExportResult(sent=3, dropped=0)
    assert len(requests) == 1
    endpoint, headers, payload = requests[0]
    assert endpoint.endswith("/v1/traces")
    assert headers["authorization"] == "Bearer project-key"
    assert headers["content-type"] == "application/json"
    assert headers["X-AgentPGO-Project-ID"] == "project-id"
    assert set(payload) == {"resourceSpans"}
    resource = payload["resourceSpans"][0]["resource"]["attributes"]
    assert {item["key"]: item["value"] for item in resource}["service.name"] == {"stringValue": "research-agent"}
    scopes = payload["resourceSpans"][0]["scopeSpans"]
    spans = scopes[0]["spans"]
    assert len(spans) == 3
    child, outer, async_span = spans
    assert child["parentSpanId"] == outer["spanId"]
    assert async_span.get("parentSpanId") is None
    attrs = {item["key"]: item["value"] for item in child["attributes"]}
    assert attrs["agentpgo.node"] == {"stringValue": "inner"}
    assert attrs["gen_ai.request.model"] == {"stringValue": "anthropic/claude-sonnet"}
    assert attrs["gen_ai.system"] == {"stringValue": "anthropic"}


def test_sync_and_async_decorators_record_success_and_error_without_capturing_content() -> None:
    spans = []
    client = AgentPGOClient(enabled=True, endpoint="", on_export=spans.extend)

    @client.trace(node="sync", model="test/model")
    def sync_operation() -> str:
        return "customer output must not be recorded"

    @client.trace(node="async", model="test/model")
    async def async_operation() -> str:
        raise RuntimeError("prompt=customer secret output=private")

    assert sync_operation() == "customer output must not be recorded"
    try:
        run(async_operation())
    except RuntimeError:
        pass
    else:  # pragma: no cover - assertion documents the decorator contract
        raise AssertionError("the decorated exception must propagate")

    assert len(spans) == 2
    assert spans[0].status["code"] == "ok"
    assert spans[1].status["code"] == "error"
    serialized = json.dumps([span.__dict__ for span in spans])
    assert "customer output" not in serialized
    assert "customer secret" not in serialized
    assert "private" not in serialized


def test_nested_context_propagates_trace_id_and_explicit_attributes() -> None:
    spans = []
    client = AgentPGOClient(enabled=True, endpoint="", on_export=spans.extend)

    with client.trace(
        node="root",
        model="model-a",
        trace_id="a" * 32,
        attributes={"retry_count": 2, "prompt": "must be removed"},
    ):
        with client.trace(node="child", model="model-b"):
            pass

    assert [span.trace_id for span in spans] == ["a" * 32, "a" * 32]
    child, root = spans
    assert child.parent_span_id == root.span_id
    assert root.attributes["retry_count"] == 2
    assert "prompt" not in root.attributes


def test_queue_is_bounded_and_export_failures_are_fail_open() -> None:
    requests = []

    async def failing_transport(endpoint, headers, payload):
        requests.append(payload)
        raise OSError("collector unavailable")

    client = AgentPGOClient(endpoint="https://collector.example/v1/traces", max_queue_size=2, transport=failing_transport)
    for node in ("one", "two", "three"):
        with client.trace(node=node, model="test/model"):
            pass

    result = run(client.flush())
    assert result.sent == 0
    assert result.dropped == 3  # one evicted by the bound, two failed exports
    assert isinstance(result.error, OSError)
    assert len(requests) == 1


def test_disabled_client_never_calls_transport_and_shutdown_flushes() -> None:
    calls = []

    async def transport(endpoint, headers, payload):
        calls.append(payload)
        return 200

    client = AgentPGOClient(enabled=False, transport=transport)
    with client.trace(node="ignored", model="test/model"):
        pass
    result = run(client.shutdown())
    assert result == ExportResult(sent=0, dropped=0)
    assert calls == []


def test_content_like_attributes_are_removed_but_usage_counters_remain() -> None:
    attrs = sanitize_attributes({
        "input": "private prompt",
        "output": "private completion",
        "message": "private message",
        "response": "private response",
        "tool_input": "private tool args",
        "gen_ai.usage.input_tokens": 12,
        "gen_ai.usage.output_tokens": 4,
    })

    assert "input" not in attrs
    assert "output" not in attrs
    assert "message" not in attrs
    assert "response" not in attrs
    assert "tool_input" not in attrs
    assert attrs["gen_ai.usage.input_tokens"] == 12
    assert attrs["gen_ai.usage.output_tokens"] == 4


def test_sync_flush_helper_exports_without_an_event_loop() -> None:
    spans = []
    client = AgentPGOClient(endpoint="https://collector.example/v1/traces", on_export=spans.extend)
    with client.trace(node="sync", model="test/model"):
        pass

    # on_export is synchronous, so this helper makes the documented sync path
    # explicit while the async `flush()` remains available to async callers.
    result = client.flush_sync()

    assert result == ExportResult(sent=0, dropped=0)
    assert len(spans) == 1
