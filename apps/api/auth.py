"""API-key and explicitly non-production demo authentication helpers.

The demo credential intentionally uses a small, dependency-free signed token
format. It is useful for exercising the browser/API integration before real
user sessions exist, but it is gated out of production by configuration.
"""

from __future__ import annotations

import hashlib
import hmac
import base64
import json
import math
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from time import time
from typing import Any

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from fastapi import Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import ApiKey, AuthSession, Membership, User, utc_now


_password_hasher = PasswordHasher()
SESSION_PREFIX = "trw_session_"
SESSION_TTL_SECONDS = 60 * 60 * 24 * 30


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def hash_api_key(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def issue_api_key(*, organization_id: str, project_id: str | None = None, name: str = "default") -> tuple[str, ApiKey]:
    secret = "agp_" + secrets.token_urlsafe(32)
    return secret, ApiKey(
        organization_id=organization_id,
        project_id=project_id,
        name=name,
        key_prefix=secret[:12],
        key_hash=hash_api_key(secret),
    )


def normalize_email(email: str) -> str:
    return email.strip().lower()


def hash_password(password: str) -> str:
    return _password_hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _password_hasher.verify(password_hash, password)
    except (InvalidHashError, VerificationError, VerifyMismatchError):
        return False


def issue_session(*, user_id: str, expires_in_seconds: int = SESSION_TTL_SECONDS) -> tuple[str, AuthSession]:
    if expires_in_seconds <= 0:
        raise ValueError("expires_in_seconds must be positive")
    raw = SESSION_PREFIX + secrets.token_urlsafe(48)
    now = utc_now()
    record = AuthSession(
        user_id=user_id,
        token_hash=hash_api_key(raw),
        expires_at=now + timedelta(seconds=expires_in_seconds),
        last_seen_at=now,
    )
    return raw, record


def _session_record(token: str, session: Session) -> AuthSession | None:
    return session.scalar(
        select(AuthSession).where(
            AuthSession.token_hash == hash_api_key(token),
            AuthSession.revoked_at.is_(None),
        )
    )


def session_token(authorization: str | None) -> str | None:
    bearer = _is_bearer(authorization)
    return bearer if bearer and bearer.startswith(SESSION_PREFIX) else None


def revoke_session(token: str, session: Session) -> bool:
    record = _session_record(token, session)
    if record is None or record.revoked_at is not None:
        return False
    record.revoked_at = utc_now()
    return True


def authenticate_user(request: Request, session: Session, authorization: str | None = None) -> User:
    bearer = _is_bearer(authorization or request.headers.get("authorization"))
    if not bearer or not bearer.startswith(SESSION_PREFIX):
        raise HTTPException(status_code=401, detail="Authentication required", headers={"WWW-Authenticate": "Bearer"})
    record = _session_record(bearer, session)
    now = utc_now()
    if record is None or _as_utc(record.expires_at) <= now:
        raise HTTPException(status_code=401, detail="Invalid or expired session", headers={"WWW-Authenticate": "Bearer"})
    user = session.get(User, record.user_id)
    if user is None or user.disabled_at is not None:
        raise HTTPException(status_code=401, detail="Invalid or expired session", headers={"WWW-Authenticate": "Bearer"})
    record.last_seen_at = now
    return user


@dataclass(frozen=True)
class Tenant:
    organization_id: str
    project_id: str | None
    api_key_id: str


DEMO_TOKEN_PREFIX = "agp_demo"
_revoked_demo_jtis: set[str] = set()


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    if not value or len(value) > 8192:
        raise ValueError("invalid demo token segment")
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _configured_demo_secret() -> str | None:
    secret = os.getenv("DEMO_AUTH_SECRET")
    return secret.strip() if secret and secret.strip() else None


def _configured_demo_tenant() -> tuple[str | None, str | None]:
    organization_id = os.getenv("DEMO_AUTH_ORGANIZATION_ID") or os.getenv("DEMO_AUTH_ORG_ID")
    project_id = os.getenv("DEMO_AUTH_PROJECT_ID")
    return (organization_id.strip() if organization_id else None, project_id.strip() if project_id else None)


def _demo_enabled() -> bool:
    return (
        os.getenv("APP_ENV", "development").strip().lower() != "production"
        and os.getenv("DEMO_AUTH_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}
    )


def issue_demo_token(
    *,
    organization_id: str,
    project_id: str | None = None,
    subject: str = "demo-user",
    expires_in_seconds: int = 3600,
    secret: str | None = None,
    jti: str | None = None,
) -> str:
    """Issue a signed demo bearer token for local/staging integration tests."""
    signing_secret = secret or _configured_demo_secret()
    if not signing_secret:
        raise ValueError("DEMO_AUTH_SECRET is required")
    if expires_in_seconds <= 0:
        raise ValueError("expires_in_seconds must be positive")
    now = int(time())
    payload: dict[str, Any] = {
        "sub": subject,
        "org_id": organization_id,
        "project_id": project_id,
        "iat": now,
        "exp": now + expires_in_seconds,
        "jti": jti or secrets.token_urlsafe(16),
    }
    encoded_payload = _b64encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    signing_input = f"{DEMO_TOKEN_PREFIX}.{encoded_payload}".encode("ascii")
    signature = _b64encode(hmac.new(signing_secret.encode("utf-8"), signing_input, hashlib.sha256).digest())
    return f"{DEMO_TOKEN_PREFIX}.{encoded_payload}.{signature}"


def _demo_claims(token: str) -> dict[str, Any] | None:
    try:
        prefix, encoded_payload, encoded_signature = token.split(".", 2)
        if prefix != DEMO_TOKEN_PREFIX or not encoded_payload or not encoded_signature:
            return None
        signing_secret = _configured_demo_secret()
        if not signing_secret:
            return None
        expected = hmac.new(
            signing_secret.encode("utf-8"),
            f"{prefix}.{encoded_payload}".encode("ascii"),
            hashlib.sha256,
        ).digest()
        actual = _b64decode(encoded_signature)
        if not hmac.compare_digest(expected, actual):
            return None
        claims = json.loads(_b64decode(encoded_payload).decode("utf-8"))
        if not isinstance(claims, dict):
            return None
        exp = claims.get("exp")
        jti = claims.get("jti")
        if (
            not isinstance(exp, (int, float))
            or not math.isfinite(float(exp))
            or exp <= time()
            or not isinstance(jti, str)
            or not jti
            or jti in _revoked_demo_jtis
        ):
            return None
        return claims
    except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError, base64.binascii.Error):
        return None


def revoke_demo_jti(jti: str) -> None:
    """Revoke a demo token identifier for the lifetime of this process."""
    if jti:
        _revoked_demo_jtis.add(jti)


def revoke_demo_token(token: str) -> bool:
    """Revoke a valid token, returning whether it contained a usable JTI."""
    claims = _demo_claims(token)
    if claims is None:
        return False
    revoke_demo_jti(str(claims["jti"]))
    return True


def _is_bearer(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        return None
    return value.strip()


def _extract_key(request: Request, authorization: str | None, x_api_key: str | None) -> str:
    candidate = x_api_key or request.headers.get("X-AgentPGO-API-Key")
    if candidate:
        return candidate.strip()
    if authorization:
        scheme, _, value = authorization.partition(" ")
        if scheme.lower() == "bearer" and value.strip():
            return value.strip()
    raise HTTPException(status_code=401, detail="Missing API key", headers={"WWW-Authenticate": "Bearer"})


def authenticate(
    request: Request,
    session: Session,
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
) -> Tenant:
    bearer = _is_bearer(authorization)
    if bearer and bearer.startswith(SESSION_PREFIX):
        record = _session_record(bearer, session)
        if record is None or record.revoked_at is not None or _as_utc(record.expires_at) <= utc_now():
            raise HTTPException(status_code=401, detail="Invalid or expired session", headers={"WWW-Authenticate": "Bearer"})
        user = session.get(User, record.user_id)
        if user is None or user.disabled_at is not None:
            raise HTTPException(status_code=401, detail="Invalid or expired session", headers={"WWW-Authenticate": "Bearer"})
        membership = session.scalar(select(Membership).where(Membership.user_id == user.id).order_by(Membership.created_at))
        if membership is None:
            raise HTTPException(status_code=403, detail="User has no organization membership")
        record.last_seen_at = utc_now()
        return Tenant(str(membership.organization_id), None, f"session:{user.id}")
    if bearer and bearer.startswith(f"{DEMO_TOKEN_PREFIX}."):
        # Demo auth is intentionally impossible to enable in production. A
        # signed token also cannot choose a different tenant than configured.
        if not _demo_enabled():
            raise HTTPException(status_code=401, detail="Invalid demo token", headers={"WWW-Authenticate": "Bearer"})
        claims = _demo_claims(bearer)
        configured_org, configured_project = _configured_demo_tenant()
        if (
            claims is None
            or not configured_org
            or claims.get("org_id") != configured_org
            or claims.get("project_id") != configured_project
        ):
            raise HTTPException(status_code=401, detail="Invalid demo token", headers={"WWW-Authenticate": "Bearer"})
        return Tenant(
            organization_id=configured_org,
            project_id=configured_project,
            api_key_id=f"demo:{claims['jti']}",
        )
    secret = _extract_key(request, authorization, x_api_key)
    if len(secret) > 512:
        raise HTTPException(status_code=401, detail="Invalid API key")
    key = session.scalar(select(ApiKey).where(ApiKey.key_hash == hash_api_key(secret), ApiKey.revoked_at.is_(None)))
    if key is None:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return Tenant(str(key.organization_id), str(key.project_id) if key.project_id else None, str(key.id))


def revoke_api_key(key: ApiKey) -> None:
    key.revoked_at = datetime.now(timezone.utc)
