import os
import time

from sqlalchemy import func, select

from engine.config import settings
from engine.db import Dataset, Job, User, audit, new_id
from engine.ingestion import DataError, filename_format


def store_dataset(db, user_id, filename, staged_path, sha256, size):
    cfg = settings()
    fmt = filename_format(filename)
    if not 0 < size <= cfg.max_upload_bytes:
        raise DataError("File is empty or exceeds the upload size limit.")
    # Serialize storage and queue quota decisions across all API replicas for this account.
    user = db.scalar(select(User).where(User.id == user_id).with_for_update().execution_options(populate_existing=True))
    if not user or not user.active:
        raise DataError("Account is unavailable.")
    used = db.scalar(select(func.coalesce(func.sum(Dataset.size_bytes), 0)).where(Dataset.owner_id == user_id))
    if used + size > cfg.max_user_storage_bytes:
        raise DataError("Account storage quota exceeded; delete an unused dataset.")
    did = new_id()
    storage_name = f"{did}.{fmt}"
    target = cfg.data_dir / "uploads" / storage_name
    os.replace(staged_path, target)
    os.chmod(target, 0o600)
    if hasattr(os, "O_DIRECTORY"):
        directory_fd = os.open(target.parent, os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    dataset = Dataset(id=did, owner_id=user_id, filename=filename, format=fmt,
                      storage_name=storage_name, sha256=sha256, size_bytes=size)
    try:
        db.add(dataset)
        audit(db, user_id, "dataset.upload", did)
        db.commit()
    except BaseException:
        db.rollback()
        # A failed commit acknowledgement does not prove the transaction was rolled back.
        # Keep bytes intact; cleanup removes only unreferenced files after 24 hours.
        raise
    return dataset


def enqueue(db, user_id, dataset_id, objective, thresholds, request_key=None):
    cfg = settings()
    user = db.scalar(select(User).where(User.id == user_id).with_for_update().execution_options(populate_existing=True))
    if not user or not user.active:
        raise DataError("Account is unavailable.")
    if request_key:
        previous = db.scalar(select(Job).where(Job.owner_id == user_id, Job.request_key == request_key))
        if previous:
            if (previous.dataset_id, previous.objective, previous.thresholds) != (dataset_id, objective, thresholds):
                from fastapi import HTTPException
                raise HTTPException(409, "Idempotency key was already used for a different request.")
            db.commit()
            return previous
    dataset = db.scalar(select(Dataset).where(Dataset.id == dataset_id, Dataset.owner_id == user_id)
                        .with_for_update())
    if not dataset:
        raise DataError("Dataset not found.")
    active = db.scalar(select(func.count()).select_from(Job).where(Job.owner_id == user_id,
                                                                  Job.status.in_(["queued", "running"])))
    if active >= cfg.max_pending_jobs:
        raise DataError("Active job quota reached; wait for a job to finish.")
    job = Job(owner_id=user_id, dataset_id=dataset_id, objective=objective, thresholds=thresholds,
              status="queued", created_at=time.time(), request_key=request_key)
    db.add(job)
    db.flush()
    audit(db, user_id, "job.create", job.id)
    db.commit()
    return job
