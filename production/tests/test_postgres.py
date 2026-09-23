"""Real PostgreSQL tests. Set ADPE_TEST_POSTGRES_URL to a disposable database only."""
import os
from concurrent.futures import ThreadPoolExecutor

import pytest
from alembic.config import Config
from sqlalchemy import text

from alembic import command
from engine.config import settings
from engine.db import Job, get_engine, session_factory
from engine.security import create_user
from engine.services import enqueue
from engine.worker import claim_job, finish

pytestmark = pytest.mark.skipif(not os.getenv("ADPE_TEST_POSTGRES_URL"), reason="No disposable PostgreSQL configured")


@pytest.fixture
def postgres(tmp_path, monkeypatch):
    monkeypatch.setenv("ADPE_ENVIRONMENT", "test")
    monkeypatch.setenv("ADPE_DATABASE_URL", os.environ["ADPE_TEST_POSTGRES_URL"])
    monkeypatch.setenv("ADPE_DATA_DIR", str(tmp_path / "data"))
    settings.cache_clear()
    get_engine.cache_clear()
    command.upgrade(Config("alembic.ini"), "head")
    with get_engine().begin() as conn:
        conn.execute(text("TRUNCATE users, datasets, jobs, auth_sessions, audit_events, rate_buckets, worker_heartbeats CASCADE"))
    with session_factory()() as db:
        user = create_user(db, "pguser", "Postgres-Test-Password!")
        from engine.db import Dataset
        dataset = Dataset(owner_id=user.id, filename="a.csv", format="csv", storage_name="a.csv", sha256="0" * 64, size_bytes=10)
        db.add(dataset)
        db.commit()
    yield user.id, dataset.id
    get_engine().dispose()
    get_engine.cache_clear()
    settings.cache_clear()


def test_concurrent_claims_are_unique(postgres):
    user, dataset = postgres
    with session_factory()() as db:
        for _ in range(10):
            db.add(Job(owner_id=user, dataset_id=dataset, objective="Concurrent claim", thresholds={}))
        db.commit()
    with ThreadPoolExecutor(max_workers=8) as pool:
        claims = list(pool.map(lambda _: claim_job(), range(16)))
    ids = [c[0] for c in claims if c]
    assert len(ids) == 10 and len(set(ids)) == 10
    for claim in claims:
        if claim:
            assert finish(*claim, {"ok": True, "result": {"verified": True}})


def test_concurrent_queue_quota_is_atomic(postgres):
    user, dataset = postgres
    settings().max_pending_jobs = 2
    def submit(_):
        from engine.ingestion import DataError
        with session_factory()() as db:
            try:
                enqueue(db, user, dataset, "Concurrent submission", {})
                return True
            except DataError:
                return False
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(submit, range(8)))
    assert sum(results) == 2


def test_concurrent_rate_limit_is_atomic(postgres):
    from fastapi import HTTPException

    from engine.security import rate_limit
    def attempt(_):
        with session_factory()() as db:
            try:
                rate_limit(db, "concurrent", 3, 3600)
                return True
            except HTTPException as exc:
                assert exc.status_code == 429
                return False
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(attempt, range(12)))
    assert sum(results) == 3


def test_concurrent_idempotent_submissions_share_one_job(postgres):
    user, dataset = postgres
    def submit(_):
        with session_factory()() as db:
            return enqueue(db, user, dataset, "Same request", {}, "retry-across-replicas").id
    with ThreadPoolExecutor(max_workers=8) as pool:
        ids = list(pool.map(submit, range(8)))
    assert len(set(ids)) == 1


def test_concurrent_storage_quota_is_atomic(postgres, tmp_path):
    import hashlib

    from engine.ingestion import DataError
    from engine.services import store_dataset
    user, _ = postgres
    settings().max_user_storage_bytes = 14  # Fixture already owns ten bytes.
    def upload(index):
        source = tmp_path / f"{index}.csv"
        source.write_bytes(b"x\n1\n")
        with session_factory()() as db:
            try:
                store_dataset(db, user, source.name, source, hashlib.sha256(b"x\n1\n").hexdigest(), 4)
                return True
            except DataError:
                return False
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(upload, range(8)))
    assert sum(results) == 1
