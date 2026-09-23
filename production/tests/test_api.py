import time

from engine.db import AuthSession, Job, session_factory
from tests.conftest import sign_in


def upload(client, filename="test.csv", body=b"a,b\n1,2\n3,4\n"):
    return client.post("/api/datasets", params={"filename": filename}, content=body,
                       headers={"Content-Type": "application/octet-stream"})


def test_authentication_required(client):
    assert client.get("/api/datasets").status_code == 401
    assert client.get("/api/jobs").status_code == 401


def test_session_cookie_csrf_and_logout(client):
    response = sign_in(client)
    assert "HttpOnly" in response.headers["set-cookie"]
    assert "SameSite=strict" in response.headers["set-cookie"]
    assert client.get("/api/auth/me").status_code == 200
    csrf = client.headers.pop("X-CSRF-Token")
    assert upload(client).status_code == 403
    client.headers["X-CSRF-Token"] = csrf
    assert client.post("/api/auth/logout").status_code == 204
    assert client.get("/api/auth/me").status_code == 401


def test_cross_origin_blocked_and_headers(signed):
    assert signed.post("/api/auth/logout", headers={"Origin": "https://evil.example"}).status_code == 403
    response = signed.get("/")
    assert response.status_code == 200
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert response.headers["x-content-type-options"] == "nosniff"
    assert signed.get("/", headers={"Host": "evil.example"}).status_code == 400


def test_session_expiry(signed):
    from sqlalchemy import update
    with session_factory()() as db:
        db.execute(update(AuthSession).values(expires_at=time.time() - 1))
        db.commit()
    assert signed.get("/api/auth/me").status_code == 401


def test_invalid_login_and_rate_limit(client):
    for _ in range(10):
        assert client.post("/api/auth/login", json={"username": "unknown", "password": "incorrect"}).status_code == 401
    assert client.post("/api/auth/login", json={"username": "unknown", "password": "incorrect"}).status_code == 429


def test_upload_create_cancel_delete(signed):
    response = upload(signed)
    assert response.status_code == 201, response.text
    dataset = response.json()
    response = signed.post("/api/jobs", json={"dataset_id": dataset["id"]})
    assert response.status_code == 202
    job = response.json()
    assert signed.delete("/api/datasets/" + dataset["id"]).status_code == 409
    assert signed.get(f"/api/jobs/{job['id']}/report").status_code == 409
    assert signed.post(f"/api/jobs/{job['id']}/cancel").status_code == 200
    assert signed.post(f"/api/jobs/{job['id']}/cancel").status_code == 409
    assert signed.delete("/api/datasets/" + dataset["id"]).status_code == 204
    assert signed.get("/api/jobs").json()["total"] == 0


def test_account_isolation(signed, queued):
    dataset, job = queued
    sign_in(signed, "other")
    assert signed.get("/api/datasets").json()["items"] == []
    for path in [f"/api/jobs/{job.id}", f"/api/jobs/{job.id}/report"]:
        assert signed.get(path).status_code == 404
    assert signed.post(f"/api/jobs/{job.id}/cancel").status_code == 404
    assert signed.delete(f"/api/datasets/{dataset.id}").status_code == 404
    assert signed.post("/api/jobs", json={"dataset_id": dataset.id}).status_code == 404
    assert signed.get("/api/admin/audit").status_code == 403


def test_upload_rejects_path_empty_and_oversize(signed, environment):
    assert upload(signed, "../secret.csv").status_code == 422
    assert upload(signed, "x.exe").status_code == 422
    assert upload(signed, body=b"").status_code == 422
    environment.max_upload_bytes = 1024
    assert upload(signed, body=b"x" * 1025).status_code == 413
    assert list((environment.data_dir / "work").glob("*.upload")) == []


def test_json_body_is_bounded(signed):
    response = signed.post("/api/jobs", content=b"x" * 32769, headers={"Content-Type": "application/json"})
    assert response.status_code == 413


def test_storage_and_job_quotas(signed, environment):
    environment.max_user_storage_bytes = 20
    dataset = upload(signed).json()
    assert upload(signed).status_code == 422
    environment.max_pending_jobs = 1
    assert signed.post("/api/jobs", json={"dataset_id": dataset["id"]}).status_code == 202
    assert signed.post("/api/jobs", json={"dataset_id": dataset["id"]}).status_code == 422


def test_threshold_validation(signed, queued):
    for thresholds in [{"a": {}}, {"a": {"min": 10, "max": 1}}, {"a": {"max": "nan"}}]:
        assert signed.post("/api/jobs", json={"dataset_id": queued[0].id, "thresholds": thresholds}).status_code == 422


def test_admin_create_disable_and_audit(signed, accounts):
    sign_in(signed, "admin")
    response = signed.post("/api/admin/users", json={"username": "newanalyst", "password": "Strong-Example-Password!"})
    assert response.status_code == 201
    assert signed.post(f"/api/admin/users/{accounts[0].id}/disable").status_code == 409
    assert signed.post(f"/api/admin/users/{accounts[2].id}/disable").status_code == 204
    assert any(e["action"] == "user.disable" for e in signed.get("/api/admin/audit").json()["items"])
    response = signed.post("/api/auth/login", json={"username": "other", "password": "Test-only-Password-2026!"})
    assert response.status_code == 401


def test_retry_retains_original(signed, queued):
    with session_factory()() as db:
        job = db.get(Job, queued[1].id)
        job.status = "failed"
        job.error = "Malformed input"
        db.commit()
    response = signed.post(f"/api/jobs/{queued[1].id}/retry")
    assert response.status_code == 202
    assert response.json()["id"] != queued[1].id
    assert signed.get(f"/api/jobs/{queued[1].id}").json()["status"] == "failed"


def test_readiness_requires_worker(client):
    assert client.get("/health/live").status_code == 200
    assert client.get("/health/ready").status_code == 503
    from engine.worker import heartbeat
    heartbeat("test-worker")
    assert client.get("/health/ready").status_code == 200
