"""Focused Dodo billing boundary contracts."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient

from apps.api.auth import issue_api_key
from apps.api.billing import DodoProviderError, HttpDodoClient
from apps.api.db import create_session_factory, create_tables
from apps.api.main import create_app
from apps.api.models import BillingCheckout, BillingWebhookEvent, Membership, Organization


class _RetryingDodo:
    def __init__(self) -> None:
        self.checkout_calls = 0
        self.portal_calls: list[tuple[str, str | None]] = []

    def create_checkout_session(self, payload, *, idempotency_key):
        self.checkout_calls += 1
        if self.checkout_calls == 1:
            raise DodoProviderError("temporary provider failure", retryable=True)
        return {"session_id": "sess_123", "checkout_url": "https://checkout.example/sess_123"}

    def create_customer_portal_session(self, customer_id, *, return_url=None):
        self.portal_calls.append((customer_id, return_url))
        return {"link": "https://portal.example/session"}


class _FailingDodo:
    def create_checkout_session(self, payload, *, idempotency_key):
        raise DodoProviderError("permanent provider failure", retryable=False)

    def create_customer_portal_session(self, customer_id, *, return_url=None):
        return {"link": "https://portal.example/session"}


def _client(tmp_path):
    factory = create_session_factory(f"sqlite:///{tmp_path / 'billing.db'}")
    create_tables(factory)
    dodo = _RetryingDodo()
    return TestClient(create_app(session_factory=factory, dodo_client=dodo)), factory, dodo


def _signup(client: TestClient, email: str) -> dict:
    response = client.post(
        "/api/v1/auth/signup",
        json={"name": "Billing User", "email": email, "password": "a sufficiently long password"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _configure_org(factory, access_token: str | None = None):
    with factory.begin() as session:
        membership = session.query(Membership).one()
        organization = session.get(Organization, membership.organization_id)
        assert organization is not None
        organization.dodo_customer_id = "cus_123"
        organization.dodo_subscription_id = "sub_123"
        organization.plan = "pro"
        organization.plan_status = "active"
        return organization.id


def test_checkout_retries_provider_failure_and_accepts_body_idempotency_compatibility(tmp_path, monkeypatch):
    monkeypatch.setenv("DODO_CHECKOUT_RETRY_DELAY_SECONDS", "0")
    client, factory, dodo = _client(tmp_path)
    user = _signup(client, "billing@example.com")
    body = {"plan": "pro", "idempotencyKey": "checkout_1"}

    first = client.post(
        "/api/v1/billing/checkout", headers={"authorization": f"Bearer {user['accessToken']}"}, json=body
    )
    assert first.status_code == 201, first.text
    assert first.json()["checkoutSessionId"] == "sess_123"
    assert dodo.checkout_calls == 2

    replay = client.post(
        "/api/v1/billing/checkout",
        headers={"authorization": f"Bearer {user['accessToken']}", "Idempotency-Key": "checkout_1"},
        json={"plan": "pro"},
    )
    assert replay.status_code == 201
    assert replay.json()["checkoutSessionId"] == "sess_123"
    assert dodo.checkout_calls == 2

    conflicting = client.post(
        "/api/v1/billing/checkout",
        headers={"authorization": f"Bearer {user['accessToken']}", "Idempotency-Key": "header_key"},
        json={"plan": "pro", "idempotencyKey": "body_key"},
    )
    assert conflicting.status_code == 409


def test_failed_checkout_retry_reuses_the_existing_idempotency_row(tmp_path, monkeypatch):
    monkeypatch.setenv("DODO_CHECKOUT_MAX_ATTEMPTS", "1")
    client, factory, _ = _client(tmp_path)
    user = _signup(client, "failed-retry@example.com")
    client.app.state.dodo_client = _FailingDodo()

    first = client.post(
        "/api/v1/billing/checkout",
        headers={"authorization": f"Bearer {user['accessToken']}"},
        json={"plan": "pro", "idempotencyKey": "retry-existing"},
    )
    assert first.status_code == 502, first.text

    succeeding_dodo = _RetryingDodo()
    succeeding_dodo.checkout_calls = 1
    client.app.state.dodo_client = succeeding_dodo
    second = client.post(
        "/api/v1/billing/checkout",
        headers={"authorization": f"Bearer {user['accessToken']}"},
        json={"plan": "pro", "idempotencyKey": "retry-existing"},
    )
    assert second.status_code == 201, second.text
    with factory.begin() as session:
        assert session.query(BillingCheckout).count() == 1
        checkout = session.query(BillingCheckout).one()
        assert checkout.status == "created"
        assert checkout.provider_session_id == "sess_123"


def test_customer_portal_requires_user_session_not_project_api_key(tmp_path):
    client, factory, dodo = _client(tmp_path)
    user = _signup(client, "portal@example.com")
    organization_id = _configure_org(factory)
    with factory.begin() as session:
        secret, key = issue_api_key(organization_id=organization_id, project_id=None)
        session.add(key)

    denied = client.post("/api/v1/billing/portal", headers={"x-api-key": secret})
    assert denied.status_code == 401
    allowed = client.post(
        "/api/v1/billing/portal", headers={"authorization": f"Bearer {user['accessToken']}"}
    )
    assert allowed.status_code == 200
    assert allowed.json() == {"url": "https://portal.example/session"}
    assert dodo.portal_calls[0][0] == "cus_123" and dodo.portal_calls[0][1].endswith("/profile")


def _signed_headers(body: bytes, event_id: str, secret: str) -> dict[str, str]:
    timestamp = str(int(time.time()))
    signed = f"{event_id}.{timestamp}.".encode() + body
    signature = base64.b64encode(hmac.new(secret.encode(), signed, hashlib.sha256).digest()).decode()
    return {
        "webhook-id": event_id,
        "webhook-timestamp": timestamp,
        "webhook-signature": f"v1,{signature}",
    }


def test_webhook_validates_business_product_status_and_handles_refund_succeeded(tmp_path, monkeypatch):
    secret = "webhook-secret"
    monkeypatch.setenv("DODO_PAYMENTS_WEBHOOK_KEY", secret)
    monkeypatch.setenv("DODO_PAYMENTS_BUSINESS_ID", "biz_123")
    monkeypatch.setenv("DODO_PRO_PRODUCT_ID", "prod_pro")
    client, factory, _ = _client(tmp_path)
    _signup(client, "webhook@example.com")
    organization_id = _configure_org(factory)

    payload = {
        "business_id": "biz_123",
        "type": "subscription.active",
        "timestamp": "2026-09-04T10:00:00Z",
        "data": {
            "payload_type": "Subscription",
            "customer_id": "cus_123",
            "subscription_id": "sub_123",
            "product_id": "prod_pro",
            "status": "active",
        },
    }
    raw = json.dumps(payload, separators=(",", ":")).encode()
    accepted = client.post("/api/v1/billing/webhooks/dodo", content=raw, headers=_signed_headers(raw, "evt_1", secret))
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["handled"] is True

    invalid = {**payload, "business_id": "biz_other"}
    raw_invalid = json.dumps(invalid, separators=(",", ":")).encode()
    rejected = client.post("/api/v1/billing/webhooks/dodo", content=raw_invalid, headers=_signed_headers(raw_invalid, "evt_2", secret))
    assert rejected.status_code == 200
    assert rejected.json()["handled"] is False

    refund = {**payload, "type": "refund.succeeded", "data": {"customer_id": "cus_123", "subscription_id": "sub_123", "status": "succeeded"}}
    raw_refund = json.dumps(refund, separators=(",", ":")).encode()
    refunded = client.post("/api/v1/billing/webhooks/dodo", content=raw_refund, headers=_signed_headers(raw_refund, "evt_3", secret))
    assert refunded.status_code == 200
    with factory.begin() as session:
        organization = session.get(Organization, organization_id)
        assert organization is not None
        assert organization.plan == "free"
        assert session.query(BillingWebhookEvent).filter_by(provider_event_id="evt_3").one().status == "processed"


def test_subscription_updated_failure_downgrades_instead_of_reactivating(tmp_path, monkeypatch):
    secret = "webhook-secret"
    monkeypatch.setenv("DODO_PAYMENTS_WEBHOOK_KEY", secret)
    monkeypatch.setenv("DODO_PAYMENTS_BUSINESS_ID", "biz_123")
    monkeypatch.setenv("DODO_PRO_PRODUCT_ID", "prod_pro")
    client, factory, _ = _client(tmp_path)
    _signup(client, "updated-failure@example.com")
    organization_id = _configure_org(factory)

    payload = {
        "business_id": "biz_123",
        "type": "subscription.updated",
        "timestamp": "2026-09-14T10:00:00Z",
        "data": {
            "customer_id": "cus_123",
            "subscription_id": "sub_123",
            "product_id": "prod_pro",
            "status": "failed",
        },
    }
    raw = json.dumps(payload, separators=(",", ":")).encode()
    response = client.post(
        "/api/v1/billing/webhooks/dodo",
        content=raw,
        headers=_signed_headers(raw, "evt_updated_failed", secret),
    )
    assert response.status_code == 200, response.text
    assert response.json()["handled"] is True
    with factory.begin() as session:
        organization = session.get(Organization, organization_id)
        assert organization is not None
        assert organization.plan == "free"
        assert organization.plan_status == "past_due"


def test_http_portal_request_uses_documented_query_shape(tmp_path, monkeypatch):
    captured = {}

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b'{"link":"https://portal.example"}'

    def fake_urlopen(request, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return _Response()

    monkeypatch.setenv("DODO_PAYMENTS_API_KEY", "secret-not-logged")
    monkeypatch.setattr("apps.api.billing.urlopen", fake_urlopen)
    result = HttpDodoClient().create_customer_portal_session("cus_123", return_url="https://app.example/profile")
    request = captured["request"]
    assert result["link"] == "https://portal.example"
    assert parse_qs(urlparse(request.full_url).query) == {"return_url": ["https://app.example/profile"]}
    assert request.data == b""

