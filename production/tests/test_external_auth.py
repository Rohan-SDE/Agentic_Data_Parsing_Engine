import hashlib
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from engine.db import User, session_factory

SUBJECT = "12345678-1234-1234-1234-123456789abc"


@pytest.fixture
def external(environment, monkeypatch):
    environment.public_registration = True
    environment.supabase_url = "https://example.supabase.co"
    environment.supabase_publishable_key = "test-publishable-key"
    environment.email_auth_enabled = True
    environment.phone_auth_enabled = True
    environment.google_auth_enabled = True
    calls = []
    def provider(path, payload=None, token=None):
        calls.append((path, payload, token))
        if path == "user":
            return {"id": SUBJECT, "email_confirmed_at": "2026-01-01", "phone_confirmed_at": "2026-01-01",
                    "identities": [{"provider": "google"}], "user_metadata": {"is_admin": True}}
        return {"access_token": "verified-provider-token"}
    monkeypatch.setattr("engine.external_auth.provider", provider)
    return calls


def verify(client, channel="email"):
    return client.post("/api/auth/external/verify-code", json={"channel": channel,
        "address": "test@example.com" if channel == "email" else "+919876543210", "code": "123456"})


def test_disabled(client):
    assert client.get("/api/auth/external/options").json() == {"email": False, "phone": False, "google": False}
    assert verify(client).status_code == 403
    assert client.post("/api/auth/external/google/start").status_code == 403


@pytest.mark.parametrize("channel", ["email", "phone"])
def test_verified_identity_is_private_standard_account(client, external, channel):
    response = verify(client, channel)
    assert response.status_code == 200 and not response.json()["is_admin"]
    assert client.get("/api/admin/users").status_code == 403
    assert client.get("/api/datasets").json()["total"] == 0
    assert "HttpOnly" in response.headers["set-cookie"]
    again = verify(client, channel)
    assert again.json()["username"] == response.json()["username"]
    with session_factory()() as db:
        user = db.scalar(select(User).where(User.username == response.json()["username"]))
        user.active = False
        db.commit()
    assert verify(client, channel).status_code == 403


def test_unverified_and_provider_failure(client, external, monkeypatch):
    monkeypatch.setattr("engine.external_auth.provider", lambda *a, **k: {"access_token": "token", "id": SUBJECT})
    assert verify(client).status_code == 401
    def failed(*args, **kwargs):
        raise HTTPException(503, "Unavailable")
    monkeypatch.setattr("engine.external_auth.provider", failed)
    assert verify(client).status_code == 503


def test_signup_disabled_allows_existing_identity(client, external, environment):
    assert verify(client).status_code == 200
    environment.public_registration = False
    assert verify(client).status_code == 200
    with session_factory()() as db:
        db.query(User).filter(User.external_subject.is_not(None)).update({User.external_subject: "other"})
        db.commit()
    assert verify(client).status_code == 403


def test_send_code_limits_and_validation(client, external):
    body = {"channel": "phone", "address": "+919876543210"}
    for _ in range(3):
        assert client.post("/api/auth/external/send-code", json=body).status_code == 200
    assert external[0][1] == {"phone": "+919876543210", "channel": "sms", "create_user": True}
    assert client.post("/api/auth/external/send-code", json=body).status_code == 429
    assert client.post("/api/auth/external/send-code", json={**body, "address": "123"}).status_code == 422
    assert client.post("/api/auth/external/send-code", json=body,
                       headers={"Origin": "https://evil.example"}).status_code == 403


def test_google_pkce_callback(client, external):
    import base64
    start = client.post("/api/auth/external/google/start")
    params = parse_qs(urlparse(start.json()["url"]).query)
    verifier = client.cookies.get("adpe_oauth_verifier")
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    assert params["code_challenge"] == [challenge]
    assert params["code_challenge_method"] == ["s256"]
    assert "HttpOnly" in start.headers["set-cookie"] and "SameSite=lax" in start.headers["set-cookie"]
    result = client.get("/api/auth/external/google/callback?code=code", follow_redirects=False)
    assert result.status_code == 303 and result.headers["location"] == "/"
    assert external[0] == ("token?grant_type=pkce", {"auth_code": "code", "code_verifier": verifier}, None)
    assert not client.get("/api/auth/me").json()["is_admin"]
    assert not client.cookies.get("adpe_oauth_verifier")
    assert client.get("/api/auth/external/google/callback?code=code", follow_redirects=False).headers["location"] == "/?auth_error=1"


def test_provider_transport_redacts_errors(environment, monkeypatch):
    import httpx

    from engine.external_auth import provider
    environment.supabase_url = "https://example.supabase.co"
    class Client:
        def __init__(self, **kwargs):
            assert kwargs["follow_redirects"] is False
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def request(self, *args, **kwargs):
            return httpx.Response(400, json={"secret": "must not leak"})
    monkeypatch.setattr("engine.external_auth.httpx.Client", Client)
    with pytest.raises(HTTPException) as error:
        provider("verify", {"token": "123456"})
    assert error.value.status_code == 400 and "must not leak" not in error.value.detail
