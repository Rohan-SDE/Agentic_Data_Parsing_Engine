import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from engine.db import User, session_factory
from tests.conftest import PASSWORD


def register(client, **overrides):
    return client.post("/api/auth/register", json={"username": "newvisitor", "password": PASSWORD, **overrides})


def test_disabled_by_default(client):
    assert client.get("/api/auth/options").json() == {"public_registration": False}
    assert register(client).status_code == 403


def test_registration_login_and_no_admin(client, environment):
    environment.public_registration = True
    assert client.get("/api/auth/options").json()["public_registration"]
    assert register(client).status_code == 201
    with session_factory()() as db:
        user = db.scalar(select(User).where(User.username == "newvisitor"))
        assert user.active and not user.is_admin and user.password_hash != PASSWORD
    response = client.post("/api/auth/login", json={"username": "newvisitor", "password": PASSWORD})
    assert response.status_code == 200 and not response.json()["is_admin"]
    assert client.get("/api/admin/users").status_code == 403
    assert client.get("/api/datasets").json() == {"items": [], "total": 0}


@pytest.mark.parametrize("payload", [{"is_admin": True}, {"password": "short"}, {"username": "Bad Name"}])
def test_rejects_privilege_and_invalid_input(client, environment, payload):
    environment.public_registration = True
    assert register(client, **payload).status_code == 422


def test_duplicate_and_origin(client, environment):
    environment.public_registration = True
    assert register(client).status_code == 201
    assert register(client).status_code == 409
    assert client.post("/api/auth/register", json={"username": "another", "password": PASSWORD},
                       headers={"Origin": "https://evil.example"}).status_code == 403


def test_ip_rate_limit(client, environment):
    environment.public_registration = True
    for _ in range(5):
        assert register(client, username="analyst").status_code == 409
    assert register(client).status_code == 429


def test_duplicate_race_handled(client, environment, monkeypatch):
    environment.public_registration = True
    def collision(*args, **kwargs):
        raise IntegrityError("insert", {}, Exception("duplicate"))
    monkeypatch.setattr("engine.app.create_user", collision)
    assert register(client).status_code == 409
