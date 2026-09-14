"""Small, fail-open Python connector for AgentPGO's OTLP ingestion API.

The connector deliberately records metadata only.  It never serializes the
operation's return value or arguments, and sensitive attribute names are
removed before a span can leave the process.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import re
import secrets
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from contextvars import ContextVar
from dataclasses import dataclass
from functools import wraps
from typing import Any, Callable, Mapping, Protocol, TypeVar


SCHEMA_VERSION = "1.0.0"
INSTRUMENTATION_NAME = "@agentpgo/sdk"
DEFAULT_ENDPOINT = "https://api.agentpgo.dev/v1/traces"
DEFAULT_SERVICE = "agentpgo-client"

_trace_context: ContextVar[str | None] = ContextVar("agentpgo_trace_id", default=None)
_span_context: ContextVar[str | None] = ContextVar("agentpgo_span_id", default=None)
_SENSITIVE = (
    "prompt",
    "completion",
    "password",
    "secret",
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "raw_input",
    "raw_output",
    "raw_content",
    "raw_text",
    "document_text",
)
_CONTENT_KEY = re.compile(
    r"(?:^|[._-])(prompt|input|completion|output|content|message|messages|response|"
    r"tool[_-]?input|tool[_-]?output|secret|password|authorization|api[-_]?key)(?:$|[._-])",
    re.IGNORECASE,
)

T = TypeVar("T")


class Transport(Protocol):
    def __call__(self, endpoint: str, headers: Mapping[str, str], payload: dict[str, Any]) -> Any:
        """Send a payload and return an HTTP status or response-like object."""


@dataclass(frozen=True)
class ExportResult:
    sent: int
    dropped: int
    error: Exception | None = None


@dataclass
class SpanRecord:
    trace_id: str
    span_id: str
    parent_span_id: str | None
    name: str
    start_time_unix_nano: str
    end_time_unix_nano: str | None
    attributes: dict[str, Any]
    status: dict[str, Any]


def _is_sensitive(key: str) -> bool:
    normalized = key.lower().replace(" ", "_")
    # Usage counters contain the words input/output but are safe metadata.
    if normalized.endswith(("tokens", "token_count", "token_counts")):
        return False
    if _CONTENT_KEY.search(normalized):
        return True
    compact = normalized.replace("-", "_").replace(".", "_")
    return any(part in compact for part in _SENSITIVE)


def sanitize_attributes(values: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Keep only OTLP primitive values and remove content/credential fields."""

    output: dict[str, Any] = {}
    for key, value in (values or {}).items():
        if _is_sensitive(str(key)):
            continue
        if isinstance(value, (str, bool)):
            output[str(key)] = value
        elif isinstance(value, (int, float)) and not isinstance(value, bool) and value == value:
            output[str(key)] = value
        elif isinstance(value, (list, tuple)) and all(
            isinstance(item, (str, bool)) or (isinstance(item, (int, float)) and not isinstance(item, bool) and item == item)
            for item in value
        ):
            output[str(key)] = list(value)
    return output


def _now_ns() -> str:
    return str(time.time_ns())


def _string_value(value: Any) -> dict[str, Any]:
    if isinstance(value, bool):
        return {"boolValue": value}
    if isinstance(value, int):
        return {"intValue": str(value)}
    if isinstance(value, float):
        return {"doubleValue": value}
    if isinstance(value, list):
        return {"arrayValue": {"values": [_string_value(item) for item in value]}}
    return {"stringValue": str(value)}


def _attributes_to_otlp(values: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [{"key": key, "value": _string_value(value)} for key, value in sanitize_attributes(values).items()]


def _new_id(byte_count: int) -> str:
    return secrets.token_hex(byte_count)


def to_otlp_payload(spans: list[SpanRecord], *, service_name: str, service_version: str | None = None, environment: str | None = None, resource_attributes: Mapping[str, Any] | None = None) -> dict[str, Any]:
    resource: dict[str, Any] = {
        "service.name": service_name,
        "agentpgo.schema.version": SCHEMA_VERSION,
    }
    if service_version:
        resource["service.version"] = service_version
    if environment:
        resource["deployment.environment"] = environment
    resource.update(sanitize_attributes(resource_attributes))
    otlp_spans = []
    for span in spans:
        code = {"unset": 0, "ok": 1, "error": 2}.get(span.status.get("code", "unset"), 0)
        otlp_spans.append(
            {
                "traceId": span.trace_id,
                "spanId": span.span_id,
                **({"parentSpanId": span.parent_span_id} if span.parent_span_id else {}),
                "name": span.name[:256],
                "kind": 1,
                "startTimeUnixNano": span.start_time_unix_nano,
                **({"endTimeUnixNano": span.end_time_unix_nano} if span.end_time_unix_nano else {}),
                "attributes": _attributes_to_otlp(span.attributes),
                "events": [],
                "status": {"code": code},
            }
        )
    return {
        "resourceSpans": [
            {
                "resource": {"attributes": _attributes_to_otlp(resource)},
                "scopeSpans": [{"scope": {"name": INSTRUMENTATION_NAME, "version": SCHEMA_VERSION}, "spans": otlp_spans}],
            }
        ]
    }


async def _http_transport(endpoint: str, headers: Mapping[str, str], payload: dict[str, Any]) -> int:
    """Use only the standard library so the SDK adds no runtime dependency."""

    def send() -> int:
        request = urllib.request.Request(endpoint, data=json.dumps(payload).encode("utf-8"), headers=dict(headers), method="POST")
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                return int(response.status)
        except urllib.error.HTTPError as error:
            return int(error.code)

    return await asyncio.to_thread(send)


class TraceHandle:
    """A span that works as sync/async context manager and decorator."""

    def __init__(self, client: "AgentPGOClient", *, node: str, model: str, provider: str | None, trace_id: str | None, parent_span_id: str | None, attributes: Mapping[str, Any] | None):
        inherited_trace = _trace_context.get()
        inherited_parent = _span_context.get()
        self.client = client
        self.node = node
        self.model = model
        self.provider = provider
        self._attributes = dict(attributes or {})
        self.record = SpanRecord(
            trace_id=trace_id or inherited_trace or _new_id(16),
            span_id=_new_id(8),
            parent_span_id=parent_span_id or inherited_parent,
            name=f"agent.node.{node}",
            start_time_unix_nano=_now_ns(),
            end_time_unix_nano=None,
            attributes=sanitize_attributes(
                {
                    **(attributes or {}),
                    "agentpgo.node": node,
                    "gen_ai.request.model": model,
                    **({"gen_ai.system": provider} if provider else {}),
                }
            ),
            status={"code": "unset"},
        )
        self._trace_token = None
        self._span_token = None
        self._ended = False

    @property
    def trace_id(self) -> str:
        return self.record.trace_id

    @property
    def span_id(self) -> str:
        return self.record.span_id

    @property
    def parent_span_id(self) -> str | None:
        return self.record.parent_span_id

    def __enter__(self) -> "TraceHandle":
        self._trace_token = _trace_context.set(self.record.trace_id)
        self._span_token = _span_context.set(self.record.span_id)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self._finish("error" if exc_type else "ok")
        self._reset_context()

    async def __aenter__(self) -> "TraceHandle":
        return self.__enter__()

    async def __aexit__(self, exc_type, exc, tb) -> None:
        self.__exit__(exc_type, exc, tb)

    def _reset_context(self) -> None:
        if self._span_token is not None:
            _span_context.reset(self._span_token)
            self._span_token = None
        if self._trace_token is not None:
            _trace_context.reset(self._trace_token)
            self._trace_token = None

    def _finish(self, code: str) -> None:
        if self._ended:
            return
        self._ended = True
        self.record.end_time_unix_nano = _now_ns()
        self.record.status = {"code": code}
        self.client.capture(self.record)

    def __call__(self, function: Callable[..., T]) -> Callable[..., T]:
        if inspect.iscoroutinefunction(function):
            @wraps(function)
            async def async_wrapper(*args, **kwargs):
                async with self.client.trace(
                    node=self.record.attributes["agentpgo.node"],
                    model=self.record.attributes["gen_ai.request.model"],
                    provider=self.record.attributes.get("gen_ai.system"),
                ):
                    return await function(*args, **kwargs)

            return async_wrapper  # type: ignore[return-value]

        @wraps(function)
        def sync_wrapper(*args, **kwargs):
            with self.client.trace(
                node=self.record.attributes["agentpgo.node"],
                model=self.record.attributes["gen_ai.request.model"],
                provider=self.record.attributes.get("gen_ai.system"),
            ):
                return function(*args, **kwargs)

        return sync_wrapper  # type: ignore[return-value]


class AgentPGOClient:
    def __init__(self, *, api_key: str | None = None, endpoint: str = DEFAULT_ENDPOINT, service_name: str = DEFAULT_SERVICE, service_version: str | None = None, environment: str | None = None, project_id: str | None = None, enabled: bool = True, max_queue_size: int = 1000, flush_interval_s: float = 0, transport: Transport | None = None, resource_attributes: Mapping[str, Any] | None = None, on_export: Callable[[list[SpanRecord]], Any] | None = None):
        if max_queue_size < 1:
            raise ValueError("max_queue_size must be positive")
        self.api_key = api_key
        self.endpoint = endpoint
        self.service_name = service_name
        self.service_version = service_version
        self.environment = environment
        self.project_id = project_id
        self.enabled = enabled
        self.max_queue_size = max_queue_size
        self.transport = transport or _http_transport
        self.resource_attributes = resource_attributes or {}
        self.on_export = on_export
        self._queue: deque[SpanRecord] = deque()
        self._queue_lock = threading.Lock()
        self._flush_lock = asyncio.Lock()
        self._dropped_pending = 0
        self._closed = False
        self._periodic_task: asyncio.Task | None = None
        if enabled and flush_interval_s > 0:
            try:
                self._periodic_task = asyncio.create_task(self._periodic_flush(flush_interval_s))
            except RuntimeError:
                self._periodic_task = None

    def trace(self, *, node: str, model: str, provider: str | None = None, trace_id: str | None = None, parent_span_id: str | None = None, attributes: Mapping[str, Any] | None = None) -> TraceHandle:
        return TraceHandle(self, node=node, model=model, provider=provider, trace_id=trace_id, parent_span_id=parent_span_id, attributes=attributes)

    def capture(self, span: SpanRecord) -> None:
        if self._closed or not self.enabled:
            return
        if self.on_export is not None:
            try:
                result = self.on_export([span])
                if inspect.isawaitable(result):
                    try:
                        asyncio.get_running_loop().create_task(result)
                    except RuntimeError:
                        pass
            except Exception:
                pass
            return
        with self._queue_lock:
            if len(self._queue) >= self.max_queue_size:
                self._queue.popleft()
                self._dropped_pending += 1
            self._queue.append(span)

    async def _periodic_flush(self, interval: float) -> None:
        while not self._closed:
            await asyncio.sleep(interval)
            await self.flush()

    async def flush(self) -> ExportResult:
        if not self.enabled:
            return ExportResult(0, 0)
        async with self._flush_lock:
            with self._queue_lock:
                batch = list(self._queue)
                self._queue.clear()
                dropped = self._dropped_pending
                self._dropped_pending = 0
            if not batch:
                return ExportResult(0, dropped)
            payload = to_otlp_payload(batch, service_name=self.service_name, service_version=self.service_version, environment=self.environment, resource_attributes=self.resource_attributes)
            headers = {"content-type": "application/json"}
            if self.api_key:
                headers["authorization"] = f"Bearer {self.api_key}"
            if self.project_id:
                headers["X-AgentPGO-Project-ID"] = self.project_id
            try:
                result = self.transport(self.endpoint, headers, payload)
                if inspect.isawaitable(result):
                    result = await result
                status = getattr(result, "status_code", result)
                if isinstance(status, int) and not 200 <= status < 300:
                    raise RuntimeError(f"OTLP export failed with HTTP {status}")
                return ExportResult(len(batch), dropped)
            except Exception as error:  # fail open: customer agent must continue
                return ExportResult(0, dropped + len(batch), error)

    def flush_sync(self) -> ExportResult:
        """Flush from synchronous code when no event loop is running."""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.flush())
        raise RuntimeError("flush_sync cannot be called from a running event loop; await flush() instead")

    async def shutdown(self) -> ExportResult:
        if self._closed:
            return ExportResult(0, 0)
        self._closed = True
        if self._periodic_task:
            self._periodic_task.cancel()
            try:
                await self._periodic_task
            except asyncio.CancelledError:
                pass
        return await self.flush()

    def shutdown_sync(self) -> ExportResult:
        """Shutdown from synchronous code when no event loop is running."""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.shutdown())
        raise RuntimeError("shutdown_sync cannot be called from a running event loop; await shutdown() instead")


def current_trace_id() -> str | None:
    """Return the trace ID active in this execution context, if any."""
    return _trace_context.get()


def current_span_id() -> str | None:
    """Return the current parent span ID active in this execution context."""
    return _span_context.get()


__all__ = ["AgentPGOClient", "ExportResult", "SpanRecord", "TraceHandle", "current_span_id", "current_trace_id", "sanitize_attributes", "to_otlp_payload"]
