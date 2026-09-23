import json
import logging
import os
import signal
import subprocess
import sys
import time

from sqlalchemy import and_, delete, or_, select, update

from engine.config import settings
from engine.db import Dataset, Job, WorkerHeartbeat, audit, new_id, session_factory

log = logging.getLogger("adpe.worker")


def heartbeat(worker_id):
    with session_factory()() as db:
        row = db.get(WorkerHeartbeat, worker_id)
        if row:
            row.seen_at = time.time()
        else:
            db.add(WorkerHeartbeat(id=worker_id, seen_at=time.time()))
        db.commit()


def write_identity(worker_id):
    path = settings().worker_identity_file
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + "." + worker_id)
    temp.write_text(json.dumps({"worker_id": worker_id, "pid": os.getpid()}), encoding="utf-8")
    temp.chmod(0o600)
    os.replace(temp, path)


def own_worker_healthy():
    """Health is tied to this container's worker, never to a different replica."""
    try:
        identity = json.loads(settings().worker_identity_file.read_text(encoding="utf-8"))
        os.kill(int(identity["pid"]), 0)
        with session_factory()() as db:
            beat = db.get(WorkerHeartbeat, identity["worker_id"])
            return bool(beat and beat.seen_at > time.time() - 60)
    except (OSError, ValueError, KeyError, TypeError):
        return False


def retire_worker(worker_id):
    try:
        with session_factory()() as db:
            db.execute(delete(WorkerHeartbeat).where(WorkerHeartbeat.id == worker_id))
            db.commit()
    finally:
        path = settings().worker_identity_file
        try:
            if json.loads(path.read_text()).get("worker_id") == worker_id:
                path.unlink(missing_ok=True)
        except (OSError, ValueError):
            pass


def claim_job():
    cfg = settings()
    now = time.time()
    eligible = or_(and_(Job.status == "queued", Job.available_at <= now),
                   and_(Job.status == "running", Job.lease_until < now))
    with session_factory()() as db:
        db.execute(update(Job).where(eligible, Job.attempts >= cfg.max_attempts).values(
            status="failed", error="Worker attempts exhausted.", finished_at=now, lease_token=None, lease_until=None))
        candidate = db.scalar(select(Job.id).where(eligible, Job.attempts < cfg.max_attempts)
                              .order_by(Job.created_at).with_for_update(skip_locked=True).limit(1))
        if not candidate:
            db.commit()
            return None
        token = new_id()
        claimed = db.execute(update(Job).where(Job.id == candidate, eligible, Job.attempts < cfg.max_attempts)
                             .values(status="running", lease_token=token, lease_until=now + cfg.lease_seconds,
                                     attempts=Job.attempts + 1, started_at=now, error=None)).rowcount
        db.commit()
        return (candidate, token) if claimed else None


def renew(job_id, token):
    with session_factory()() as db:
        count = db.execute(update(Job).where(Job.id == job_id, Job.status == "running", Job.lease_token == token)
                           .values(lease_until=time.time() + settings().lease_seconds)).rowcount
        db.commit()
        return bool(count)


def finish(job_id, token, output):
    cfg = settings()
    with session_factory()() as db:
        job = db.get(Job, job_id)
        if not job or job.status != "running" or job.lease_token != token:
            return False
        if output.get("ok"):
            changes = {"status": "completed", "result": output["result"], "error": None, "finished_at": time.time()}
        else:
            retry = output.get("retryable", False) and job.attempts < cfg.max_attempts
            changes = {"status": "queued" if retry else "failed", "error": output.get("error", "Analysis failed."),
                       "finished_at": None if retry else time.time(),
                       "available_at": time.time() + min(30, 2 ** job.attempts) if retry else time.time()}
        count = db.execute(update(Job).where(Job.id == job_id, Job.status == "running", Job.lease_token == token)
                           .values(**changes, lease_token=None, lease_until=None)).rowcount
        if count:
            audit(db, job.owner_id, "job." + changes["status"], job_id)
        db.commit()
        return bool(count)


def stop_process(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def execute_job(job_id, token, worker_id, stopping=lambda: False):
    cfg = settings()
    with session_factory()() as db:
        job = db.get(Job, job_id)
        if not job or job.status != "running" or job.lease_token != token:
            return
        dataset = db.get(Dataset, job.dataset_id)
        if not dataset:
            return
        spec = {"path": str((cfg.data_dir / "uploads" / dataset.storage_name).resolve()),
                "format": dataset.format, "objective": job.objective, "thresholds": job.thresholds,
                "dataset": {"id": dataset.id, "filename": dataset.filename, "sha256": dataset.sha256,
                            "size_bytes": dataset.size_bytes}}
    prefix = cfg.data_dir / "work" / token
    spec_path, output_path = prefix.with_suffix(".json"), prefix.with_suffix(".result")
    spec["output"] = str(output_path.resolve())
    process = None
    try:
        spec_path.write_text(json.dumps(spec), encoding="utf-8")
        spec_path.chmod(0o600)
        child_env = {**os.environ, "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1", "MKL_NUM_THREADS": "1"}
        process = subprocess.Popen([sys.executable, "-m", "engine.runner", str(spec_path.resolve())],
                                   env=child_env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        started = time.monotonic()
        while process.poll() is None:
            heartbeat(worker_id)
            if not renew(job_id, token):
                stop_process(process)
                return
            if stopping():
                stop_process(process)
                finish(job_id, token, {"ok": False, "retryable": True, "error": "Worker stopped; analysis interrupted."})
                return
            if time.monotonic() - started > cfg.job_timeout_seconds:
                stop_process(process)
                finish(job_id, token, {"ok": False, "retryable": False, "error": "Analysis exceeded its time limit."})
                return
            try:
                process.wait(timeout=min(2, cfg.lease_seconds / 3))
            except subprocess.TimeoutExpired:
                pass
        if output_path.exists() and output_path.stat().st_size <= 8 * 1024 * 1024:
            output = json.loads(output_path.read_text(encoding="utf-8"))
        else:
            output = {"ok": False, "retryable": True, "error": "Worker exited unexpectedly or exceeded a resource limit."}
        finish(job_id, token, output)
    except Exception as exc:
        log.error("Job execution failed: %s", type(exc).__name__, extra={"job_id": job_id})
        finish(job_id, token, {"ok": False, "retryable": True, "error": "Worker infrastructure error."})
    finally:
        if process:
            stop_process(process)
        spec_path.unlink(missing_ok=True)
        output_path.unlink(missing_ok=True)
        prefix.with_suffix(".tmp").unlink(missing_ok=True)


def run_worker(once=False):
    settings().prepare_dirs()
    worker_id = new_id()
    write_identity(worker_id)
    stopping = False
    def stop(*_):
        nonlocal stopping
        stopping = True
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        while not stopping:
            try:
                heartbeat(worker_id)
                claim = claim_job()
                if claim:
                    execute_job(*claim, worker_id, stopping=lambda: stopping)
                elif not once:
                    time.sleep(2)
                if once:
                    break
            except Exception as exc:
                log.error("Worker iteration failed: %s", type(exc).__name__)
                if once:
                    raise
                time.sleep(2)
    finally:
        retire_worker(worker_id)
