"""Dodo Payments boundary for server-side checkout and webhooks.

The provider client is deliberately a tiny protocol so tests and local
development can inject a fake without making network calls. Dodo credentials
are read only at call time from runtime environment/Secrets Manager-backed
environment variables.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from datetime import datetime, timezone
from typing import Any, Protocol
from urllib.parse import urlencode
from urllib.error import HTTPError, URLError
from urllib.request import Request as UrlRequest, urlopen
from uuid import uuid4

from fastapi import Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .models import BillingCheckout, BillingWebhookEvent, Membership, Organization, User, utc_now
from services.billing.referrals import qualify_referral


class DodoClient(Protocol):
    def create_checkout_session(self, payload: dict[str, Any], *, idempotency_key: str) -> dict[str, Any]: ...
    def create_customer_portal_session(self, customer_id: str, *, return_url: str | None = None) -> dict[str, Any]: ...


class DodoConfigurationError(RuntimeError):
    pass


class DodoProviderError(RuntimeError):
    """Provider failure; retryable is safe only when the same idempotency key is reused."""
    def __init__(self, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable


class HttpDodoClient:
    """Minimal dependency-free Dodo REST client for the checkout call."""

    def create_checkout_session(self, payload: dict[str, Any], *, idempotency_key: str) -> dict[str, Any]:
        token = (os.getenv("DODO_PAYMENTS_API_KEY") or os.getenv("DODO_API_KEY") or "").strip()
        product_id = os.getenv("DODO_PRO_PRODUCT_ID", "").strip()
        if not token or not product_id:
            raise DodoConfigurationError("Dodo checkout is not configured")
        environment = os.getenv("DODO_PAYMENTS_ENVIRONMENT", "test_mode").strip().lower()
        default_base = "https://live.dodopayments.com" if environment == "live_mode" else "https://test.dodopayments.com"
        base_url = os.getenv("DODO_API_BASE_URL", default_base).strip().rstrip("/")
        request_payload = {"product_cart": [{"product_id": product_id, "quantity": 1}], **payload}
        encoded = json.dumps(request_payload, separators=(",", ":")).encode("utf-8")
        req = UrlRequest(
            f"{base_url}/checkouts",
            data=encoded,
            method="POST",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Idempotency-Key": idempotency_key,
            },
        )
        try:
            with urlopen(req, timeout=float(os.getenv("DODO_TIMEOUT_SECONDS", "15"))) as response:
                result = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            raise DodoProviderError(f"Dodo checkout failed with status {exc.code}", retryable=exc.code == 408 or exc.code == 429 or exc.code >= 500) from exc
        except (URLError, TimeoutError) as exc:
            raise DodoProviderError("Dodo checkout request failed", retryable=True) from exc
        except (ValueError, json.JSONDecodeError) as exc:
            raise DodoProviderError("Dodo checkout returned invalid JSON", retryable=False) from exc
        if not isinstance(result, dict) or not result.get("session_id"):
            raise DodoProviderError("Dodo returned an invalid checkout response", retryable=False)
        return result

    def create_customer_portal_session(self, customer_id: str, *, return_url: str | None = None) -> dict[str, Any]:
        token = (os.getenv("DODO_PAYMENTS_API_KEY") or os.getenv("DODO_API_KEY") or "").strip()
        if not token:
            raise DodoConfigurationError("Dodo portal is not configured")
        environment = os.getenv("DODO_PAYMENTS_ENVIRONMENT", "test_mode").strip().lower()
        base_url = os.getenv("DODO_API_BASE_URL", "https://live.dodopayments.com" if environment == "live_mode" else "https://test.dodopayments.com").strip().rstrip("/")
        # Dodo documents return_url as a query parameter on this POST; body is empty.
        query = f"?{urlencode({'return_url': return_url})}" if return_url else ""
        req = UrlRequest(
            f"{base_url}/customers/{customer_id}/customer-portal/session{query}",
            data=b"",
            method="POST",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json", "Accept": "application/json"},
        )
        try:
            with urlopen(req, timeout=float(os.getenv("DODO_TIMEOUT_SECONDS", "15"))) as response:
                result = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
            raise DodoProviderError("Dodo customer portal request failed") from exc
        if not isinstance(result, dict) or not (result.get("link") or result.get("portal_url")):
            raise DodoProviderError("Dodo returned an invalid portal response")
        return result


class CheckoutRequest(BaseModel):
    model_config = {"populate_by_name": True}
    plan: str = Field(min_length=1, max_length=16)
    referral_code: str | None = Field(default=None, alias="referralCode", max_length=128)
    idempotency_key: str | None = Field(default=None, alias="idempotencyKey", min_length=1, max_length=255)

    @field_validator("plan")
    @classmethod
    def only_pro(cls, value: str) -> str:
        value = value.strip().lower()
        if value != "pro":
            raise ValueError("only the pro plan is available for checkout")
        return value


def _request_hash(body: CheckoutRequest) -> str:
    value = json.dumps(body.model_dump(by_alias=True, exclude_none=True, exclude={"idempotency_key"}), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def verify_dodo_signature(raw_body: bytes, *, webhook_id: str, signature: str, timestamp: str, secret: str | None = None) -> bool:
    """Verify Standard Webhooks HMAC signatures with replay protection."""
    key = (secret or os.getenv("DODO_PAYMENTS_WEBHOOK_KEY", "")).strip()
    if not key or not webhook_id or not signature or not timestamp:
        return False
    try:
        sent_at = int(timestamp)
    except (TypeError, ValueError):
        return False
    tolerance = int(os.getenv("DODO_WEBHOOK_TOLERANCE_SECONDS", "300"))
    if tolerance > 0 and abs(int(time.time()) - sent_at) > tolerance:
        return False
    signed = f"{webhook_id}.{timestamp}.".encode("utf-8") + raw_body
    # Standard Webhooks secrets are usually whsec_ + base64, while accepting
    # raw test secrets keeps local contract tests simple.
    keys = [key.encode("utf-8")]
    if key.startswith("whsec_"):
        try:
            keys.insert(0, base64.b64decode(key[6:] + "=" * (-len(key[6:]) % 4)))
        except (ValueError, base64.binascii.Error):
            return False
    expected_values: set[str] = set()
    for signing_key in keys:
        digest = hmac.new(signing_key, signed, hashlib.sha256).digest()
        expected_values.update({base64.b64encode(digest).decode("ascii"), digest.hex()})
    return any(hmac.compare_digest(part.removeprefix("v1,"), expected) for part in signature.split(" ") for expected in expected_values)


def _iso_date(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _event_data(payload: dict[str, Any]) -> dict[str, Any]:
    data = payload.get("data", {})
    return data if isinstance(data, dict) else {}


_SUBSCRIPTION_EVENTS = {
    "subscription.active": {"active"},
    "subscription.updated": {"active", "on_hold", "failed", "cancelled", "canceled", "expired"},
    "subscription.renewed": {"active"},
    "subscription.plan_changed": {"active"},
    "subscription.on_hold": {"on_hold"},
    "subscription.failed": {"failed"},
    "subscription.cancelled": {"cancelled", "canceled"},
    "subscription.canceled": {"cancelled", "canceled"},
    "subscription.expired": {"expired"},
}


def _event_timestamp(payload: dict[str, Any]) -> datetime | None:
    data = _event_data(payload)
    return _iso_date(payload.get("timestamp") or data.get("updated_at") or data.get("created_at"))


def _valid_provider_identity(event_type: str, payload: dict[str, Any]) -> bool:
    expected_business = (os.getenv("DODO_PAYMENTS_BUSINESS_ID") or os.getenv("DODO_BUSINESS_ID") or "").strip()
    if expected_business and payload.get("business_id") != expected_business:
        return False
    data = _event_data(payload)
    normalized = event_type.lower()
    status = str(data.get("status") or "").strip().lower()
    if normalized in _SUBSCRIPTION_EVENTS:
        expected_product = os.getenv("DODO_PRO_PRODUCT_ID", "").strip()
        if expected_product and data.get("product_id") != expected_product:
            return False
        if not status or status not in _SUBSCRIPTION_EVENTS[normalized] or not data.get("subscription_id"):
            return False
    elif normalized == "payment.succeeded" and status != "succeeded":
        return False
    elif normalized == "refund.succeeded" and status != "succeeded":
        return False
    return True


def _subscription_fields(data: dict[str, Any]) -> tuple[str | None, str | None, datetime | None]:
    customer = data.get("customer") if isinstance(data.get("customer"), dict) else {}
    customer_id = data.get("customer_id") or customer.get("customer_id")
    subscription_id = data.get("subscription_id")
    expiry = _iso_date(data.get("next_billing_date") or data.get("current_period_end") or data.get("expires_at"))
    return (str(customer_id) if customer_id else None, str(subscription_id) if subscription_id else None, expiry)


def _apply_subscription_event(session: Session, event_type: str, payload: dict[str, Any], *, event_id: str | None = None) -> bool:
    if not _valid_provider_identity(event_type, payload):
        return False
    data = _event_data(payload)
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    if isinstance(data.get("metadata"), dict):
        metadata = {**metadata, **data["metadata"]}
    organization_id = metadata.get("organization_id") or metadata.get("organizationId")
    customer_id, subscription_id, expiry = _subscription_fields(data)
    organization = session.get(Organization, str(organization_id)) if organization_id else None
    if organization is None and subscription_id:
        organization = session.scalar(select(Organization).where(Organization.dodo_subscription_id == subscription_id))
    if organization is None and customer_id:
        organization = session.scalar(select(Organization).where(Organization.dodo_customer_id == customer_id))
    if organization is None:
        checkout_id = metadata.get("checkout_id") or metadata.get("checkoutId")
        checkout = session.get(BillingCheckout, str(checkout_id)) if checkout_id else None
        if checkout is None and data.get("session_id"):
            checkout = session.scalar(select(BillingCheckout).where(BillingCheckout.provider_session_id == str(data["session_id"])))
        if checkout is not None:
            organization = session.get(Organization, checkout.organization_id)
    if organization is None:
        return False
    normalized = event_type.lower()
    incoming_at = _event_timestamp(payload)
    if incoming_at is not None and subscription_id:
        for prior in session.scalars(select(BillingWebhookEvent).order_by(BillingWebhookEvent.received_at.desc())).all():
            if event_id and prior.provider_event_id == event_id:
                continue
            prior_data = _event_data(prior.payload)
            prior_at = _event_timestamp(prior.payload)
            if prior_data.get("subscription_id") == subscription_id and prior_at is not None and prior_at > incoming_at:
                return False
    status = str(data.get("status") or "").strip().lower()
    # `subscription.updated` is also used for terminal/provider-failure
    # transitions.  Classify by the provider status before the broad event
    # family so a failed/cancelled update cannot reactivate Pro.
    if normalized in {"subscription.cancelled", "subscription.canceled", "subscription.expired", "refund.succeeded", "dispute.lost"} or (
        normalized == "subscription.updated" and status in {"cancelled", "canceled", "expired"}
    ):
        organization.plan = "free"
        organization.plan_status = "canceled" if status in {"cancelled", "canceled"} or "cancel" in normalized else "expired"
        organization.plan_source = "dodo"
        organization.plan_expires_at = utc_now()
    elif normalized in {"subscription.on_hold", "subscription.failed", "payment.failed"} or (
        normalized == "subscription.updated" and status in {"on_hold", "failed"}
    ):
        organization.plan = "free"
        organization.plan_status = "past_due"
        organization.plan_source = "dodo"
    elif (normalized in _SUBSCRIPTION_EVENTS and status == "active") or normalized == "payment.succeeded":
        organization.plan = "pro"
        organization.plan_status = "active"
        organization.plan_source = "dodo"
    else:
        return False
    if customer_id:
        organization.dodo_customer_id = customer_id
    if subscription_id:
        organization.dodo_subscription_id = subscription_id
    organization.dodo_subscription_status = status or normalized.rsplit(".", 1)[-1]
    if expiry:
        organization.plan_expires_at = expiry
    # The first successful recurring billing event is the qualification point
    # for early-Pro referrals. Rewards remain a durable pending ledger until a
    # provider-specific gateway applies the free month.
    if normalized == "subscription.renewed" and subscription_id:
        qualify_referral(
            session,
            invitee_organization_id=organization.id,
            subscription_id=subscription_id,
        )
    return True


def register_billing_routes(app: Any, *, get_tenant: Any, get_user: Any, get_session: Any) -> None:
    @app.post("/v1/billing/checkout", status_code=201, tags=["billing"])
    def create_checkout(
        body: CheckoutRequest,
        request: Request,
        tenant: Any = Depends(get_tenant),
        user: User = Depends(get_user),
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        if session.scalar(select(Membership).where(Membership.user_id == user.id, Membership.organization_id == tenant.organization_id)) is None:
            raise HTTPException(status_code=403, detail="User is not a member of this workspace")
        organization = session.get(Organization, tenant.organization_id)
        if organization is None:
            raise HTTPException(status_code=404, detail="Workspace not found")
        if organization.plan == "pro" and organization.plan_status == "active":
            raise HTTPException(status_code=409, detail="Workspace already has an active Pro subscription")
        header_key = (request.headers.get("Idempotency-Key") or "").strip()
        body_key = (body.idempotency_key or "").strip()
        if header_key and body_key and header_key != body_key:
            raise HTTPException(status_code=409, detail="Idempotency-Key header conflicts with request body")
        key = header_key or body_key
        if not key:
            raise HTTPException(status_code=400, detail="Idempotency-Key header is required")
        digest = _request_hash(body)
        existing = session.scalar(select(BillingCheckout).where(BillingCheckout.organization_id == organization.id, BillingCheckout.idempotency_key == key))
        checkout = None
        if existing is not None:
            if existing.request_hash != digest:
                raise HTTPException(status_code=409, detail="Idempotency key was used with a different request")
            if existing.checkout_url:
                return {"checkoutUrl": existing.checkout_url, "checkoutSessionId": existing.provider_session_id, "status": existing.status}
            if existing.status != "failed":
                raise HTTPException(status_code=409, detail="Checkout creation is still in progress")
            existing.status, existing.error = "pending", None
            session.commit()
            checkout = existing
        if checkout is None:
            metadata = {"organization_id": organization.id, "user_id": user.id}
            if body.referral_code:
                metadata["referral_code"] = body.referral_code
            checkout = BillingCheckout(
                organization_id=organization.id, user_id=user.id, plan="pro", idempotency_key=key,
                request_hash=digest, metadata_json=metadata, status="pending",
            )
            session.add(checkout)
            try:
                session.commit()
            except IntegrityError as exc:
                session.rollback()
                raise HTTPException(status_code=409, detail="Checkout request is already being processed") from exc
        else:
            metadata = dict(checkout.metadata_json or {})
        return_url = os.getenv("DODO_PAYMENTS_RETURN_URL", "").strip() or f"{os.getenv('APP_ORIGIN', '').rstrip('/')}/profile?billing=complete"
        payload = {"customer": {"email": user.email, "name": user.name}, "metadata": {**metadata, "checkout_id": checkout.id}}
        if return_url:
            payload["return_url"] = return_url
        try:
            max_attempts = max(1, min(int(os.getenv("DODO_CHECKOUT_MAX_ATTEMPTS", "3")), 5))
            for attempt in range(max_attempts):
                try:
                    result = app.state.dodo_client.create_checkout_session(payload, idempotency_key=key)
                    break
                except DodoProviderError as exc:
                    if not exc.retryable or attempt + 1 >= max_attempts:
                        raise
                    delay = max(0.0, float(os.getenv("DODO_CHECKOUT_RETRY_DELAY_SECONDS", "0.25"))) * (2 ** attempt)
                    if delay:
                        time.sleep(delay)
        except DodoConfigurationError as exc:
            checkout.status, checkout.error = "failed", "Dodo checkout is not configured"
            session.commit()
            raise HTTPException(status_code=503, detail="Pro checkout is not configured") from exc
        except DodoProviderError as exc:
            checkout.status, checkout.error = "failed", "provider checkout failed"
            session.commit()
            raise HTTPException(status_code=502, detail="Unable to create Pro checkout") from exc
        checkout.provider_session_id = str(result.get("session_id"))
        checkout.checkout_url = str(result.get("checkout_url")) if result.get("checkout_url") else None
        checkout.status = "created"
        session.commit()
        return {"checkoutUrl": checkout.checkout_url, "checkoutSessionId": checkout.provider_session_id, "status": checkout.status}

    @app.get("/v1/billing/entitlement", tags=["billing"])
    def billing_entitlement(tenant: Any = Depends(get_tenant), session: Session = Depends(get_session)) -> dict[str, Any]:
        organization = session.get(Organization, tenant.organization_id)
        if organization is None:
            raise HTTPException(status_code=404, detail="Workspace not found")
        return {"organizationId": organization.id, "plan": organization.plan, "status": organization.plan_status, "source": organization.plan_source, "expiresAt": organization.plan_expires_at.isoformat() if organization.plan_expires_at else None, "subscriptionId": organization.dodo_subscription_id}

    @app.post("/v1/billing/portal", tags=["billing"])
    def billing_portal(
        tenant: Any = Depends(get_tenant),
        user: User = Depends(get_user),
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        if session.scalar(select(Membership).where(Membership.user_id == user.id, Membership.organization_id == tenant.organization_id)) is None:
            raise HTTPException(status_code=403, detail="User is not a member of this workspace")
        organization = session.get(Organization, tenant.organization_id)
        if organization is None:
            raise HTTPException(status_code=404, detail="Workspace not found")
        if organization.plan != "pro" or organization.plan_status != "active":
            raise HTTPException(status_code=409, detail="An active Pro subscription is required")
        if not organization.dodo_customer_id:
            raise HTTPException(status_code=409, detail="No Dodo customer is linked to this workspace")
        return_url = os.getenv("DODO_PAYMENTS_RETURN_URL", "").strip() or os.getenv("APP_ORIGIN", "").rstrip("/") + "/profile"
        try:
            result = app.state.dodo_client.create_customer_portal_session(organization.dodo_customer_id, return_url=return_url or None)
        except DodoConfigurationError as exc:
            raise HTTPException(status_code=503, detail="Billing portal is not configured") from exc
        except DodoProviderError as exc:
            raise HTTPException(status_code=502, detail="Unable to create billing portal session") from exc
        return {"url": result.get("link") or result.get("portal_url")}

    @app.post("/v1/billing/webhooks/dodo", tags=["billing"])
    async def dodo_webhook(request: Request, session: Session = Depends(get_session)) -> dict[str, Any]:
        raw_body = await request.body()
        if len(raw_body) > 2 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="Webhook payload is too large")
        webhook_id = request.headers.get("webhook-id", "").strip()
        if not verify_dodo_signature(raw_body, webhook_id=webhook_id, signature=request.headers.get("webhook-signature", ""), timestamp=request.headers.get("webhook-timestamp", "")):
            raise HTTPException(status_code=401, detail="Invalid webhook signature")
        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=400, detail="Invalid webhook JSON") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("type"), str):
            raise HTTPException(status_code=422, detail="Webhook event type is required")
        if not webhook_id:
            raise HTTPException(status_code=400, detail="Webhook ID is required")
        existing = session.scalar(select(BillingWebhookEvent).where(BillingWebhookEvent.provider_event_id == webhook_id))
        if existing is not None:
            return {"received": True, "duplicate": True}
        event = BillingWebhookEvent(provider_event_id=webhook_id, event_type=str(payload["type"]), payload=payload, status="received")
        session.add(event)
        try:
            session.flush()
        except IntegrityError:
            session.rollback()
            return {"received": True, "duplicate": True}
        handled = _apply_subscription_event(session, str(payload["type"]), payload, event_id=webhook_id)
        event.status = "processed" if handled else "ignored"
        event.processed_at = utc_now()
        session.commit()
        return {"received": True, "duplicate": False, "handled": handled}
