"""Customer-hosted runner transport for generic agent evaluations."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import json
import math
import time
from typing import Any, Callable
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class RunnerResult:
    quality: float
    latency_ms: float
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    passed: bool | None = None
    output: Any = "[REDACTED]"
    provider_request_id: str | None = None


class RunnerExecutor:
    """Synchronous request seam used by the worker and deterministic tests.

    Production callers should provide a transport that talks to the customer's
    runner gateway. The default transport uses HTTPS and sends only a bounded,
    signed task payload.
    """

    def __init__(self, endpoint: str, *, signing_secret: str, transport: Callable[[str, str, dict[str, Any], dict[str, str]], dict[str, Any]] | None = None, clock: Callable[[], float] = time.time, timeout_seconds: float = 120.0, store_content: bool = False) -> None:
        if not endpoint or not signing_secret:
            raise ValueError("runner endpoint and signing_secret are required")
        self.endpoint = endpoint
        self.signing_secret = signing_secret.encode()
        self.transport = transport or self._https_transport
        self.clock = clock
        self.timeout_seconds = timeout_seconds
        self.store_content = store_content

    def _https_transport(self, method: str, url: str, body: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
        request = Request(url, data=json.dumps(body, separators=(",", ":"), sort_keys=True).encode(), headers={"Content-Type": "application/json", **headers}, method=method)
        with urlopen(request, timeout=self.timeout_seconds) as response:
            value = json.loads(response.read().decode())
        if not isinstance(value, dict):
            raise ValueError("runner response must be an object")
        return value

    def execute(self, candidate: dict[str, Any], job: dict[str, Any], case: dict[str, Any]) -> RunnerResult:
        now = int(self.clock())
        body: dict[str, Any] = {
            "execution_key": f"{job['id']}:{case['case_id']}",
            "optimization_run_id": job.get("id"),
            "organization_id": job.get("organization_id"),
            "project_id": job.get("project_id"),
            "candidate": candidate,
            "case": {"case_id": case["case_id"], "input": case.get("input")},
            "expires_at": now + 300,
        }
        if self.store_content:
            body["case"]["expected"] = case.get("expected")
        canonical = json.dumps(body, separators=(",", ":"), sort_keys=True).encode()
        signature = hmac.new(self.signing_secret, str(now).encode() + b"." + canonical, hashlib.sha256).hexdigest()
        payload = self.transport("POST", self.endpoint, body, {"X-AgentPGO-Runner-Signature": f"sha256={signature}", "X-AgentPGO-Runner-Timestamp": str(now)})
        usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
        quality_value = payload.get("quality", payload.get("score"))
        if quality_value is None or "latency_ms" not in payload:
            raise ValueError("runner response must include quality/score and latency_ms")
        quality = float(quality_value)
        latency = float(payload["latency_ms"])
        if not math.isfinite(quality) or not 0.0 <= quality <= 1.0 or not math.isfinite(latency) or latency < 0:
            raise ValueError("runner quality or latency is invalid")
        passed = payload.get("passed")
        if passed is not None and not isinstance(passed, bool):
            raise ValueError("runner passed must be boolean when provided")

        def integer_usage(name: str) -> int:
            value = usage.get(name, payload.get(name, 0))
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"runner {name} must be a non-negative integer")
            return value

        def monetary_usage(name: str) -> float:
            value = usage.get(name, payload.get(name, 0.0))
            result = float(value)
            if not math.isfinite(result) or result < 0:
                raise ValueError(f"runner {name} must be a non-negative number")
            return result

        output = payload.get("output", "[REDACTED]") if self.store_content else "[REDACTED]"
        return RunnerResult(quality=quality, latency_ms=latency, input_tokens=integer_usage("input_tokens"), output_tokens=integer_usage("output_tokens"), cost_usd=monetary_usage("cost_usd"), passed=passed, output=output, provider_request_id=str(payload.get("provider_request_id")) if payload.get("provider_request_id") else None)

