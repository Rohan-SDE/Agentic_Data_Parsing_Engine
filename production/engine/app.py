import asyncio
import hashlib
import logging
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool
from starlette.middleware.trustedhost import TrustedHostMiddleware

from engine import __version__, reports
from engine.config import settings
from engine.db import (
    SCHEMA_REVISION,
    Audit,
    AuthSession,
    Dataset,
    Job,
    User,
    WorkerHeartbeat,
    audit,
    get_db,
    new_id,
    session_factory,
)
from engine.ingestion import DataError, filename_format
from engine.security import (
    DUMMY_HASH,
    authenticate,
    create_user,
    digest,
    hash_password,
    hasher,
    password_ok,
    rate_limit,
)
from engine.services import enqueue, store_dataset

log = logging.getLogger("adpe.api")
DB = Annotated[Session, Depends(get_db)]
STATIC = Path(__file__).parent / "static"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Login(StrictModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class NewUser(Login):
    is_admin: bool = False


class Limits(StrictModel):
    min: float | None = Field(None, ge=-1e100, le=1e100)
    max: float | None = Field(None, ge=-1e100, le=1e100)

    @model_validator(mode="after")
    def bounds(self):
        if self.min is None and self.max is None:
            raise ValueError("Specify min or max.")
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError("min cannot exceed max.")
        return self


class NewJob(StrictModel):
    dataset_id: str = Field(min_length=36, max_length=36)
    objective: str = Field("Identify data quality issues, unusual measurements and relationships.", min_length=3, max_length=2000)
    thresholds: dict[str, Limits] = Field(default_factory=dict, max_length=128)


def current(request: Request, db: DB):
    return authenticate(request, db)


AUTH = Annotated[tuple, Depends(current)]


def admin_only(auth):
    if not auth[0].is_admin:
        raise HTTPException(403, "Administrator access required.")
    return auth[0]


def dataset_view(item):
    return {"id": item.id, "filename": item.filename, "format": item.format, "size_bytes": item.size_bytes,
            "sha256": item.sha256, "created_at": item.created_at}


def job_view(item, with_result=False):
    out = {k: getattr(item, k) for k in ("id", "dataset_id", "objective", "status", "attempts", "created_at",
                                         "started_at", "finished_at", "error")}
    if with_result:
        out["result"] = item.result
        out["thresholds"] = item.thresholds
    return out


def owned_job(db, job_id, user):
    item = db.scalar(select(Job).where(Job.id == job_id, Job.owner_id == user.id))
    if not item:
        raise HTTPException(404, "Job not found.")
    return item


class BodyLimitMiddleware:
    """Bound request bodies before FastAPI parses JSON, including chunked requests."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        is_upload = scope["path"] == "/api/datasets" and scope["method"] == "POST"
        limit = settings().max_upload_bytes if is_upload else 32768
        timeout = settings().upload_timeout_seconds if is_upload else settings().request_body_timeout_seconds
        deadline = time.monotonic() + timeout
        total = 0
        async def limited_receive():
            nonlocal total
            remaining = deadline - time.monotonic()
            try:
                if remaining <= 0:
                    raise TimeoutError
                message = await asyncio.wait_for(receive(), timeout=remaining)
            except TimeoutError as exc:
                raise HTTPException(408, "Request body took too long to upload.") from exc
            if message["type"] == "http.request":
                total += len(message.get("body", b""))
                if total > limit:
                    raise HTTPException(413, "Request exceeds the size limit.")
            return message
        headers = dict(scope["headers"])
        try:
            length = int(headers.get(b"content-length", b"0"))
        except ValueError:
            return await JSONResponse({"detail": "Invalid Content-Length."}, status_code=400)(scope, receive, send)
        if length < 0:
            return await JSONResponse({"detail": "Invalid Content-Length."}, status_code=400)(scope, receive, send)
        if length > limit:
            return await JSONResponse({"detail": "Request exceeds the size limit."}, status_code=413)(scope, receive, send)
        return await self.app(scope, limited_receive, send)


@asynccontextmanager
async def lifespan(app):
    settings().prepare_dirs()
    if settings().environment == "production":
        with session_factory()() as db:
            if db.scalar(text("SELECT version_num FROM alembic_version")) != SCHEMA_REVISION:
                raise RuntimeError("Database schema is not current; run migrations before starting the API.")
    yield


app = FastAPI(title="Agentic Data Parsing Engine", version=__version__, lifespan=lifespan,
              docs_url=None, redoc_url=None,
              openapi_url="/openapi.json" if settings().environment != "production" else None)
app.add_middleware(BodyLimitMiddleware)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings().allowed_hosts)


@app.middleware("http")
async def security_headers(request, call_next):
    request_id = new_id()
    start = time.monotonic()
    origin = request.headers.get("origin")
    if request.method not in {"GET", "HEAD", "OPTIONS"} and origin and origin != settings().public_origin.rstrip("/"):
        response = JSONResponse({"detail": "Cross-origin requests are not allowed."}, status_code=403)
    else:
        try:
            response = await call_next(request)
        except Exception as exc:
            log.error("Request failed: %s", type(exc).__name__, extra={"request_id": request_id})
            response = JSONResponse({"detail": "Internal server error.", "request_id": request_id}, status_code=500)
    response.headers.update({"X-Request-ID": request_id, "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer",
        "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
        "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
                                   "connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
        "Cache-Control": "no-store" if request.url.path.startswith("/api") else "no-cache"})
    if settings().secure_cookies:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    # Never log query strings, filenames, objectives, credentials or dataset values.
    log.info("http_request", extra={"request_id": request_id, "method": request.method,
                                    "status": response.status_code, "duration_ms": round((time.monotonic()-start)*1000)})
    return response


@app.exception_handler(DataError)
async def data_error(request, exc):
    return JSONResponse({"detail": str(exc)}, status_code=422)


@app.get("/health/live")
def live():
    return {"status": "alive", "version": __version__}


@app.get("/health/ready")
def ready(db: DB):
    try:
        if db.scalar(text("SELECT version_num FROM alembic_version")) != SCHEMA_REVISION:
            return JSONResponse({"status": "migration_required"}, status_code=503)
        recent = db.scalar(select(func.max(WorkerHeartbeat.seen_at)))
        healthy = recent is not None and recent > time.time() - 60
        probe = settings().data_dir / "work" / (new_id() + ".probe")
        probe.write_text("ok")
        probe.unlink()
    except Exception:
        return JSONResponse({"status": "not_ready"}, status_code=503)
    return JSONResponse({"status": "ready" if healthy else "worker_unavailable"}, status_code=200 if healthy else 503)


@app.post("/api/auth/login")
def login(body: Login, request: Request, response: Response, db: DB):
    rate_limit(db, "login-ip:" + (request.client.host if request.client else "unknown"), 20, 300)
    rate_limit(db, "login-user:" + body.username.lower(), 10, 300)
    user = db.scalar(select(User).where(User.username == body.username.lower()))
    valid = password_ok(user.password_hash if user else DUMMY_HASH, body.password)
    if not valid or not user or not user.active:
        audit(db, None, "auth.login_failed")
        db.commit()
        raise HTTPException(401, "Invalid username or password.")
    if hasher.check_needs_rehash(user.password_hash):
        user.password_hash = hash_password(body.password)
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    db.add(AuthSession(token_hash=digest(token), user_id=user.id, csrf_token=csrf,
                       expires_at=time.time() + settings().session_hours * 3600))
    audit(db, user.id, "auth.login")
    db.commit()
    response.set_cookie("adpe_session", token, httponly=True, secure=settings().secure_cookies,
                        samesite="strict", max_age=settings().session_hours * 3600, path="/")
    return {"username": user.username, "is_admin": user.is_admin, "csrf_token": csrf}


@app.get("/api/auth/me")
def me(auth: AUTH):
    return {"username": auth[0].username, "is_admin": auth[0].is_admin, "csrf_token": auth[1].csrf_token}


@app.post("/api/auth/logout", status_code=204)
def logout(response: Response, db: DB, auth: AUTH):
    db.delete(auth[1])
    audit(db, auth[0].id, "auth.logout")
    db.commit()
    response.delete_cookie("adpe_session", path="/", secure=settings().secure_cookies, httponly=True, samesite="strict")


@app.get("/api/config")
def config(auth: AUTH):
    cfg = settings()
    return {"max_upload_bytes": cfg.max_upload_bytes, "max_rows": cfg.max_rows, "max_columns": cfg.max_columns,
            "max_user_storage_bytes": cfg.max_user_storage_bytes, "ollama_enabled": cfg.ollama_enabled,
            "version": __version__}


@app.post("/api/datasets", status_code=201)
async def upload(request: Request, db: DB, auth: AUTH, filename: str = Query(max_length=200)):
    filename_format(filename)
    await run_in_threadpool(rate_limit, db, "upload:" + auth[0].id, 20, 300)
    cfg = settings()
    temp = cfg.data_dir / "work" / (new_id() + ".upload")
    checksum, size = hashlib.sha256(), 0
    try:
        with temp.open("xb") as file:
            temp.chmod(0o600)
            async for chunk in request.stream():
                size += len(chunk)
                if size > cfg.max_upload_bytes:
                    raise HTTPException(413, "File exceeds the upload size limit.")
                checksum.update(chunk)
                await run_in_threadpool(file.write, chunk)
            await run_in_threadpool(file.flush)
            await run_in_threadpool(__import__("os").fsync, file.fileno())
        dataset = await run_in_threadpool(store_dataset, db, auth[0].id, filename, temp, checksum.hexdigest(), size)
        return dataset_view(dataset)
    finally:
        temp.unlink(missing_ok=True)


@app.get("/api/datasets")
def datasets(db: DB, auth: AUTH, limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0)):
    items = db.scalars(select(Dataset).where(Dataset.owner_id == auth[0].id).order_by(Dataset.created_at.desc())
                       .limit(limit).offset(offset))
    total = db.scalar(select(func.count()).select_from(Dataset).where(Dataset.owner_id == auth[0].id))
    return {"items": [dataset_view(i) for i in items], "total": total}


@app.delete("/api/datasets/{dataset_id}", status_code=204)
def delete_dataset(dataset_id: str, db: DB, auth: AUTH):
    db.scalar(select(User).where(User.id == auth[0].id).with_for_update())
    item = db.scalar(select(Dataset).where(Dataset.id == dataset_id, Dataset.owner_id == auth[0].id).with_for_update())
    if not item:
        raise HTTPException(404, "Dataset not found.")
    active = db.scalar(select(Job.id).where(Job.dataset_id == item.id, Job.status.in_(["queued", "running"])).limit(1))
    if active:
        raise HTTPException(409, "Cancel active jobs before deleting this dataset.")
    db.execute(delete(Job).where(Job.dataset_id == item.id))
    db.delete(item)
    audit(db, auth[0].id, "dataset.delete", item.id)
    db.commit()
    (settings().data_dir / "uploads" / item.storage_name).unlink(missing_ok=True)


@app.post("/api/jobs", status_code=202)
def create_job(body: NewJob, db: DB, auth: AUTH,
               idempotency_key: Annotated[str | None, Header(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")] = None):
    rate_limit(db, "jobs:" + auth[0].id, 30, 60)
    if not db.scalar(select(Dataset.id).where(Dataset.id == body.dataset_id, Dataset.owner_id == auth[0].id)):
        raise HTTPException(404, "Dataset not found.")
    return job_view(enqueue(db, auth[0].id, body.dataset_id, body.objective,
                            {k: v.model_dump(exclude_none=True) for k, v in body.thresholds.items()}, idempotency_key))


@app.get("/api/jobs")
def jobs(db: DB, auth: AUTH, limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0)):
    items = db.scalars(select(Job).where(Job.owner_id == auth[0].id).order_by(Job.created_at.desc()).limit(limit).offset(offset))
    total = db.scalar(select(func.count()).select_from(Job).where(Job.owner_id == auth[0].id))
    return {"items": [job_view(i) for i in items], "total": total}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str, db: DB, auth: AUTH):
    return job_view(owned_job(db, job_id, auth[0]), True)


@app.post("/api/jobs/{job_id}/cancel")
def cancel(job_id: str, db: DB, auth: AUTH):
    owned_job(db, job_id, auth[0])
    count = db.execute(update(Job).where(Job.id == job_id, Job.status.in_(["queued", "running"]))
                       .values(status="cancelled", lease_token=None, lease_until=None, finished_at=time.time())).rowcount
    if not count:
        raise HTTPException(409, "Only queued or running jobs can be cancelled.")
    audit(db, auth[0].id, "job.cancel", job_id)
    db.commit()
    return {"status": "cancelled"}


@app.post("/api/jobs/{job_id}/retry", status_code=202)
def retry(job_id: str, db: DB, auth: AUTH,
          idempotency_key: Annotated[str | None, Header(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")] = None):
    rate_limit(db, "jobs:" + auth[0].id, 30, 60)
    old = owned_job(db, job_id, auth[0])
    if old.status not in {"failed", "cancelled"}:
        raise HTTPException(409, "Only failed or cancelled jobs can be retried.")
    # A new job preserves the original execution history.
    return job_view(enqueue(db, auth[0].id, old.dataset_id, old.objective, old.thresholds, idempotency_key))


@app.get("/api/jobs/{job_id}/report")
def report(job_id: str, db: DB, auth: AUTH, format: str = Query("json", pattern="^(json|md|pdf)$")):
    item = owned_job(db, job_id, auth[0])
    if item.status != "completed" or not item.result:
        raise HTTPException(409, "Report is not ready.")
    rate_limit(db, "reports:" + auth[0].id, 30, 60)
    audit(db, auth[0].id, "report.download", item.id)
    db.commit()
    headers = {"Content-Disposition": f'attachment; filename="analysis-{item.id}.{format}"'}
    if format == "json":
        return JSONResponse(item.result, headers=headers)
    if format == "md":
        return Response(reports.markdown(item.result), media_type="text/markdown", headers=headers)
    return Response(reports.pdf(item.result), media_type="application/pdf", headers=headers)


@app.get("/api/admin/users")
def list_users(db: DB, auth: AUTH, limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0)):
    admin_only(auth)
    return {"items": [{"id": u.id, "username": u.username, "is_admin": u.is_admin, "active": u.active}
                      for u in db.scalars(select(User).order_by(User.created_at).limit(limit).offset(offset))]}


@app.post("/api/admin/users", status_code=201)
def add_user(body: NewUser, db: DB, auth: AUTH):
    admin = admin_only(auth)
    try:
        user = create_user(db, body.username.lower(), body.password, body.is_admin)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    audit(db, admin.id, "user.create", user.id)
    db.commit()
    return {"id": user.id, "username": user.username}


@app.post("/api/admin/users/{user_id}/disable", status_code=204)
def disable_user(user_id: str, db: DB, auth: AUTH):
    admin = admin_only(auth)
    if user_id == admin.id:
        raise HTTPException(409, "You cannot disable your own account.")
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(404, "User not found.")
    user.active = False
    db.execute(delete(AuthSession).where(AuthSession.user_id == user_id))
    audit(db, admin.id, "user.disable", user_id)
    db.commit()


@app.get("/api/admin/audit")
def audit_events(db: DB, auth: AUTH, limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0)):
    admin_only(auth)
    return {"items": [{"id": e.id, "actor_id": e.actor_id, "action": e.action, "resource_id": e.resource_id,
                        "created_at": e.created_at}
                      for e in db.scalars(select(Audit).order_by(Audit.created_at.desc()).limit(limit).offset(offset))]}


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/docs", include_in_schema=False)
def api_reference():
    if settings().environment == "production":
        raise HTTPException(404, "Not found.")
    return FileResponse(STATIC / "docs.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
