from threading import Lock
from uuid import uuid4
from datetime import datetime


_jobs = {}
_jobs_lock = Lock()


def create_job(filename: str):
    job_id = str(uuid4())

    job = {
        "job_id": job_id,
        "filename": filename,
        "status": "queued",
        "stage": "queued",
        "progress": 0,
        "message": "Job created",
        "created_at": datetime.now().isoformat(),
        "completed_at": None,
        "error": None,
        "results": {}
    }

    with _jobs_lock:
        _jobs[job_id] = job

    return job


def get_job(job_id: str):
    with _jobs_lock:
        return _jobs.get(job_id)


def update_job(job_id: str, **updates):
    with _jobs_lock:
        if job_id in _jobs:
            _jobs[job_id].update(updates)
            return _jobs[job_id]

    return None


def complete_job(job_id: str, results=None):
    with _jobs_lock:
        if job_id in _jobs:
            _jobs[job_id].update({
                "status": "completed",
                "stage": "completed",
                "progress": 100,
                "message": "Processing completed successfully",
                "completed_at": datetime.now().isoformat(),
                "results": results or {}
            })

            return _jobs[job_id]

    return None


def fail_job(job_id: str, error: str):
    with _jobs_lock:
        if job_id in _jobs:
            _jobs[job_id].update({
                "status": "failed",
                "stage": "failed",
                "message": "Processing failed",
                "error": error
            })

            return _jobs[job_id]

    return None