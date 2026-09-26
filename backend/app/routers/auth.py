from __future__ import annotations

import asyncio

import json

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from app import passkeys
from app.config import settings
from app.deps import ctx
from app.security import (
    Principal,
    client_ip,
    issue_token,
    login_throttle,
    require_auth,
    verify_password,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    password: str = Field(min_length=1, max_length=512)


class TokenResponse(BaseModel):
    token: str
    expires_in: int
    operator: str


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, request: Request) -> TokenResponse:
    ip = client_ip(request)
    login_throttle.check(ip)
    if not await asyncio.to_thread(verify_password, body.password, settings.password_hash):
        login_throttle.fail(ip)
        ctx().db.audit(settings.operator, "login.failed", "bad password", ip)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid password")
    login_throttle.succeed(ip)
    ttl = settings.token_ttl_min * 60
    ctx().db.audit(settings.operator, "login.success", "", ip)
    return TokenResponse(
        token=issue_token(settings.operator, ttl), expires_in=ttl, operator=settings.operator
    )


@router.get("/me")
async def me(principal: Principal = Depends(require_auth)) -> dict:
    return {"operator": principal.subject, "scope": principal.scope}


@router.post("/ws-ticket")
async def ws_ticket(principal: Principal = Depends(require_auth)) -> dict:
    """Short-lived, single-purpose credential for the WebSocket handshake."""
    return {
        "ticket": issue_token(principal.subject, settings.ws_ticket_ttl_sec, scope="ws"),
        "expires_in": settings.ws_ticket_ttl_sec,
    }


# ── passkeys: Face ID / Touch ID ─────────────────────────────────────────────
class RegisterRequest(BaseModel):
    handle: str
    credential: dict[str, Any]
    label: str = Field(default="", max_length=60)


class AuthenticateRequest(BaseModel):
    handle: str
    credential: dict[str, Any]


@router.get("/passkeys/available")
async def passkeys_available() -> dict:
    """Whether the sign-in screen should offer Face ID at all.

    Unauthenticated on purpose: the login page has to ask before anyone is
    signed in. It reveals only whether a device is enrolled and never which.
    """
    configured = bool(settings.rp_id and settings.rp_origin)
    try:
        enrolled = len(ctx().db.passkeys())
    except Exception:
        enrolled = 0
    return {"configured": configured, "enrolled": enrolled, "available": configured and enrolled > 0}


@router.post("/passkeys/register/options")
async def passkey_register_options(principal: Principal = Depends(require_auth)) -> dict:
    """Enrolling a device is gated behind an existing session — a new passkey
    is a second key to the account, not a way in for a stranger."""
    try:
        handle, options = passkeys.registration_options(settings, ctx().db)
    except passkeys.PasskeyError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"handle": handle, "options": json.loads(options)}


@router.post("/passkeys/register")
async def passkey_register(
    body: RegisterRequest, request: Request, principal: Principal = Depends(require_auth)
) -> dict:
    try:
        result = passkeys.verify_registration(
            settings, ctx().db, body.credential, body.handle, body.label
        )
    except passkeys.PasskeyError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    ip = client_ip(request)
    ctx().db.audit(principal.subject, "passkey.register", result["label"], ip)
    ctx().db.add_event("warn", f"a device was enrolled for Face ID sign-in by {principal.subject}", source="api")
    return {"ok": True, **result}


@router.post("/passkeys/authenticate/options")
async def passkey_auth_options(request: Request) -> dict:
    ip = client_ip(request)
    login_throttle.check(ip)
    try:
        handle, options = passkeys.authentication_options(settings, ctx().db)
    except passkeys.PasskeyError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"handle": handle, "options": json.loads(options)}


@router.post("/passkeys/authenticate", response_model=TokenResponse)
async def passkey_authenticate(body: AuthenticateRequest, request: Request) -> TokenResponse:
    ip = client_ip(request)
    login_throttle.check(ip)
    try:
        result = passkeys.verify_authentication(settings, ctx().db, body.credential, body.handle)
    except passkeys.PasskeyError as exc:
        # A failed passkey attempt counts against the same limiter as the PIN,
        # so this route cannot be used to sidestep the lockout.
        login_throttle.fail(ip)
        ctx().db.audit(settings.operator, "login.failed", f"passkey: {exc}", ip)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc))

    login_throttle.succeed(ip)
    ttl = settings.token_ttl_min * 60
    ctx().db.audit(settings.operator, "login.success", f"passkey: {result['label']}", ip)
    return TokenResponse(
        token=issue_token(settings.operator, ttl), expires_in=ttl, operator=settings.operator
    )


@router.get("/passkeys")
async def list_passkeys(principal: Principal = Depends(require_auth)) -> dict:
    return {"passkeys": ctx().db.passkeys()}


@router.delete("/passkeys/{credential_id}")
async def delete_passkey(
    credential_id: str, request: Request, principal: Principal = Depends(require_auth)
) -> dict:
    ctx().db.delete_passkey(credential_id)
    ctx().db.audit(principal.subject, "passkey.delete", credential_id[:16], client_ip(request))
    return {"ok": True, "passkeys": ctx().db.passkeys()}
