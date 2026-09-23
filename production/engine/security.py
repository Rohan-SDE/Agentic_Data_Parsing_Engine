import hashlib
import secrets
import threading
import time

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import HTTPException, Request
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from engine.db import AuthSession, RateBucket, User

hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2)
DUMMY_HASH = hasher.hash(secrets.token_urlsafe(32))
PASSWORD_SLOTS = threading.BoundedSemaphore(2)


def hash_password(password):
    if not PASSWORD_SLOTS.acquire(blocking=False):
        raise HTTPException(429, "Password service is busy. Try again.", headers={"Retry-After": "2"})
    try:
        return hasher.hash(password)
    finally:
        PASSWORD_SLOTS.release()


def digest(value: str):
    return hashlib.sha256(value.encode()).hexdigest()


def password_ok(encoded, password):
    if not PASSWORD_SLOTS.acquire(blocking=False):
        raise HTTPException(429, "Password service is busy. Try again.", headers={"Retry-After": "2"})
    try:
        return hasher.verify(encoded, password)
    except (VerificationError, InvalidHashError):
        return False
    finally:
        PASSWORD_SLOTS.release()


def rate_limit(db, key, limit, window=60):
    now = time.time()
    bucket_key = digest(f"{key}:{int(now // window)}")
    # Atomic UPDATE handles concurrent requests; the savepoint handles first-insert races.
    count = db.execute(update(RateBucket).where(RateBucket.key == bucket_key)
                       .values(count=RateBucket.count + 1).returning(RateBucket.count)).scalar_one_or_none()
    if count is None:
        try:
            with db.begin_nested():
                db.add(RateBucket(key=bucket_key, count=1, expires_at=now + window * 2))
                db.flush()
            count = 1
        except IntegrityError:
            count = db.execute(update(RateBucket).where(RateBucket.key == bucket_key)
                               .values(count=RateBucket.count + 1).returning(RateBucket.count)).scalar_one()
    db.commit()
    if count > limit:
        raise HTTPException(429, "Too many requests. Try again later.", headers={"Retry-After": str(window)})


def authenticate(request: Request, db):
    token = request.cookies.get("adpe_session", "")
    session = db.get(AuthSession, digest(token)) if token else None
    if not session or session.expires_at < time.time():
        raise HTTPException(401, "Sign in to continue.")
    user = db.get(User, session.user_id)
    if not user or not user.active:
        raise HTTPException(401, "Account is disabled.")
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        supplied = request.headers.get("X-CSRF-Token", "")
        if not secrets.compare_digest(supplied, session.csrf_token):
            raise HTTPException(403, "Invalid CSRF token.")
    return user, session


def create_user(db, username, password, admin=False):
    import re
    if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{2,63}", username):
        raise ValueError("Username must contain 3–64 lowercase letters, digits, dots, dashes or underscores.")
    if not 12 <= len(password) <= 128:
        raise ValueError("Password must contain 12–128 characters.")
    if db.scalar(select(User).where(User.username == username)):
        raise ValueError("Username already exists.")
    user = User(username=username, password_hash=hash_password(password), is_admin=admin)
    db.add(user)
    db.flush()
    return user
