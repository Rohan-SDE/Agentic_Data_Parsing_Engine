import asyncio
import os
import threading
import time

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import text

from engine.config import Settings
from engine.db import User, session_factory
from engine.ingestion import DataError
from engine.maintenance import cleanup, verify_storage
from engine.services import enqueue, store_dataset
from engine.worker import heartbeat, own_worker_healthy, retire_worker, write_identity
from scripts.configure_production import configure


def test_submission_replay_and_conflict(signed):
    dataset = signed.post("/api/datasets?filename=a.csv", content=b"x\n1\n").json()["id"]
    header = {"Idempotency-Key": "network-retry-key"}
    first = signed.post("/api/jobs", json={"dataset_id": dataset}, headers=header)
    replay = signed.post("/api/jobs", json={"dataset_id": dataset}, headers=header)
    assert first.status_code == replay.status_code == 202
    assert first.json()["id"] == replay.json()["id"]
    assert signed.get("/api/jobs").json()["total"] == 1
    conflict = signed.post("/api/jobs", json={"dataset_id": dataset, "objective": "Different objective"}, headers=header)
    assert conflict.status_code == 409


def test_idempotency_key_is_validated(signed):
    dataset = signed.post("/api/datasets?filename=a.csv", content=b"x\n1\n").json()["id"]
    response = signed.post("/api/jobs", json={"dataset_id": dataset}, headers={"Idempotency-Key": "invalid key"})
    assert response.status_code == 422


def test_body_deadline_is_enforced(environment):
    from engine.app import BodyLimitMiddleware
    environment.request_body_timeout_seconds = 0.01
    async def receive():
        await asyncio.sleep(1)
        return {"type": "http.request", "body": b"{}"}
    async def consumer(scope, receive, send):
        await receive()
    middleware = BodyLimitMiddleware(consumer)
    with pytest.raises(HTTPException) as error:
        asyncio.run(middleware({"type": "http", "method": "POST", "path": "/api/jobs", "headers": []}, receive, None))
    assert error.value.status_code == 408


def test_stale_schema_blocks_readiness(client):
    with session_factory()() as db:
        db.execute(text("UPDATE alembic_version SET version_num='0001'"))
        db.commit()
    response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.json()["status"] == "migration_required"


def test_worker_health_cannot_be_masked_by_replica(environment):
    write_identity("local-worker")
    heartbeat("other-worker")
    assert not own_worker_healthy()
    heartbeat("local-worker")
    assert own_worker_healthy()
    retire_worker("local-worker")
    assert not own_worker_healthy()


def test_hashing_overload_is_bounded(monkeypatch):
    from engine import security
    lock = threading.BoundedSemaphore(1)
    lock.acquire()
    monkeypatch.setattr(security, "PASSWORD_SLOTS", lock)
    for function, args in ((security.hash_password, ("password",)),
                           (security.password_ok, (security.DUMMY_HASH, "password"))):
        with pytest.raises(HTTPException) as error:
            function(*args)
        assert error.value.status_code == 429
        assert error.value.headers["Retry-After"] == "2"


def test_disabled_account_rechecked_at_commit(queued, environment):
    dataset, _ = queued
    with session_factory()() as stale:
        user = stale.get(User, dataset.owner_id)
        assert user.active
        with session_factory()() as fresh:
            fresh.get(User, user.id).active = False
            fresh.commit()
        with pytest.raises(DataError, match="Account"):
            enqueue(stale, user.id, dataset.id, "Inspect", {})
        source = environment.data_dir / "work" / "late.csv"
        source.write_bytes(b"x\n1\n")
        with pytest.raises(DataError, match="Account"):
            store_dataset(stale, user.id, "late.csv", source, "0" * 64, 4)
        assert source.exists()


def test_cleanup_preserves_sources_and_recent_staging(queued, environment):
    source = environment.data_dir / "uploads" / queued[0].storage_name
    old = time.time() - 90000
    os.utime(source, (old, old))
    orphan = environment.data_dir / "uploads" / "orphan.csv"
    orphan.write_text("x")
    os.utime(orphan, (old, old))
    recent = environment.data_dir / "work" / "active.upload"
    recent.write_text("x")
    assert cleanup() == 1
    assert source.exists() and recent.exists() and not orphan.exists()


def test_restored_source_verification_detects_corruption(queued, environment):
    assert verify_storage() == {"checked": 1, "failures": 0}
    (environment.data_dir / "uploads" / queued[0].storage_name).write_text("corrupt")
    assert verify_storage() == {"checked": 1, "failures": 1}


def test_secret_configuration_is_private_and_not_overwritten(tmp_path):
    config = configure(tmp_path, "data.example.com", "ops@example.com")
    secret = tmp_path / ".secrets" / "database_url"
    assert secret.read_text().strip().endswith("@db:5432/adpe")
    assert "://adpe_runtime:" in secret.read_text()
    assert (tmp_path / ".secrets" / "runtime_password").read_text() != (tmp_path / ".secrets" / "postgres_password").read_text()
    assert "postgresql" not in config.read_text()
    assert (tmp_path / ".secrets").stat().st_mode & 0o777 == 0o700
    with pytest.raises(FileExistsError):
        configure(tmp_path, "data.example.com", "ops@example.com")
    cfg = Settings(_env_file=None, database_url_file=secret)
    assert cfg.database_url == secret.read_text().strip()
    assert secret.read_text().strip() not in repr(cfg)


@pytest.mark.parametrize("options", [
    {"allowed_hosts": ["*.example.com"]},
    {"allowed_hosts": ["different.example.com"]},
    {"trust_proxy_headers": True, "trusted_proxy_ips": "*"},
    {"public_origin": "https://user:password@data.example.com"},
])
def test_production_rejects_ambiguous_trust(options):
    values = {"environment": "production", "database_url": "postgresql+psycopg://u:p@db/adpe",
              "secure_cookies": True, "public_origin": "https://data.example.com",
              "allowed_hosts": ["data.example.com"], **options}
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **values)
