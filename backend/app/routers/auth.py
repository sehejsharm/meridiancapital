"""Sign-in, sessions and the devices allowed to hold them.

Getting in takes two things: the operator's PIN (or a passkey) and a device an
operator has approved (app/devices.py). The dashboard's relay sends the device's
secret in ``X-Meridian-Device``; it lives in an httpOnly cookie the page's script
cannot read. A right PIN on a device that is not approved gets a code to show and
an approval request, not a session.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from typing import Any
from urllib.parse import unquote

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app import devices, passkeys
from app.config import settings
from app.deps import ctx
from app.security import (
    MAX_SESSION_SEC,
    Principal,
    client_ip,
    issue_token,
    login_throttle,
    require_auth,
    verify_password,
    ws_ticket_claims,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])
log = logging.getLogger("meridian.auth")

DEVICE_HEADER = "x-meridian-device"
LABEL_HEADER = "x-meridian-device-label"


class LoginRequest(BaseModel):
    password: str = Field(min_length=1, max_length=512)


class TokenResponse(BaseModel):
    token: str
    expires_in: int
    operator: str


def _device_secret(request: Request) -> str:
    return request.headers.get(DEVICE_HEADER, "").strip()


def _device_label(request: Request) -> str:
    # Percent-encoded by the relay: an HTTP header carries no "·" or "é" safely.
    return devices.clean_label(unquote(request.headers.get(LABEL_HEADER, "")[:300]))


def _session(device_id: str, auth_time: int | None = None, subject: str | None = None) -> TokenResponse:
    ttl = settings.token_ttl_min * 60
    who = subject or settings.operator
    return TokenResponse(token=issue_token(who, ttl, auth_time=auth_time, device=device_id),
                         expires_in=ttl, operator=who)


def _alert(title: str, text: str, level: str = "warn") -> None:
    """Telegram, webhook and phone push, off the request path. Never raises."""
    from app import notify

    def run() -> None:
        try:
            notify.send(title, text, kind="security", level=level)
        except Exception:  # an alert must never break sign-in
            log.exception("security alert failed")

    threading.Thread(target=run, name="security-alert", daemon=True).start()


def _out_of_date() -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                         detail="this sign-in page is out of date — reload it and try again")


def _pending_response(device: dict, code: str) -> JSONResponse:
    return JSONResponse(status_code=status.HTTP_403_FORBIDDEN, content={
        "detail": "this device needs an operator's approval",
        "pending": True,
        "code": code,
        "device": device.get("id"),
        "expires_in": devices.CODE_TTL_MS // 1000,
    }, headers={"Cache-Control": "no-store"})


def _ask_for_approval(request: Request, secret: str, how: str) -> JSONResponse:
    """The right PIN on a device nobody approved: a code for its screen, and a
    message to the operators."""
    db = ctx().db
    ip = client_ip(request)
    label = _device_label(request)
    try:
        device, code = devices.request_approval(db, secret, label, ip)
    except devices.DeviceError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    db.audit(settings.operator, "device.pending", f"{device['label']} ({how}) — waiting for approval", ip)
    db.add_event("warn", f"a new device asks to sign in: {device['label']} from {ip} — it entered the right {how}; "
                         f"approve it in Controls with the code shown on it", source="api")
    if devices.alert_gate.allow(device["id"]):
        _alert("Device asks to sign in",
               f"{device['label']} from {ip} entered the right {how}. To let it in, open Controls on a signed-in "
               f"device and type the code shown on it. If this was not you, change the PIN now.")
    return _pending_response(device, code)


# ── sign-in ──────────────────────────────────────────────────────────────────
@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, request: Request):
    db = ctx().db
    ip = client_ip(request)
    secret = _device_secret(request)
    if not devices.valid_secret(secret):
        raise _out_of_date()
    device = devices.find(db, secret)
    trusted = device is not None and device["status"] == devices.APPROVED
    # An approved device has a lockout of its own, so a flood of guesses from
    # elsewhere never locks the operator out. Anything else is limited per
    # address, and all of it together by the budget for unknown devices.
    key = f"device:{device['id']}" if trusted else f"ip:{ip}"
    login_throttle.check(key)
    if not trusted:
        paused = devices.unknown_budget.paused_for()
        if paused:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"sign-in from new devices is paused after too many wrong PINs; retry in {paused // 60 + 1} min",
            )
    if not await asyncio.to_thread(verify_password, body.password, settings.password_hash):
        login_throttle.fail(key)
        db.audit(settings.operator, "login.failed", "bad PIN" + ("" if trusted else " from a device not approved"), ip)
        if not trusted and devices.unknown_budget.fail():
            db.add_event("critical", "too many wrong PINs from devices that are not approved: PIN sign-in from new "
                                     "devices is paused for an hour (approved devices are not affected)", source="api")
            _alert("Sign-in paused", "Too many wrong PINs from devices that are not approved. PIN sign-in from new "
                                     "devices is paused for an hour; approved devices still get in.", "critical")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid password")
    login_throttle.succeed(key)
    if not trusted:
        return _ask_for_approval(request, secret, "PIN")
    devices.touch(db, device["id"], ip)
    db.audit(settings.operator, "login.success", device["label"], ip)
    return _session(device["id"])


@router.post("/device/claim", response_model=TokenResponse)
async def claim_device_session(request: Request):
    """The waiting device asks whether it was approved; if so, it gets its session
    once, without typing the PIN again (it typed it minutes ago)."""
    db = ctx().db
    ip = client_ip(request)
    secret = _device_secret(request)
    if not devices.valid_secret(secret):
        raise _out_of_date()
    login_throttle.check(f"ip:{ip}")
    state, device = devices.claim(db, secret)
    if state == "pending":
        return JSONResponse(status_code=status.HTTP_202_ACCEPTED, content={
            "pending": True, "detail": "waiting for an operator's approval",
            "expires_in": max(0, (int(device["code_expires_ms"]) - devices.now_ms()) // 1000),
        }, headers={"Cache-Control": "no-store"})
    if state != "approved" or device is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="this device was not approved, or the request ran out — enter the PIN again")
    devices.touch(db, device["id"], ip)
    db.audit(settings.operator, "login.success", f"{device['label']} (just approved)", ip)
    return _session(device["id"])


@router.post("/renew", response_model=TokenResponse)
async def renew(request: Request, principal: Principal = Depends(require_auth)) -> TokenResponse:
    """A fresh token for a session that is still valid, so an open dashboard is not
    signed out mid-session. It keeps the original sign-in time, and stops renewing
    a week after the PIN or passkey was last used.

    A session from before device approval was required carries its device over:
    it was signed in with the PIN already, and renewing approves the device it is
    on, so nobody already at the desk is turned out by the change."""
    at = principal.auth_time or int(time.time())
    if time.time() - at > MAX_SESSION_SEC:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="this session is a week old — sign in again")
    db = ctx().db
    device_id = principal.device
    if device_id is None:
        secret = _device_secret(request)
        if not devices.valid_secret(secret):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="sign in again")
        ip = client_ip(request)
        device = devices.approve_secret(db, secret, _device_label(request), ip, by=principal.subject,
                                        via="already signed in when device approval was switched on")
        device_id = device["id"]
        db.audit(principal.subject, "device.approved", f"{device['label']} (carried over from an earlier session)", ip)
        db.add_event("info", f"device approved: {device['label']} — it was already signed in when device approval "
                             f"was switched on", source="api")
    else:
        devices.touch(db, device_id, client_ip(request))
    return _session(device_id, auth_time=at, subject=principal.subject)


@router.get("/me")
async def me(principal: Principal = Depends(require_auth)) -> dict:
    return {"operator": principal.subject, "scope": principal.scope, "device": principal.device}


@router.post("/ws-ticket")
async def ws_ticket(principal: Principal = Depends(require_auth)) -> dict:
    """Short-lived, single-purpose credential for the WebSocket handshake. It names
    the session it came from, so the socket closes when that session ends."""
    return {
        "ticket": issue_token(principal.subject, settings.ws_ticket_ttl_sec, scope="ws",
                              device=principal.device, extra=ws_ticket_claims(principal)),
        "expires_in": settings.ws_ticket_ttl_sec,
    }


@router.post("/logout")
async def logout(request: Request, principal: Principal = Depends(require_auth)) -> dict:
    """Sign this device out: its session tokens stop working here, not just in the browser."""
    db = ctx().db
    if principal.device and principal.device != devices.CONSOLE_DEVICE:
        devices.end_sessions(db, principal.device)
    db.audit(principal.subject, "logout", "", client_ip(request))
    return {"ok": True}


@router.post("/sessions/revoke-all")
async def sign_out_everywhere(request: Request, principal: Principal = Depends(require_auth)) -> dict:
    """End every session on every device, this one included. Devices stay approved:
    signing in again takes the PIN, not a new approval."""
    db = ctx().db
    devices.end_all_sessions(db)
    ip = client_ip(request)
    db.audit(principal.subject, "sessions.revoke_all", "", ip)
    db.add_event("warn", f"every session was signed out by {principal.subject}", source="api")
    return {"ok": True}


# ── the devices themselves ───────────────────────────────────────────────────
class ApproveRequest(BaseModel):
    code: str = Field(min_length=4, max_length=20)


def _actor_label(principal: Principal) -> str:
    if not principal.device:
        return "a session from before device approval"
    row = devices.get(ctx().db, principal.device)
    return row["label"] if row else principal.device


@router.get("/devices")
async def list_devices(principal: Principal = Depends(require_auth)) -> dict:
    return devices.listing(ctx().db, current=principal.device)


@router.post("/devices/{device_id}/approve")
async def approve_device(device_id: str, body: ApproveRequest, request: Request,
                         principal: Principal = Depends(require_auth)) -> dict:
    db = ctx().db
    ip = client_ip(request)
    via = f"approved on {_actor_label(principal)}"
    try:
        device = devices.approve(db, device_id, body.code, by=principal.subject, via=via)
    except devices.DeviceError as exc:
        db.audit(principal.subject, "device.approve_failed", f"{device_id}: {exc}", ip)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    db.audit(principal.subject, "device.approved", f"{device['label']} ({via})", ip)
    db.add_event("warn", f"device approved for sign-in: {device['label']} — by {principal.subject} ({via})", source="api")
    _alert("Device approved", f"{device['label']} can now sign in ({via}). If this was not you, revoke it in "
                              f"Controls and change the PIN.", "info")
    return devices.listing(db, current=principal.device)


@router.post("/devices/{device_id}/deny")
async def deny_device(device_id: str, request: Request, principal: Principal = Depends(require_auth)) -> dict:
    db = ctx().db
    try:
        device = devices.deny(db, device_id)
    except devices.DeviceError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    db.audit(principal.subject, "device.denied", device["label"], client_ip(request))
    db.add_event("info", f"device refused: {device['label']} — by {principal.subject}", source="api")
    return devices.listing(db, current=principal.device)


@router.delete("/devices/{device_id}")
async def revoke_device(device_id: str, request: Request, principal: Principal = Depends(require_auth)) -> dict:
    db = ctx().db
    try:
        device = devices.revoke(db, device_id)
    except devices.DeviceError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    db.audit(principal.subject, "device.revoked", device["label"], client_ip(request))
    db.add_event("warn", f"device removed: {device['label']} — its sessions ended, by {principal.subject}",
                 source="api")
    return {**devices.listing(db, current=principal.device), "signed_out": device_id == principal.device}


# ── passkeys: Face ID / Touch ID ─────────────────────────────────────────────
class RegisterRequest(BaseModel):
    handle: str = Field(max_length=200)
    credential: dict[str, Any]
    label: str = Field(default="", max_length=60)


class AuthenticateRequest(BaseModel):
    handle: str = Field(max_length=200)
    credential: dict[str, Any]


@router.get("/passkeys/available")
async def passkeys_available() -> dict:
    """Whether the sign-in screen should offer Face ID at all.

    Unauthenticated on purpose: the login page has to ask before anyone is
    signed in. It says only whether any passkey is enrolled, never how many or which.
    """
    configured = bool(settings.rp_id and settings.rp_origin)
    try:
        enrolled = len(ctx().db.passkeys()) > 0
    except Exception:
        enrolled = False
    return {"configured": configured, "available": configured and enrolled}


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
    login_throttle.check(f"ip:{ip}")
    try:
        handle, options = passkeys.authentication_options(settings, ctx().db)
    except passkeys.PasskeyError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"handle": handle, "options": json.loads(options)}


@router.post("/passkeys/authenticate", response_model=TokenResponse)
async def passkey_authenticate(body: AuthenticateRequest, request: Request):
    """A passkey signs in on its own: it was enrolled from a signed-in session and
    proves the device holds it, so the device it is used on is approved by it."""
    db = ctx().db
    ip = client_ip(request)
    secret = _device_secret(request)
    if not devices.valid_secret(secret):
        raise _out_of_date()
    login_throttle.check(f"ip:{ip}")
    try:
        result = passkeys.verify_authentication(settings, db, body.credential, body.handle)
    except passkeys.PasskeyError as exc:
        # A failed passkey attempt counts against the same limiter as the PIN,
        # so this route cannot be used to sidestep the lockout.
        login_throttle.fail(f"ip:{ip}")
        db.audit(settings.operator, "login.failed", f"passkey: {exc}", ip)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc))

    login_throttle.succeed(f"ip:{ip}")
    device = devices.find(db, secret)
    if device is None or device["status"] != devices.APPROVED:
        device = devices.approve_secret(db, secret, _device_label(request), ip, by=settings.operator,
                                        via=f"passkey: {result['label']}")
        db.audit(settings.operator, "device.approved", f"{device['label']} (passkey {result['label']})", ip)
        db.add_event("warn", f"device approved by its passkey: {device['label']} ({result['label']})", source="api")
    devices.touch(db, device["id"], ip)
    db.audit(settings.operator, "login.success", f"passkey: {result['label']} on {device['label']}", ip)
    return _session(device["id"])


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
