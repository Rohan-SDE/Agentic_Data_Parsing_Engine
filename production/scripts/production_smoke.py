"""Disposable production-profile TLS, process recovery, and matched-restore acceptance.

Only run on an isolated CI runner with ports 80/443 free. Never targets an existing project.
"""
import json
import os
import secrets
import ssl
import subprocess
import tempfile
import time
from pathlib import Path

import httpx
from configure_production import configure
from recovery import Compose, backup, restore

ROOT = Path(__file__).resolve().parents[1]


def tls_client(compose, destination):
    cert = destination / "root.crt"
    for _ in range(60):
        try:
            compose.run("cp", "proxy:/data/caddy/pki/authorities/local/root.crt", str(cert),
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            break
        except subprocess.CalledProcessError:
            time.sleep(1)
    return httpx.Client(base_url="https://localhost", verify=ssl.create_default_context(cafile=str(cert)), timeout=10)


def wait_ready(client):
    for _ in range(90):
        try:
            if client.get("/health/ready").status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(1)
    raise AssertionError("Production services did not become ready")


def login(client, password):
    response = client.post("/api/auth/login", json={"username": "smokeuser", "password": password})
    response.raise_for_status()
    cookie = response.headers["set-cookie"].lower()
    assert "secure" in cookie and "httponly" in cookie and "samesite=strict" in cookie
    client.headers["X-CSRF-Token"] = response.json()["csrf_token"]


def main():
    os.chdir(ROOT)
    # Refuses existing settings. Test secrets never overwrite operator settings.
    configure(ROOT, "localhost", "ci@example.com")
    project = "adpe-prod-ci-" + secrets.token_hex(4)
    source = Compose(project, ROOT / "compose.production.yaml", ROOT / ".env.production")
    recovery = Compose(project + "-recovery", ROOT / "compose.production.yaml", ROOT / ".env.production")
    out = ROOT / "test-results"
    out.mkdir(exist_ok=True)
    password = secrets.token_urlsafe(24)
    try:
        source.require_empty()
        source.run("config", "--quiet")
        source.run("up", "--build", "-d")
        source.run("exec", "-T", "proxy", "caddy", "validate", "--config", "/etc/caddy/Caddyfile")
        privileges = "from engine.db import get_engine; from sqlalchemy import text; " \
            "c=get_engine().connect(); r=c.execute(text(\"SELECT rolsuper,rolcreaterole,rolcreatedb FROM pg_roles " \
            "WHERE rolname=current_user\")).one(); assert not any(r); " \
            "assert not c.scalar(text(\"SELECT has_schema_privilege(current_user,'public','CREATE')\"))"
        source.run("exec", "-T", "api", "python", "-c", privileges)
        code = "from engine.db import session_factory; from engine.security import create_user; import os; " \
               "db=session_factory()(); create_user(db,'smokeuser',os.environ['ADPE_TEST_PASSWORD'],True); db.commit()"
        source.run("exec", "-T", "-e", "ADPE_TEST_PASSWORD", "api", "python", "-c", code,
                   env={**os.environ, "ADPE_TEST_PASSWORD": password})
        with tempfile.TemporaryDirectory(prefix="adpe-recovery-ci-") as tmp:
            destination = Path(tmp)
            with tls_client(source, destination) as client:
                wait_ready(client)
                assert client.get("/docs").status_code == client.get("/openapi.json").status_code == 404
                assert "max-age=" in client.get("/health/live").headers["strict-transport-security"]
                login(client, password)
                response = client.post("/api/datasets", params={"filename": "smoke.csv"}, content=b"x,y\n1,2\n3,4\n")
                response.raise_for_status()
                body = {"dataset_id": response.json()["id"]}
                headers = {"Idempotency-Key": "production-smoke-key"}
                response = client.post("/api/jobs", json=body, headers=headers)
                response.raise_for_status()
                job_id = response.json()["id"]
                assert client.post("/api/jobs", json=body, headers=headers).json()["id"] == job_id
                for _ in range(60):
                    response = client.get("/api/jobs/" + job_id)
                    response.raise_for_status()
                    job = response.json()
                    if job["status"] in {"completed", "failed"}:
                        break
                    time.sleep(1)
                assert job["status"] == "completed" and job["result"]["rows"] == 2
                source.run("kill", "-s", "SIGKILL", "worker")
                source.run("up", "-d", "worker")
                source.run("restart", "api")
                wait_ready(client)
                assert client.get(f"/api/jobs/{job_id}/report?format=pdf").content.startswith(b"%PDF")
            started = time.monotonic()
            backup(source, destination / "matched")
            source.run("down")  # Preserve source volumes. Release CI-only TLS ports and subnet.
            restore(recovery, destination / "matched")
            recovery.run("up", "-d", "--no-deps", "proxy")
            with tls_client(recovery, destination) as client:
                wait_ready(client)
                login(client, password)
                report = client.get(f"/api/jobs/{job_id}/report?format=json")
                report.raise_for_status()
                assert report.json()["rows"] == 2
                assert client.get(f"/api/jobs/{job_id}/report?format=pdf").content.startswith(b"%PDF")
            result = {"status": "passed", "profile": "production", "tls": "verified local CA",
                      "checks": ["restricted database role", "secure cookies", "docs disabled", "idempotency", "real PostgreSQL and worker",
                                 "process restart", "matched backup", "restore", "all source checksums", "retained PDF"],
                      "backup_and_recovery_seconds": round(time.monotonic() - started, 2)}
            (out / "production-result.json").write_text(json.dumps(result, indent=2))
            print(json.dumps(result))
    finally:
        for compose in (source, recovery):
            with (out / (compose.project + ".log")).open("w") as log:
                subprocess.run(compose.prefix + ["logs", "--no-color", "--tail", "100"], stdout=log, stderr=log)
            subprocess.run(compose.prefix + ["down", "--volumes"], check=False)


if __name__ == "__main__":
    main()
