"""Bounded routine maintenance and read-only restored-storage verification."""
import hashlib
import logging
import signal
import threading
import time

from sqlalchemy import delete, select

from engine.config import settings
from engine.db import AuthSession, Dataset, RateBucket, WorkerHeartbeat, session_factory

log = logging.getLogger("adpe.maintenance")


def cleanup():
    cutoff = time.time() - 86400
    with session_factory()() as db:
        db.execute(delete(AuthSession).where(AuthSession.expires_at < time.time()))
        db.execute(delete(RateBucket).where(RateBucket.expires_at < time.time()))
        db.execute(delete(WorkerHeartbeat).where(WorkerHeartbeat.seen_at < cutoff))
        db.commit()
    removed = 0
    for folder in ("work", "uploads"):
        for path in (settings().data_dir / folder).iterdir():
            try:
                if path.is_symlink() or not path.is_file() or path.stat().st_mtime >= cutoff:
                    continue
                if folder == "uploads":
                    with session_factory()() as db:
                        if db.scalar(select(Dataset.id).where(Dataset.storage_name == path.name)):
                            continue
                path.unlink(missing_ok=True)
                removed += 1
            except FileNotFoundError:
                pass  # Another cleanup process may have removed the same orphan.
    return removed


def verify_storage():
    """Run with writers stopped, so concurrent deletions cannot invalidate results."""
    checked, failures = 0, 0
    after = ""
    while True:
        # Close the DB transaction before potentially slow disk reads.
        with session_factory()() as db:
            rows = list(db.scalars(select(Dataset).where(Dataset.id > after).order_by(Dataset.id).limit(100)))
        if not rows:
            break
        for row in rows:
            checked += 1
            path = settings().data_dir / "uploads" / row.storage_name
            try:
                if path.is_symlink() or path.parent != settings().data_dir / "uploads":
                    raise ValueError("Invalid source path")
                with path.open("rb") as stream:
                    valid = path.stat().st_size == row.size_bytes
                    valid = valid and hashlib.file_digest(stream, "sha256").hexdigest() == row.sha256
                failures += not valid
            except (OSError, ValueError):
                failures += 1
        after = rows[-1].id
    return {"checked": checked, "failures": failures}


def run_maintenance():
    stopped = threading.Event()
    def stop(*_):
        stopped.set()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    while not stopped.is_set():
        try:
            removed = cleanup()
            log.info("cleanup_completed", extra={"removed": removed})
            delay = 86400
        except Exception as exc:
            log.error("Cleanup failed: %s", type(exc).__name__)
            delay = 60
        stopped.wait(delay)
