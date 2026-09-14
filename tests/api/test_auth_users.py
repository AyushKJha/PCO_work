"""Persisted user/session authentication contract tests."""

from __future__ import annotations

from fastapi.testclient import TestClient

from apps.api.db import create_session_factory, create_tables
from apps.api.main import create_app
from apps.api.models import AuthSession, Membership, User


def _client(tmp_path):
    factory = create_session_factory(f"sqlite:///{tmp_path / 'auth.db'}")
    create_tables(factory)
    return TestClient(create_app(session_factory=factory)), factory


def test_signup_persists_user_membership_and_returns_session(tmp_path):
    client, factory = _client(tmp_path)
    with client:
        response = client.post(
            "/api/v1/auth/signup",
            json={"name": "Ada Lovelace", "email": "Ada@Example.com", "password": "correct horse battery staple"},
        )

    assert response.status_code == 201
    body = response.json()
    assert body["user"]["email"] == "ada@example.com"
    assert body["user"]["name"] == "Ada Lovelace"
    assert body["tokenType"] == "Bearer"
    assert body["accessToken"]
    with factory() as session:
        assert session.query(User).count() == 1
        assert session.query(Membership).count() == 1
        assert session.query(AuthSession).count() == 1


def test_signin_me_patch_refresh_and_logout_revoke_session(tmp_path):
    client, _ = _client(tmp_path)
    with client:
        signup = client.post(
            "/api/v1/auth/signup",
            json={"name": "Grace Hopper", "email": "grace@example.com", "password": "a sufficiently long password"},
        ).json()
        client.post("/api/v1/auth/logout", headers={"authorization": f"Bearer {signup['accessToken']}"})

        signin = client.post(
            "/api/v1/auth/signin",
            json={"email": "GRACE@example.com", "password": "a sufficiently long password"},
        )
        assert signin.status_code == 200
        token = signin.json()["accessToken"]
        me = client.get("/api/v1/me", headers={"authorization": f"Bearer {token}"})
        assert me.status_code == 200
        assert me.json()["authType"] == "session"
        assert me.json()["user"]["email"] == "grace@example.com"

        patched = client.patch(
            "/api/v1/me",
            headers={"authorization": f"Bearer {token}"},
            json={"name": "Grace B. Hopper"},
        )
        assert patched.status_code == 200
        assert patched.json()["user"]["name"] == "Grace B. Hopper"

        refreshed = client.post("/api/v1/auth/refresh", headers={"authorization": f"Bearer {token}"})
        assert refreshed.status_code == 200
        refreshed_token = refreshed.json()["accessToken"]
        assert refreshed_token != token
        assert client.get("/api/v1/me", headers={"authorization": f"Bearer {token}"}).status_code == 401

        assert client.post("/api/v1/auth/logout", headers={"authorization": f"Bearer {refreshed_token}"}).status_code == 204
        assert client.get("/api/v1/me", headers={"authorization": f"Bearer {refreshed_token}"}).status_code == 401


def test_bad_credentials_do_not_reveal_account_existence(tmp_path):
    client, _ = _client(tmp_path)
    with client:
        client.post(
            "/api/v1/auth/signup",
            json={"name": "Katherine Johnson", "email": "kat@example.com", "password": "a sufficiently long password"},
        )
        existing = client.post("/api/v1/auth/signin", json={"email": "kat@example.com", "password": "wrong password"})
        missing = client.post("/api/v1/auth/signin", json={"email": "nobody@example.com", "password": "wrong password"})
    assert existing.status_code == missing.status_code == 401
    assert existing.json()["error"]["message"] == missing.json()["error"]["message"]
