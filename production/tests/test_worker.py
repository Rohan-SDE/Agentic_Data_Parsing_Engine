import time

from engine.db import Job, session_factory
from engine.worker import claim_job, execute_job, finish, renew


def test_claim_only_once(queued):
    claim = claim_job()
    assert claim[0] == queued[1].id
    assert claim_job() is None
    assert renew(*claim)


def test_expired_lease_reclaimed_and_old_result_rejected(queued):
    old = claim_job()
    with session_factory()() as db:
        db.get(Job, old[0]).lease_until = time.time() - 5
        db.commit()
    new = claim_job()
    assert new[0] == old[0] and new[1] != old[1]
    assert not finish(*old, {"ok": True, "result": {"wrong": True}})
    assert finish(*new, {"ok": True, "result": {"correct": True}})


def test_cancel_fences_late_completion(queued):
    claim = claim_job()
    with session_factory()() as db:
        db.get(Job, claim[0]).status = "cancelled"
        db.commit()
    assert not renew(*claim)
    assert not finish(*claim, {"ok": True, "result": {}})


def test_bounded_retry_and_permanent_failure(queued, environment):
    claim = claim_job()
    finish(*claim, {"ok": False, "retryable": True, "error": "transient"})
    assert claim_job() is None  # Backoff prevents tight retry loops.
    with session_factory()() as db:
        db.get(Job, claim[0]).available_at = time.time() - 1
        db.commit()
    second = claim_job()
    assert second
    finish(*second, {"ok": False, "retryable": False, "error": "Invalid file"})
    assert claim_job() is None
    with session_factory()() as db:
        job = db.get(Job, claim[0])
        assert job.status == "failed" and job.attempts == 2


def test_worker_exhaustion(queued, environment):
    claim = claim_job()
    with session_factory()() as db:
        job = db.get(Job, claim[0])
        job.attempts = environment.max_attempts
        job.lease_until = time.time() - 1
        db.commit()
    assert claim_job() is None
    with session_factory()() as db:
        assert db.get(Job, claim[0]).status == "failed"


def test_real_subprocess_and_report_exports(signed, queued, environment):
    claim = claim_job()
    execute_job(*claim, "test-worker")
    with session_factory()() as db:
        job = db.get(Job, claim[0])
        assert job.status == "completed", job.error
        assert job.result["rows"] == 51
        assert job.result["dataset"]["sha256"] == queued[0].sha256
    for fmt, prefix in [("json", b"{"), ("md", b"# Agentic"), ("pdf", b"%PDF")]:
        response = signed.get(f"/api/jobs/{claim[0]}/report?format={fmt}")
        assert response.status_code == 200, response.text
        assert response.content.startswith(prefix)
    assert not list((environment.data_dir / "work").iterdir())
