from __future__ import annotations

import json
import urllib.error

import pytest

from services.worker.providers import BackboardExecutor, BackboardHTTPTransport, ProviderRequest, RetryPolicy


class _Response:
    def __init__(self, payload: dict):
        self.payload = payload
    def __enter__(self):
        return self
    def __exit__(self, *_):
        return False
    def read(self):
        return json.dumps(self.payload).encode()


def test_backboard_transport_sends_native_request_and_usage(monkeypatch):
    seen = {}
    def fake_urlopen(request, timeout):
        seen["url"] = request.full_url
        seen["headers"] = dict(request.header_items())
        seen["body"] = json.loads(request.data.decode())
        seen["timeout"] = timeout
        return _Response({"content": "hello", "thread_id": "t1", "message_id": "m1", "usage": {"prompt_tokens": 4, "completion_tokens": 3}})
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    result = BackboardExecutor(api_key="secret", base_url="https://example.test/api", llm_provider="openrouter", retry_policy=RetryPolicy(max_attempts=1, timeout_seconds=2)).execute(ProviderRequest(provider="backboard", model="gpt-luna-5.6", prompt="question"))
    assert result.text == "hello"
    assert (result.input_tokens, result.output_tokens) == (4, 3)
    assert seen["url"] == "https://example.test/api/threads/messages"
    assert seen["headers"]["X-api-key"] == "secret"
    assert seen["body"] == {"content": "question", "llm_provider": "openrouter", "model_name": "gpt-luna-5.6", "stream": False}


def test_backboard_requires_key(monkeypatch):
    monkeypatch.delenv("BACKBOARD_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="BACKBOARD_API_KEY"):
        BackboardHTTPTransport()



def test_backboard_transport_reads_environment_configuration(monkeypatch):
    monkeypatch.setenv("BACKBOARD_API_KEY", "env-secret")
    monkeypatch.setenv("BACKBOARD_BASE_URL", "https://backboard.example/api")
    monkeypatch.setenv("BACKBOARD_LLM_PROVIDER", "anthropic")

    transport = BackboardHTTPTransport()

    assert transport.api_key == "env-secret"
    assert transport.base_url == "https://backboard.example/api"
    assert transport.endpoint == "https://backboard.example/api/threads/messages"
    assert transport.llm_provider == "anthropic"


def test_backboard_executor_maps_http_status_to_retry_policy(monkeypatch):
    attempts = []

    def fake_urlopen(_request, timeout):
        attempts.append(timeout)
        raise urllib.error.HTTPError(
            "https://example.test/api/threads/messages", 429, "rate limited", {}, None
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    executor = BackboardExecutor(
        api_key="secret",
        base_url="https://example.test/api",
        retry_policy=RetryPolicy(max_attempts=2, backoff_seconds=0, timeout_seconds=2),
    )

    with pytest.raises(RuntimeError, match=r"rate_limit: Backboard HTTP 429"):
        executor.execute(ProviderRequest(provider="backboard", model="gpt-4o", prompt="question"))
    assert attempts == [2, 2]
