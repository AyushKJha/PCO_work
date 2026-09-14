"""Durable early-Pro referral attribution and reward primitives.

The API owns attribution state; a payment provider adapter owns the external
subscription update.  Keeping those operations separate makes webhook retries
safe and lets tests exercise the policy without contacting Dodo.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from apps.api.models import Organization, Referral, ReferralCode, ReferralReward, User, utc_now

REFERRAL_CODE_PREFIX = "TR_PRO_"
REFERRAL_CODE_TTL_DAYS = 30
REFERRAL_STATUS_PENDING = "PENDING"
REFERRAL_STATUS_QUALIFIED = "QUALIFIED"
REFERRAL_STATUS_REWARDED = "REWARDED"
REFERRAL_STATUS_REVERSED = "REVERSED"
REWARD_STATUS_PENDING = "PENDING"
REWARD_STATUS_REWARDED = "REWARDED"
MAX_QUALIFIED_PER_MONTH = 10


class ReferralPolicyError(ValueError):
    """A user/action failed a referral policy check."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class RewardGateway(Protocol):
    """Provider abstraction used by Dodo or a deterministic test double."""

    def grant_free_month(
        self,
        *,
        organization_id: str,
        subscription_id: str | None,
        idempotency_key: str,
    ) -> str | None:
        """Apply one free month and return the provider operation reference."""


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _is_active_pro(organization: Organization, now: datetime) -> bool:
    if str(organization.plan or "free").lower() != "pro":
        return False
    if str(organization.plan_status or "active").lower() not in {"active", "pro_active"}:
        return False
    return organization.plan_expires_at is None or _as_utc(organization.plan_expires_at) > now


def _new_code(session: Session) -> str:
    for _ in range(8):
        candidate = REFERRAL_CODE_PREFIX + secrets.token_urlsafe(9).replace("-", "").replace("_", "").upper()
        if session.scalar(select(ReferralCode.id).where(ReferralCode.code == candidate)) is None:
            return candidate
    raise RuntimeError("unable to allocate a unique referral code")


def get_or_create_code(
    session: Session,
    *,
    organization: Organization,
    user: User,
    now: datetime | None = None,
) -> ReferralCode:
    """Return the organization's one active code, creating it for active Pro."""
    current = now or utc_now()
    if not _is_active_pro(organization, current):
        raise ReferralPolicyError("PRO_REQUIRED", "An active Pro workspace is required to create referral codes")
    existing = session.scalar(
        select(ReferralCode).where(ReferralCode.organization_id == organization.id, ReferralCode.active.is_(True))
    )
    if existing is not None:
        return existing
    code = ReferralCode(
        organization_id=organization.id,
        created_by_user_id=user.id,
        code=_new_code(session),
        active=True,
    )
    session.add(code)
    session.flush()
    return code


def find_valid_code(session: Session, code: str) -> ReferralCode | None:
    normalized = code.strip().upper()
    if not normalized or len(normalized) > 64:
        return None
    return session.scalar(select(ReferralCode).where(ReferralCode.code == normalized, ReferralCode.active.is_(True)))


def attribute_signup(
    session: Session,
    *,
    code: str,
    invitee_user: User,
    invitee_organization: Organization,
    now: datetime | None = None,
) -> Referral:
    """Attach a newly-created workspace to a valid referral code.

    Call this before the signup transaction commits.  Invalid attribution
    raises without adding a referral row, so the caller can roll back the new
    account atomically.
    """
    current = now or utc_now()
    referral_code = find_valid_code(session, code)
    if referral_code is None:
        raise ReferralPolicyError("REFERRAL_CODE_INVALID", "Referral code is invalid or inactive")
    owner = session.get(Organization, referral_code.organization_id)
    if owner is None or owner.id == invitee_organization.id:
        raise ReferralPolicyError("REFERRAL_CODE_INVALID", "Referral code is invalid or inactive")
    existing = session.scalar(select(Referral.id).where(Referral.invitee_organization_id == invitee_organization.id))
    if existing is not None:
        raise ReferralPolicyError("REFERRAL_ALREADY_ATTRIBUTED", "This workspace already has referral attribution")
    referral = Referral(
        referral_code_id=referral_code.id,
        referrer_user_id=referral_code.created_by_user_id,
        referrer_organization_id=owner.id,
        invitee_user_id=invitee_user.id,
        invitee_organization_id=invitee_organization.id,
        status=REFERRAL_STATUS_PENDING,
        attribution_expires_at=current + timedelta(days=REFERRAL_CODE_TTL_DAYS),
    )
    session.add(referral)
    session.flush()
    return referral


def qualify_referral(
    session: Session,
    *,
    invitee_organization_id: str,
    subscription_id: str,
    now: datetime | None = None,
) -> Referral | None:
    """Mark a referral qualified after a verified first paid billing period.

    This function is intentionally provider-agnostic and idempotent.  A Dodo
    webhook handler calls it only after signature verification and event
    de-duplication, then commits the transaction.
    """
    current = now or utc_now()
    referral = session.scalar(
        select(Referral).where(Referral.invitee_organization_id == invitee_organization_id)
    )
    if referral is None or referral.status == REFERRAL_STATUS_REVERSED:
        return None
    if referral.subscription_id and referral.subscription_id != subscription_id:
        raise ReferralPolicyError("REFERRAL_SUBSCRIPTION_MISMATCH", "Subscription does not match referral attribution")
    if referral.status == REFERRAL_STATUS_PENDING and _as_utc(referral.attribution_expires_at) <= current:
        referral.status = REFERRAL_STATUS_REVERSED
        session.flush()
        return None
    if referral.status in {REFERRAL_STATUS_QUALIFIED, REFERRAL_STATUS_REWARDED}:
        return referral
    referral.subscription_id = subscription_id
    referral.status = REFERRAL_STATUS_QUALIFIED
    referral.qualified_at = current
    session.flush()
    _ensure_rewards(session, referral)
    return referral


def _ensure_rewards(session: Session, referral: Referral) -> list[ReferralReward]:
    existing = list(session.scalars(select(ReferralReward).where(ReferralReward.referral_id == referral.id)).all())
    if existing:
        return existing
    rewards = [
        ReferralReward(
            referral_id=referral.id,
            recipient_user_id=referral.referrer_user_id,
            recipient_organization_id=referral.referrer_organization_id,
            idempotency_key=f"referral:{referral.id}:referrer",
        ),
        ReferralReward(
            referral_id=referral.id,
            recipient_user_id=referral.invitee_user_id,
            recipient_organization_id=referral.invitee_organization_id,
            idempotency_key=f"referral:{referral.id}:invitee",
        ),
    ]
    session.add_all(rewards)
    session.flush()
    return rewards


def apply_rewards(
    session: Session,
    *,
    referral_id: str,
    gateway: RewardGateway,
    now: datetime | None = None,
) -> list[ReferralReward]:
    """Apply each pending reward with provider idempotency keys.

    The caller commits after this function returns.  A provider exception
    leaves pending rows untouched so a durable retry can safely resume.
    """
    current = now or utc_now()
    referral = session.get(Referral, referral_id)
    if referral is None:
        raise ReferralPolicyError("REFERRAL_NOT_FOUND", "Referral not found")
    if referral.status == REFERRAL_STATUS_PENDING:
        raise ReferralPolicyError("REFERRAL_NOT_QUALIFIED", "Referral is not qualified")
    rewards = _ensure_rewards(session, referral)
    for reward in rewards:
        if reward.status == REWARD_STATUS_REWARDED:
            continue
        provider_reference = gateway.grant_free_month(
            organization_id=reward.recipient_organization_id,
            subscription_id=referral.subscription_id,
            idempotency_key=reward.idempotency_key,
        )
        reward.status = REWARD_STATUS_REWARDED
        reward.provider_reference = provider_reference
        reward.rewarded_at = current
    if all(reward.status == REWARD_STATUS_REWARDED for reward in rewards):
        referral.status = REFERRAL_STATUS_REWARDED
    session.flush()
    return rewards


def referral_summary(session: Session, *, organization_id: str) -> dict[str, int]:
    rows = session.scalars(select(Referral.status).where(Referral.referrer_organization_id == organization_id)).all()
    summary = {"pending": 0, "qualified": 0, "rewarded": 0, "reversed": 0}
    for status in rows:
        key = str(status).lower()
        if key in summary:
            summary[key] += 1
    return summary

