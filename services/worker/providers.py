"""Provider execution seam.

The default implementations intentionally do not perform network calls. A
transport callable is injected by the application or a test, keeping domain
evaluation deterministic and preventing accidental provider requests.
"""

from dataclasses import dataclass
import time
from typing import Any, Callable, Literal
from threading import Thread
from queue import Queue as ThreadQueue, Empty
import json
import os
import urllib.error
import urllib.request


@dataclass(frozen=True)
class ProviderRequest:
    provider: str
    model: str
    prompt: str
    temperature: float = 0.0
    max_tokens: int | None = None


@dataclass(frozen=True)
class ProviderResponse:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: float = 0.0
    raw: Any = None


@dataclass(frozen=True)
class ProviderError:
    category: Literal["authentication", "rate_limit", "timeout", "server", "invalid_request", "unknown"]
    message: str
    retryable: bool
    status_code: int | None = None


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3
    backoff_seconds: float = 0.25
    max_backoff_seconds: float = 8.0
    timeout_seconds: float = 30.0

    def __post_init__(self) -> None:
        if self.max_attempts < 1 or self.max_attempts > 10 or self.backoff_seconds < 0 or self.max_backoff_seconds < 0:
            raise ValueError("invalid retry policy")
        if self.timeout_seconds <= 0 or self.timeout_seconds > 300:
            raise ValueError("timeout_seconds must be between 0 and 300")


Transport = Callable[[ProviderRequest], ProviderResponse]


def classify_provider_error(error: BaseException) -> ProviderError:
    status = getattr(error, "status_code", None) or getattr(error, "status", None)
    try:
        status = int(status) if status is not None else None
    except (TypeError, ValueError):
        status = None
    message = str(error)
    lower = message.casefold()
    if status in (401, 403) or "api key" in lower or "unauthorized" in lower:
        return ProviderError("authentication", message, False, status)
    if status in (425, 429) or "rate limit" in lower or "too many requests" in lower:
        return ProviderError("rate_limit", message, True, status)
    if status == 408 or isinstance(error, (TimeoutError, ConnectionError)) or "timeout" in lower:
        return ProviderError("timeout", message, True, status)
    if status is not None and status >= 500:
        return ProviderError("server", message, True, status)
    if status is not None and 400 <= status < 500:
        return ProviderError("invalid_request", message, False, status)
    return ProviderError("unknown", message, False, status)


class ProviderExecutor:
    def __init__(self, transport: Transport | None = None, retry_policy: RetryPolicy | None = None, sleeper: Callable[[float], None] = time.sleep, *, timeout_seconds: float | None = None) -> None:
        self.transport = transport
        self.retry_policy = retry_policy or RetryPolicy()
        self.sleeper = sleeper
        self.timeout_seconds = self.retry_policy.timeout_seconds if timeout_seconds is None else float(timeout_seconds)
        if self.timeout_seconds <= 0 or self.timeout_seconds > 300:
            raise ValueError("timeout_seconds must be between 0 and 300")

    def _call_with_timeout(self, request: ProviderRequest) -> ProviderResponse:
        result: ThreadQueue[tuple[bool, Any]] = ThreadQueue(maxsize=1)
        def run() -> None:
            try:
                result.put((True, self.transport(request)))
            except BaseException as exc:
                result.put((False, exc))
        thread = Thread(target=run, daemon=True)
        thread.start()
        try:
            ok, value = result.get(timeout=self.timeout_seconds)
        except Empty as exc:
            raise TimeoutError(f"provider call exceeded {self.timeout_seconds:.3f}s timeout") from exc
        if not ok:
            raise value
        return value

    def execute(self, request: ProviderRequest) -> ProviderResponse:
        if self.transport is None:
            raise RuntimeError("provider transport is not configured; inject one before execution")
        last_error: ProviderError | None = None
        for attempt in range(self.retry_policy.max_attempts):
            try:
                result = self._call_with_timeout(request)
                if not isinstance(result, ProviderResponse):
                    raise TypeError("provider transport must return ProviderResponse")
                return result
            except Exception as exc:
                last_error = classify_provider_error(exc)
                if not last_error.retryable or attempt + 1 >= self.retry_policy.max_attempts:
                    raise RuntimeError(f"{last_error.category}: {last_error.message}") from exc
                delay = min(self.retry_policy.backoff_seconds * (2**attempt), self.retry_policy.max_backoff_seconds)
                if delay:
                    self.sleeper(delay)
        raise RuntimeError(last_error.message if last_error else "provider execution failed")


class OpenAIExecutor(ProviderExecutor):
    def __init__(self, transport: Transport | None = None, **kwargs: Any) -> None:
        super().__init__(transport, **kwargs)


class AnthropicExecutor(ProviderExecutor):
    def __init__(self, transport: Transport | None = None, **kwargs: Any) -> None:
        super().__init__(transport, **kwargs)


class GoogleExecutor(ProviderExecutor):
    def __init__(self, transport: Transport | None = None, **kwargs: Any) -> None:
        super().__init__(transport, **kwargs)


class BackboardHTTPError(RuntimeError):
    """HTTP failure carrying the status needed by the common retry policy."""

    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        super().__init__(f"Backboard HTTP {status_code}")


class BackboardHTTPTransport:
    """Native Backboard ``POST /api/threads/messages`` transport.

    This transport is opt-in. Constructing the default provider executors never
    creates a network client or reads a provider credential. A caller must
    explicitly construct ``BackboardExecutor`` (or inject this transport).
    """

    default_base_url = "https://app.backboard.io/api"

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str | None = None,
        llm_provider: str | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        self.api_key = api_key or os.getenv("BACKBOARD_API_KEY")
        if not self.api_key:
            raise RuntimeError("BACKBOARD_API_KEY is required for Backboard live execution")

        configured_base_url = base_url or os.getenv("BACKBOARD_BASE_URL") or self.default_base_url
        configured_base_url = configured_base_url.rstrip("/")
        self.base_url = configured_base_url
        self.endpoint = (
            configured_base_url
            if configured_base_url.endswith("/threads/messages")
            else f"{configured_base_url}/threads/messages"
        )
        self.llm_provider = llm_provider or os.getenv("BACKBOARD_LLM_PROVIDER") or "openai"
        self.timeout_seconds = float(timeout_seconds)
        if self.timeout_seconds <= 0 or self.timeout_seconds > 300:
            raise ValueError("timeout_seconds must be between 0 and 300")

    @staticmethod
    def _usage_int(usage: dict[str, Any], *keys: str) -> int:
        for key in keys:
            value = usage.get(key)
            if value is None:
                continue
            try:
                parsed = int(value)
            except (TypeError, ValueError):
                continue
            if parsed >= 0:
                return parsed
        return 0

    def __call__(self, request: ProviderRequest) -> ProviderResponse:
        started = time.perf_counter()
        # Backboard auto-creates a thread when thread_id is omitted. The
        # generic ProviderRequest has no conversation/thread field, so each
        # call intentionally represents one independent message.
        body = json.dumps(
            {
                "content": request.prompt,
                "llm_provider": self.llm_provider,
                "model_name": request.model,
                "stream": False,
            }
        ).encode("utf-8")
        http_request = urllib.request.Request(
            self.endpoint,
            data=body,
            headers={"X-API-Key": self.api_key, "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(http_request, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise BackboardHTTPError(exc.code) from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise TimeoutError("Backboard request failed or timed out") from exc
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("Backboard response was not valid JSON") from exc

        if not isinstance(payload, dict) or not isinstance(payload.get("content"), str):
            raise RuntimeError("Backboard response did not contain string content")
        usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
        return ProviderResponse(
            text=payload["content"],
            input_tokens=self._usage_int(usage, "prompt_tokens", "input_tokens"),
            output_tokens=self._usage_int(usage, "completion_tokens", "output_tokens"),
            latency_ms=(time.perf_counter() - started) * 1000,
            raw=payload,
        )


class BackboardExecutor(ProviderExecutor):
    """ProviderExecutor pre-wired to Backboard's native HTTP transport."""

    def __init__(
        self,
        transport: Transport | None = None,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        llm_provider: str | None = None,
        timeout_seconds: float | None = None,
        **kwargs: Any,
    ) -> None:
        retry_policy = kwargs.get("retry_policy")
        effective_timeout = float(timeout_seconds if timeout_seconds is not None else (retry_policy.timeout_seconds if retry_policy is not None else 30.0))
        selected = transport or BackboardHTTPTransport(
            api_key,
            base_url=base_url,
            llm_provider=llm_provider,
            timeout_seconds=effective_timeout,
        )
        super().__init__(selected, timeout_seconds=effective_timeout, **kwargs)
