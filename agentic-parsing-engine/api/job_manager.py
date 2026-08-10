from datetime import datetime
from threading import Lock
from uuid import uuid4


class JobManager:
    def __init__(self):
        self.jobs = {}
        self.lock = Lock()

    def create_job(self, filename: str):
        job_id = str(uuid4())

        job = {
            "job_id": job_id,
            "filename": filename,
            "status": "queued",
            "stage": "queued",
            "progress": 0,
            "message": "Job created and waiting to start.",
            "created_at": datetime.now().isoformat(),
            "started_at": None,
            "completed_at": None,
            "result": None,
            "error": None,
        }

        with self.lock:
            self.jobs[job_id] = job

        return job

    def get_job(self, job_id: str):
        with self.lock:
            return self.jobs.get(job_id)

    def update_job(self, job_id: str, **updates):
        with self.lock:
            if job_id not in self.jobs:
                return None

            self.jobs[job_id].update(updates)
            return self.jobs[job_id]

    def start_job(self, job_id: str):
        return self.update_job(
            job_id,
            status="processing",
            stage="starting",
            progress=5,
            message="Processing job started.",
            started_at=datetime.now().isoformat(),
        )

    def complete_job(self, job_id: str, result=None):
        return self.update_job(
            job_id,
            status="completed",
            stage="completed",
            progress=100,
            message="Processing completed successfully.",
            completed_at=datetime.now().isoformat(),
            result=result,
        )

    def fail_job(self, job_id: str, error: str):
        return self.update_job(
            job_id,
            status="failed",
            stage="failed",
            progress=100,
            message="Processing failed.",
            completed_at=datetime.now().isoformat(),
            error=error,
        )


job_manager = JobManager()