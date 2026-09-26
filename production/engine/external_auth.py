"""Server-side Supabase Auth bridge. Provider tokens never reach browser storage."""
import base64
import hashlib
import re
import secrets
import time
import uuid
from typing import Annotated, Literal
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from engine.config import settings
from engine.db import AuthSession, User, audit, get_db
from engine.security import create_user, digest, rate_limit

router = APIRouter(prefix="/api/auth/external")
DB = Annotated[Session, Depends(get_db)]
COOKIE = "adpe_oauth_verifier"
COOKIE_PATH = "/api/auth/external/google"


class Address(BaseModel):
    model_config = ConfigDict(extra="forbid")
    channel: Literal["email", "phone"]
    address: str = Field(min_length=3, max_length=254)

    @model_validator(mode="after")
    def validate_address(self):
        self.address = self.address.strip()
        if self.channel == "email":
            self.address = self.address.lower()
            if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", self.address):
                raise ValueError("Enter a valid email address")
        elif not re.fullmatch(r"\+[1-9][0-9]{7,14}", self.address):
            raise ValueError("Use an international number, for example +919876543210")
        return self


class Verification(Address):
    code: str = Field(pattern=r"^[0-9]{6,10}$")


def enabled(channel):
    if not getattr(settings(), channel + "_auth_enabled"):
        raise HTTPException(403, "This sign-in method is not enabled.")


def throttle(db, request, action, limit=10):
    ip = request.client.host if request.client else "unknown"
    rate_limit(db, f"external:{action}:" + ip, limit, 3600)


def provider(path, payload=None, token=None):
    cfg = settings()
    headers = {"apikey": cfg.supabase_publishable_key}
    if token:
        headers["Authorization"] = "Bearer " + token
    try:
        with httpx.Client(timeout=10, follow_redirects=False) as client:
            response = client.request("GET" if payload is None else "POST",
                cfg.supabase_url.rstrip("/") + "/auth/v1/" + path, headers=headers, json=payload)
        if response.status_code == 429:
            raise HTTPException(429, "Too many attempts. Try again later.", headers={"Retry-After": "60"})
        if response.status_code >= 500:
            raise HTTPException(503, "Sign-in service temporarily unavailable.")
        if response.status_code >= 300:
            raise HTTPException(400, "Unable to verify or send the code. Check your details and try again.")
        return response.json()
    except (httpx.HTTPError, ValueError):
        raise HTTPException(503, "Sign-in service temporarily unavailable.") from None


def finish(db, response, result, method):
    token = result.get("access_token")
    if not isinstance(token, str) or not token:
        raise HTTPException(401, "Verification failed.")
    identity = provider("user", token=token)
    try:
        subject = str(uuid.UUID(identity["id"]))
    except (KeyError, ValueError, TypeError):
        raise HTTPException(401, "Verification failed.") from None
    verified = identity.get("phone_confirmed_at") if method == "phone" else identity.get("email_confirmed_at")
    if not verified or identity.get("is_anonymous"):
        raise HTTPException(401, "A verified account is required.")
    if method == "google" and not any(i.get("provider") == "google" for i in identity.get("identities", [])):
        raise HTTPException(401, "Google verification failed.")
    key = digest(settings().supabase_url.rstrip("/") + ":" + subject)
    user = db.scalar(select(User).where(User.external_subject == key))
    if user is None:
        if not settings().public_registration:
            raise HTTPException(403, "New account registration is disabled.")
        rate_limit(db, "external-new-global", 50, 3600)
        try:
            user = create_user(db, "user_" + uuid.uuid4().hex, secrets.token_urlsafe(48), admin=False)
            user.external_subject = key
            audit(db, user.id, "auth.register_external")
            db.commit()
        except IntegrityError:
            db.rollback()
            user = db.scalar(select(User).where(User.external_subject == key))
            if user is None:
                raise HTTPException(409, "Please try signing in again.") from None
    if not user.active:
        raise HTTPException(403, "This account is disabled.")
    session, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    db.add(AuthSession(token_hash=digest(session), user_id=user.id, csrf_token=csrf,
                       expires_at=time.time() + settings().session_hours * 3600))
    audit(db, user.id, "auth.login_" + method)
    db.commit()
    response.set_cookie("adpe_session", session, httponly=True, secure=settings().secure_cookies,
                        samesite="strict", max_age=settings().session_hours * 3600, path="/")
    return {"username": user.username, "is_admin": user.is_admin, "csrf_token": csrf}


@router.get("/options")
def options():
    return {name: getattr(settings(), name + "_auth_enabled") for name in ("email", "phone", "google")}


@router.post("/send-code")
def send_code(body: Address, request: Request, db: DB):
    enabled(body.channel)
    throttle(db, request, "send-" + body.channel, 3)
    rate_limit(db, "external-send:" + digest(body.channel + body.address), 3, 3600)
    rate_limit(db, "external-send-global:" + body.channel, 20 if body.channel == "phone" else 100, 3600)
    payload = {body.channel: body.address, "create_user": settings().public_registration}
    if body.channel == "phone":
        payload["channel"] = "sms"
    try:
        provider("otp", payload)
    except HTTPException as exc:
        if exc.status_code != 400:
            raise
        # Do not disclose whether an address is already registered.
    return {"message": "If eligible, a verification code has been sent. Check your inbox or messages."}


@router.post("/verify-code")
def verify_code(body: Verification, request: Request, response: Response, db: DB):
    enabled(body.channel)
    throttle(db, request, "verify", 15)
    rate_limit(db, "external-verify:" + digest(body.channel + body.address), 15, 3600)
    result = provider("verify", {body.channel: body.address, "token": body.code,
                                 "type": "sms" if body.channel == "phone" else "email"})
    return finish(db, response, result, body.channel)


@router.post("/google/start")
def google_start(request: Request, response: Response, db: DB):
    enabled("google")
    throttle(db, request, "google", 15)
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    response.set_cookie(COOKIE, verifier, httponly=True, secure=settings().secure_cookies,
                        samesite="lax", max_age=600, path=COOKIE_PATH)
    query = urlencode({"provider": "google", "redirect_to": settings().public_origin.rstrip("/") +
                       COOKIE_PATH + "/callback", "code_challenge": challenge, "code_challenge_method": "s256"})
    return {"url": settings().supabase_url.rstrip("/") + "/auth/v1/authorize?" + query}


@router.get("/google/callback")
def google_callback(request: Request, db: DB, code: str = ""):
    response = RedirectResponse("/", status_code=303)
    response.delete_cookie(COOKIE, path=COOKIE_PATH, secure=settings().secure_cookies, httponly=True, samesite="lax")
    try:
        enabled("google")
        throttle(db, request, "callback", 20)
        verifier = request.cookies.get(COOKIE, "")
        if not re.fullmatch(r"[A-Za-z0-9_-]{64}", verifier) or not code or len(code) > 2048:
            raise HTTPException(400, "Invalid OAuth callback.")
        result = provider("token?grant_type=pkce", {"auth_code": code, "code_verifier": verifier})
        finish(db, response, result, "google")
    except HTTPException:
        response.headers["location"] = "/?auth_error=1"
    return response
