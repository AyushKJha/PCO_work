"""Metadata-only serializers and metric helpers for persisted OTLP spans."""

from __future__ import annotations

import base64
import json
from datetime import datetime, timezone
from typing import Any, Iterable


def attribute(attributes: Any, *keys: str, default: Any = None) -> Any:
    """Return the first present telemetry attribute without assuming its type."""
    values = attributes if isinstance(attributes, dict) else {}
    for key in keys:
        if key in values:
            return values[key]
    return default


def number(value: Any, default: float = 0.0) -> float:
    """Parse numeric OTel values, which commonly arrive as strings."""
    if isinstance(value, bool) or value is None:
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed == parsed and abs(parsed) != float("inf") else default


def integer(value: Any, default: int = 0) -> int:
    return int(number(value, float(default)))


def isoformat(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def duration_ms(start: datetime | None, end: datetime | None) -> float:
    if start is None or end is None:
        return 0.0
    return max(0.0, (end - start).total_seconds() * 1000)


def trace_metrics(trace: Any) -> dict[str, Any]:
    """Extract normalized metadata and measured usage from one persisted span."""
    attrs = trace.attributes if isinstance(trace.attributes, dict) else {}
    model = attribute(attrs, "gen_ai.response.model", "gen_ai.request.model", "gen_ai.model", "model")
    provider = attribute(attrs, "gen_ai.system", "gen_ai.provider.name", "gen_ai.provider", "provider")
    node_id = attribute(attrs, "agentpgo.node", "gen_ai.node.name", "node.id", "nodeId")
    if model is not None and provider is not None and "/" not in str(model):
        model = f"{provider}/{model}"
    input_tokens = integer(attribute(attrs, "gen_ai.usage.input_tokens", "gen_ai.usage.prompt_tokens", "input_tokens", "inputTokens"))
    output_tokens = integer(attribute(attrs, "gen_ai.usage.output_tokens", "gen_ai.usage.completion_tokens", "output_tokens", "outputTokens"))
    cost = number(attribute(attrs, "agentpgo.cost.usd", "gen_ai.usage.cost", "cost_usd", "costUsd", "cost"))
    status_code = getattr(trace, "status_code", None)
    status = "ok" if status_code == 1 else "error" if status_code == 2 else "unset"
    status_message = getattr(trace, "status_message", None)
    return {
        "id": str(trace.id),
        "traceId": str(trace.trace_id),
        "spanId": str(trace.span_id),
        "parentSpanId": str(trace.parent_span_id) if trace.parent_span_id else None,
        "nodeId": str(node_id) if node_id is not None else None,
        "model": str(model) if model is not None else None,
        "provider": str(provider) if provider is not None else None,
        "startedAt": isoformat(trace.start_time),
        "endedAt": isoformat(trace.end_time),
        "durationMs": duration_ms(trace.start_time, trace.end_time),
        "inputTokens": input_tokens,
        "outputTokens": output_tokens,
        "cost": cost,
        "status": status,
        "statusCode": status_code,
        "statusMessage": None if status_message == "[REDACTED]" else status_message,
        "receivedAt": isoformat(trace.received_at),
        "serviceName": trace.service_name,
    }


def serialize_trace(trace: Any) -> dict[str, Any]:
    """Serialize one span without prompt, output, raw span, or arbitrary attrs."""
    return trace_metrics(trace)


def serialize_trace_detail(trace_id: str, traces: Iterable[Any], project_id: str) -> dict[str, Any]:
    spans = sorted(tuple(traces), key=lambda row: (row.start_time is None, row.start_time, row.id))
    metrics = [trace_metrics(row) for row in spans]
    starts = [row.start_time for row in spans if row.start_time is not None]
    ends = [row.end_time for row in spans if row.end_time is not None]
    started = min(starts) if starts else None
    ended = max(ends) if ends else None
    return {
        "id": trace_id,
        "traceId": trace_id,
        "projectId": project_id,
        "spanCount": len(metrics),
        "startedAt": isoformat(started),
        "endedAt": isoformat(ended),
        "durationMs": duration_ms(started, ended),
        "spans": metrics,
    }


def encode_cursor(received_at: datetime, row_id: str) -> str:
    payload = json.dumps({"receivedAt": isoformat(received_at), "id": row_id}, separators=(",", ":"))
    return base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii").rstrip("=")


def decode_cursor(value: str) -> tuple[datetime, str]:
    if not value or len(value) > 512:
        raise ValueError("invalid trace cursor")
    try:
        decoded = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)).decode("utf-8")
        payload = json.loads(decoded)
        timestamp, row_id = payload["receivedAt"], payload["id"]
        if not isinstance(timestamp, str) or not isinstance(row_id, str) or not row_id or len(row_id) > 64:
            raise ValueError
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed, row_id
    except (ValueError, TypeError, KeyError, json.JSONDecodeError, UnicodeDecodeError):
        raise ValueError("invalid trace cursor") from None
