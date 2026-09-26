# API guide

Base URL for local development: `http://localhost:8000`.

Authentication uses an HttpOnly session cookie. `POST /api/auth/login` returns a CSRF token. Keep the cookie in a session and send `X-CSRF-Token` on every subsequent POST/DELETE. Browser mutation requests must also have the configured same origin. There is no public signup, default credential, JWT or long-lived API key.

## Complete Python example

```python
import getpass
import time
from pathlib import Path
import httpx

with httpx.Client(base_url="http://localhost:8000", timeout=180) as client:
    login = client.post("/api/auth/login", json={
        "username": "rohan", "password": getpass.getpass("Password: ")
    })
    login.raise_for_status()
    client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
    path = Path("examples/drone_telemetry.csv")
    with path.open("rb") as stream:
        upload = client.post("/api/datasets", params={"filename": path.name},
                             content=stream, headers={"Content-Type": "application/octet-stream"})
    upload.raise_for_status()
    job = client.post("/api/jobs", json={
        "dataset_id": upload.json()["id"],
        "objective": "Investigate motor temperature and voltage.",
        "thresholds": {"motor_temperature": {"max": 80}, "battery_voltage": {"min": 10}}
    })
    job.raise_for_status()
    job_id = job.json()["id"]
    deadline = time.monotonic() + 420
    while time.monotonic() < deadline:
        response = client.get(f"/api/jobs/{job_id}")
        response.raise_for_status()
        result = response.json()
        if result["status"] in {"completed", "failed", "cancelled"}:
            break
        time.sleep(2)
    else:
        raise TimeoutError("Job remains active; inspect worker health.")
    if result["status"] != "completed":
        raise RuntimeError(result.get("error") or result["status"])
    response = client.get(f"/api/jobs/{job_id}/report", params={"format": "json"})
    response.raise_for_status()
    Path("analysis.json").write_bytes(response.content)
    client.post("/api/auth/logout").raise_for_status()
```

Uploads use a raw request body, **not multipart/form-data**. The filename is a query parameter and never controls the storage path. Receiving HTTP 201 means bytes were accepted; full syntax validation occurs in the analysis worker.

## Routes

| Method | Path | Purpose |
|---|---|---|
| GET | `/health/live` | Process health |
| GET | `/health/ready` | Database, storage probe and recent worker heartbeat; 503 if unavailable |
| POST | `/api/auth/login` | Establish session; username/password JSON |
| GET | `/api/auth/me` | Current user and CSRF token |
| POST | `/api/auth/logout` | Revoke current session |
| GET | `/api/config` | Public-to-account upload/model settings |
| POST | `/api/datasets?filename=...` | Upload raw bytes |
| GET | `/api/datasets?limit=50&offset=0` | Owner's dataset inventory |
| DELETE | `/api/datasets/{id}` | Delete dataset and all reports; rejects active jobs |
| POST | `/api/jobs` | Submit dataset_id, objective and optional thresholds |
| GET | `/api/jobs?limit=50&offset=0` | Owner's job history |
| GET | `/api/jobs/{id}` | Status, thresholds and completed result |
| POST | `/api/jobs/{id}/cancel` | Cancel a queued/running job |
| POST | `/api/jobs/{id}/retry` | Create a new run from a failed/cancelled job |
| GET | `/api/jobs/{id}/report?format=json` | JSON, `md` or `pdf` attachment |
| GET/POST | `/api/admin/users` | List/create accounts (admin) |
| POST | `/api/admin/users/{id}/disable` | Disable account and revoke sessions (admin) |
| GET | `/api/admin/audit?limit=50&offset=0` | Audit events (admin) |

Pagination limits are 1–100. Dataset and job lists include `total`; admin lists return `items` and support offsets. Administrators cannot read another account's datasets or reports through these endpoints.

## Result contract

Completed jobs contain `result.status` (`complete` or `partial`), engine version, objective, planner metadata, dataset checksum, row/column/sample counts, schema, per-tool outputs, tool trace, findings and limitations. Each finding includes type, severity, field, supporting evidence, recommended action, confidence meaning and observation scope.

Job status `completed` means the analysis process returned a report; check `result.status` before treating every check as successful. `findings_total` may exceed the returned list capped at 250. Negative results are not guarantees of data correctness.

## Errors

| Code | Meaning |
|---|---|
| 401 | Missing/expired session or invalid credentials |
| 403 | CSRF/origin mismatch or insufficient role |
| 404 | Resource absent or owned by someone else |
| 409 | Invalid job state or active dataset deletion |
| 413 | Request/upload exceeds configured size |
| 422 | Invalid request, unsupported file, quota or input constraint |
| 429 | Rate limit exceeded; respect Retry-After |
| 503 | Readiness dependency unavailable |

Responses include `X-Request-ID`. Logs exclude request bodies, query strings and credentials. Do not blindly retry a timed-out POST: inspect the dataset/job lists first, because the original request may have committed. Submission idempotency keys are not implemented in this release.

## Safe submission retries

Send `Idempotency-Key` on `POST /api/jobs` or a retry endpoint when network retries are possible. Keys must contain 8–128 letters, digits, underscores, dots, colons or hyphens. Reusing a key with the same owner, dataset, objective and thresholds returns the original job; a different payload returns 409. Keys remain associated with the job until its dataset/jobs are deleted. The dashboard preserves the key when a submission fails and the user retries the same request. Uploads themselves are not idempotent.

Request JSON is limited to 32 KiB and a 15-second body deadline; uploads have the configured size limit and a 180-second total body deadline by default. A slow body returns 408, and an oversized body returns 413. Password-hashing concurrency is bounded per API process; overload returns 429 with Retry-After.

## Public registration

`GET /api/auth/options` returns `public_registration`. When enabled, `POST /api/auth/register` accepts only `username` and `password`, returning 201 with username and `is_admin: false`. Sign in separately to create a session. Disabled: 403; validation: 422; username conflict: 409; rate limit: 429.
