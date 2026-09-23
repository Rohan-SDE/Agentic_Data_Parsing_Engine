"""Disposable Docker acceptance run. Uses its own Compose project and removes ONLY that project."""
import json
import os
import secrets
import subprocess
import time

import httpx


def main():
    # Port 8000 must be free; run this on a disposable CI runner, not the production host.
    project = "adpe-ci-" + secrets.token_hex(4)
    env = {**os.environ, "POSTGRES_PASSWORD": secrets.token_hex(32), "ADPE_TEST_PASSWORD": secrets.token_urlsafe(24)}
    prefix = ["docker", "compose", "-p", project]
    try:
        subprocess.run(prefix + ["up", "--build", "-d"], env=env, check=True)
        with httpx.Client(base_url="http://localhost:8000", timeout=10) as client:
            ready = False
            for _ in range(90):
                try:
                    ready = client.get("/health/ready").status_code == 200
                except httpx.HTTPError:
                    pass
                if ready:
                    break
                time.sleep(2)
            assert ready, "Services did not become ready"
            code = "from engine.db import session_factory; from engine.security import create_user; import os; " \
                   "db=session_factory()(); create_user(db,'smokeuser',os.environ['ADPE_TEST_PASSWORD'],True); db.commit()"
            subprocess.run(prefix + ["exec", "-T", "-e", "ADPE_TEST_PASSWORD", "api", "python", "-c", code],
                           env=env, check=True)
            login = client.post("/api/auth/login", json={"username": "smokeuser", "password": env["ADPE_TEST_PASSWORD"]})
            login.raise_for_status()
            client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
            upload = client.post("/api/datasets", params={"filename": "smoke.csv"}, content=b"x,y\n1,2\n3,4\n")
            upload.raise_for_status()
            result = client.post("/api/jobs", json={"dataset_id": upload.json()["id"]})
            result.raise_for_status()
            job_id = result.json()["id"]
            for _ in range(60):
                job = client.get("/api/jobs/" + job_id).json()
                if job["status"] in {"completed", "failed"}:
                    break
                time.sleep(1)
            assert job["status"] == "completed", job
            assert job["result"]["rows"] == 2
            pdf = client.get(f"/api/jobs/{job_id}/report?format=pdf")
            assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
            print(json.dumps({"docker_acceptance": "passed", "database": "PostgreSQL", "rows": 2}))
    finally:
        subprocess.run(prefix + ["down", "--volumes"], env=env, check=False)


if __name__ == "__main__":
    main()
