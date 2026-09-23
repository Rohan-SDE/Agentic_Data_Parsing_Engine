"""Polling intentionally includes boot-time backlog and avoids dependence on filesystem events."""
import hashlib
import logging
import os
import time

from sqlalchemy import select

from engine.config import settings
from engine.db import Dataset, Job, User, new_id, session_factory
from engine.ingestion import DataError, filename_format
from engine.services import enqueue, store_dataset

log = logging.getLogger("adpe.watcher")


def ingest_file(path, user_id):
    cfg = settings()
    filename_format(path.name)
    if path.is_symlink() or not path.is_file():
        raise DataError("Only regular files are accepted.")
    before = path.stat()
    if before.st_size > cfg.max_upload_bytes:
        raise DataError("File exceeds the upload size limit.")
    temp = cfg.data_dir / "work" / (new_id() + ".upload")
    checksum, size = hashlib.sha256(), 0
    try:
        # incoming is an operator-owned, trusted directory, never a network file share.
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "rb") as source, temp.open("xb") as target:
            temp.chmod(0o600)
            while chunk := source.read(1024 * 1024):
                size += len(chunk)
                if size > cfg.max_upload_bytes:
                    raise DataError("File exceeds the upload size limit.")
                target.write(chunk)
                checksum.update(chunk)
            target.flush()
            os.fsync(target.fileno())
        after = path.stat()
        if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
            return False
        with session_factory()() as db:
            existing = db.scalar(select(Dataset).where(Dataset.owner_id == user_id, Dataset.sha256 == checksum.hexdigest(),
                                                       Dataset.filename == path.name))
            dataset = existing or store_dataset(db, user_id, path.name, temp, checksum.hexdigest(), size)
            if not db.scalar(select(Job.id).where(Job.dataset_id == dataset.id).limit(1)):
                enqueue(db, user_id, dataset.id, "Inspect telemetry for quality issues, unusual values and relationships.", {})
        # processed means accepted into the durable queue; completion is tracked by job status.
        os.replace(path, cfg.data_dir / "processed" / (new_id() + "-" + path.name))
        return True
    finally:
        temp.unlink(missing_ok=True)


def scan(seen, user_id):
    cfg = settings()
    current = set()
    for path in (cfg.data_dir / "incoming").iterdir():
        if path.name.startswith(".") or path.suffix.lower() in {".part", ".tmp"} or path.is_symlink() or not path.is_file():
            continue
        current.add(path.name)
        stat = path.stat()
        signature = (stat.st_size, stat.st_mtime_ns)
        prior = seen.get(path.name)
        if not prior or prior[0] != signature:
            seen[path.name] = (signature, time.monotonic())
            continue
        if time.monotonic() - prior[1] < cfg.watch_stable_seconds:
            continue
        try:
            if ingest_file(path, user_id):
                seen.pop(path.name, None)
        except DataError:
            os.replace(path, cfg.data_dir / "rejected" / (new_id() + "-" + path.name))
            seen.pop(path.name, None)
            log.warning("Watcher rejected an input; inspect the rejected directory.")
    for name in set(seen) - current:
        del seen[name]


def run_watcher():
    from filelock import FileLock
    cfg = settings()
    cfg.prepare_dirs()
    with session_factory()() as db:
        user = db.scalar(select(User).where(User.username == cfg.watcher_user, User.active.is_(True)))
        if not user:
            raise ValueError("Set ADPE_WATCHER_USER to an active account before starting the watcher.")
        user_id = user.id
    seen = {}
    with FileLock(str(cfg.data_dir / "watcher.lock"), timeout=0):
        while True:
            try:
                scan(seen, user_id)
            except Exception as exc:
                log.error("Watcher iteration failed: %s", type(exc).__name__)
            time.sleep(2)
