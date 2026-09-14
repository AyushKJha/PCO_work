"""Early-access Pro referral contracts."""

from __future__ import annotations

from fastapi.testclient import TestClient

from apps.api.db import create_session_factory, create_tables
from apps.api.main import create_app
from apps.api.models import Membership, Organization, Referral
from services.billing.referrals import apply_rewards, qualify_referral


def _client(tmp_path):
    factory = create_session_factory(f"sqlite:///{tmp_path / 'referrals.db'}")
    create_tables(factory)
    return TestClient(create_app(session_factory=factory)), factory


def _signup(client: TestClient, email: str, *, referral_code: str | None = None) -> dict:
    payload = {
        "name": email.split("@", 1)[0].title(),
        "email": email,
        "password": "a sufficiently long password",
    }
    if referral_code:
        payload["referralCode"] = referral_code
    response = client.post("/api/v1/auth/signup", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def test_pro_workspace_can_create_and_list_referral_code(tmp_path):
    client, factory = _client(tmp_path)
    referrer = _signup(client, "referrer@example.com")
    with factory() as session:
        membership = session.query(Membership).one()
        organization = session.get(Organization, membership.organization_id)
        organization.plan = "pro"
        organization.plan_status = "active"
        session.commit()

    headers = {"authorization": f"Bearer {referrer['accessToken']}"}
    created = client.post("/api/v1/referrals/code", headers=headers)
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["code"].startswith("TR_PRO_")
    assert len(body["code"]) <= 64

    repeated = client.post("/api/v1/referrals/code", headers=headers)
    assert repeated.status_code == 200
    assert repeated.json()["code"] == body["code"]

    listed = client.get("/api/v1/referrals", headers=headers)
    assert listed.status_code == 200
    assert listed.json()["code"] == body["code"]
    assert listed.json()["summary"] == {"pending": 0, "qualified": 0, "rewarded": 0, "reversed": 0}


def test_free_workspace_cannot_create_referral_code(tmp_path):
    client, _ = _client(tmp_path)
    user = _signup(client, "free@example.com")
    response = client.post(
        "/api/v1/referrals/code",
        headers={"authorization": f"Bearer {user['accessToken']}"},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "PRO_REQUIRED"


def test_signup_attributes_new_workspace_to_referral_code(tmp_path):
    client, factory = _client(tmp_path)
    referrer = _signup(client, "referrer@example.com")
    with factory() as session:
        membership = session.query(Membership).one()
        organization = session.get(Organization, membership.organization_id)
        organization.plan = "pro"
        session.commit()
    code = client.post(
        "/api/v1/referrals/code",
        headers={"authorization": f"Bearer {referrer['accessToken']}"},
    ).json()["code"]

    invitee = _signup(client, "invitee@example.com", referral_code=code)
    listed = client.get(
        "/api/v1/referrals",
        headers={"authorization": f"Bearer {referrer['accessToken']}"},
    )
    assert listed.status_code == 200
    assert listed.json()["summary"]["pending"] == 1
    assert listed.json()["referrals"][0]["status"] == "PENDING"
    assert listed.json()["referrals"][0]["inviteeEmail"] == "invitee@example.com"

    invitee_me = client.get(
        "/api/v1/referrals",
        headers={"authorization": f"Bearer {invitee['accessToken']}"},
    )
    assert invitee_me.status_code == 200
    assert invitee_me.json()["summary"]["pending"] == 0


def test_invalid_referral_code_does_not_create_account(tmp_path):
    client, factory = _client(tmp_path)
    response = client.post(
        "/api/v1/auth/signup",
        json={
            "name": "New User",
            "email": "new@example.com",
            "password": "a sufficiently long password",
            "referralCode": "TR_PRO_DOES_NOT_EXIST",
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "REFERRAL_CODE_INVALID"
    with factory() as session:
        assert session.query(Membership).count() == 0


def test_referral_code_validation_is_safe_and_explicit(tmp_path):
    client, factory = _client(tmp_path)
    referrer = _signup(client, "referrer@example.com")
    with factory() as session:
        membership = session.query(Membership).one()
        organization = session.get(Organization, membership.organization_id)
        organization.plan = "pro"
        session.commit()
    code = client.post(
        "/api/v1/referrals/code",
        headers={"authorization": f"Bearer {referrer['accessToken']}"},
    ).json()["code"]

    assert client.post("/api/v1/referrals/validate", json={"code": code}).json() == {
        "valid": True,
        "code": code,
    }
    assert client.post("/api/v1/referrals/validate", json={"code": "TR_PRO_INVALID"}).json() == {
        "valid": False,
        "code": "TR_PRO_INVALID",
    }


class _FakeRewardGateway:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None, str]] = []

    def grant_free_month(self, *, organization_id: str, subscription_id: str | None, idempotency_key: str) -> str:
        self.calls.append((organization_id, subscription_id, idempotency_key))
        return f"provider:{idempotency_key}"


def test_verified_billing_qualifies_once_and_rewards_are_idempotent(tmp_path):
    client, factory = _client(tmp_path)
    referrer = _signup(client, "referrer@example.com")
    with factory() as session:
        membership = session.query(Membership).one()
        organization = session.get(Organization, membership.organization_id)
        organization.plan = "pro"
        session.commit()
    code = client.post(
        "/api/v1/referrals/code",
        headers={"authorization": f"Bearer {referrer['accessToken']}"},
    ).json()["code"]
    _signup(client, "invitee@example.com", referral_code=code)

    with factory() as session:
        referral = session.query(Referral).one()
        qualified = qualify_referral(
            session,
            invitee_organization_id=referral.invitee_organization_id,
            subscription_id="sub_123",
        )
        assert qualified is referral
        session.commit()

        gateway = _FakeRewardGateway()
        rewards = apply_rewards(session, referral_id=referral.id, gateway=gateway)
        session.commit()
        assert len(rewards) == 2
        assert len(gateway.calls) == 2
        assert session.get(Referral, referral.id).status == "REWARDED"

        # Replayed billing delivery must not issue a third provider operation.
        qualify_referral(session, invitee_organization_id=referral.invitee_organization_id, subscription_id="sub_123")
        apply_rewards(session, referral_id=referral.id, gateway=gateway)
        session.commit()
        assert len(gateway.calls) == 2
