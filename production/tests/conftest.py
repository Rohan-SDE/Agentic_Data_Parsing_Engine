import hashlib

import pytest
from alembic.config import Config
from fastapi.testclient import TestClient

from alembic import command
from engine.config import settings
from engine.db import get_engine, session_factory
from engine.security import create_user
from engine.services import enqueue, store_dataset

PASSWORD = "Test-only-Password-2026!"


@pytest.fixture
def environment(tmp_path, monkeypatch):
    monkeypatch.setenv("ADPE_ENVIRONMENT", "test")
    monkeypatch.setenv("ADPE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ADPE_DATABASE_URL", "sqlite:///" + str(tmp_path / "test.db"))
    monkeypatch.setenv("ADPE_ALLOWED_HOSTS", '["testserver","localhost","127.0.0.1"]')
    monkeypatch.setenv("ADPE_PUBLIC_ORIGIN", "http://localhost:8000")
    monkeypatch.setenv("ADPE_SECURE_COOKIES", "false")
    monkeypatch.setenv("ADPE_OLLAMA_ENABLED", "false")
    monkeypatch.setenv("ADPE_WORKER_IDENTITY_FILE", str(tmp_path / "worker.json"))
    settings.cache_clear()
    get_engine.cache_clear()
    settings().prepare_dirs()
    command.upgrade(Config("alembic.ini"), "head")
    yield settings()
    get_engine().dispose()
    get_engine.cache_clear()
    settings.cache_clear()


@pytest.fixture
def accounts(environment):
    with session_factory()() as db:
        admin = create_user(db, "admin", PASSWORD, True)
        user = create_user(db, "analyst", PASSWORD)
        other = create_user(db, "other", PASSWORD)
        db.commit()
    return admin, user, other


@pytest.fixture
def client(accounts):
    from engine.app import app
    with TestClient(app) as client:
        yield client


def sign_in(client, username="analyst"):
    response = client.post("/api/auth/login", json={"username": username, "password": PASSWORD})
    assert response.status_code == 200, response.text
    client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    return response


@pytest.fixture
def signed(client):
    sign_in(client)
    return client


@pytest.fixture
def queued(accounts, environment):
    body = "temperature,voltage\n" + "\n".join(f"{20 + i % 3},{12 + (i % 3)/10}" for i in range(50)) + "\n500,8\n"
    source = environment.data_dir / "work" / "fixture.csv"
    source.write_text(body)
    with session_factory()() as db:
        dataset = store_dataset(db, accounts[1].id, "telemetry.csv", source,
                                hashlib.sha256(body.encode()).hexdigest(), len(body))
        job = enqueue(db, accounts[1].id, dataset.id, "Find unusual telemetry.", {"temperature": {"max": 80}})
    return dataset, job
