import time

import pytest
from alembic.config import Config
from pydantic import ValidationError
from sqlalchemy import func, inspect, select

from alembic import command
from engine.config import Settings
from engine.db import Dataset, Job, get_engine, session_factory
from engine.watcher import ingest_file, scan


def test_production_requires_secure_settings():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, environment="production")
    cfg = Settings(_env_file=None, environment="production", database_url="postgresql+psycopg://u:p@db/adpe",
                   secure_cookies=True, public_origin="https://data.example.com", allowed_hosts=["data.example.com"])
    assert cfg.secure_cookies


def test_migrations_roundtrip(environment):
    assert "jobs" in inspect(get_engine()).get_table_names()
    command.check(Config("alembic.ini"))
    command.downgrade(Config("alembic.ini"), "base")
    assert "jobs" not in inspect(get_engine()).get_table_names()
    command.upgrade(Config("alembic.ini"), "head")
    assert "jobs" in inspect(get_engine()).get_table_names()


def test_watcher_ingests_and_deduplicates(accounts, environment):
    path = environment.data_dir / "incoming" / "sensor.csv"
    for _ in range(2):
        path.write_text("temp\n20\n40\n")
        assert ingest_file(path, accounts[1].id)
        assert not path.exists()
    with session_factory()() as db:
        assert db.scalar(select(func.count()).select_from(Dataset)) == 1
        assert db.scalar(select(func.count()).select_from(Job)) == 1


def test_watcher_stability_and_rejection(accounts, environment):
    path = environment.data_dir / "incoming" / "sensor.csv"
    path.write_text("a\n1\n")
    seen = {}
    scan(seen, accounts[1].id)
    assert path.exists()
    seen[path.name] = (seen[path.name][0], time.monotonic() - 30)
    scan(seen, accounts[1].id)
    assert not path.exists()
    bad = environment.data_dir / "incoming" / "x.exe"
    bad.write_text("bad")
    scan(seen, accounts[1].id)
    seen[bad.name] = (seen[bad.name][0], time.monotonic() - 30)
    scan(seen, accounts[1].id)
    assert list((environment.data_dir / "rejected").iterdir())
