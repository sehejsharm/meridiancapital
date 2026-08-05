"""Authentication.

Two ways in, both landing on the same bearer token:

  * **Login** — username and password, checked against a scrypt hash, which
    mints a signed, expiring session token. This is what the app and the
    dashboard use.
  * **Static API token** — the `API_TOKEN` from `.env`, for curl, exports and
    anything scripted.

Biometrics are deliberately *not* a server concept. Touch ID and Face ID
unlock the session token already stored on the device; the password is what
the server actually verifies. Saying otherwise would overstate what a
fingerprint on your own laptop proves to a machine in Mumbai.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Optional

from fastapi import Header, HTTPException, Query, WebSocket, status

from . import db
from .config import settings

SESSION_TTL_SECONDS = 30 * 24 * 3600     # a month; biometrics gate the device
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2 ** 14, 8, 1


# ---------------------------------------------------------------- passwords


def hash_password(password: str, salt: Optional[bytes] = None) -> str:
    """scrypt hash, stored as `scrypt$<salt_b64>$<hash_b64>`."""
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=32,
    )
    return "scrypt${}${}".format(
        base64.b64encode(salt).decode(), base64.b64encode(digest).decode()
    )


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, salt_b64, hash_b64 = stored.split("$", 2)
        if scheme != "scrypt":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
    except (ValueError, TypeError):
        return False
    candidate = hashlib.scrypt(
        password.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=32,
    )
    return hmac.compare_digest(candidate, expected)


def admin_username() -> str:
    return (os.getenv("ADMIN_USER") or "Sehej").strip()


def login_configured() -> bool:
    """True once at least one account can sign in."""
    from . import users
    try:
        return users.count() > 0
    except Exception:
        return bool((os.getenv("ADMIN_PASSWORD") or "").strip())


# ---------------------------------------------------------------- sessions


def _signing_key() -> bytes:
    """Stable per-install secret, so tokens survive a restart."""
    stored = db.kv_get("session_signing_key")
    if not stored:
        stored = secrets.token_urlsafe(48)
        db.kv_set("session_signing_key", stored)
    return stored.encode()


def _epoch() -> int:
    """Bumped by "sign out everywhere" to invalidate every issued token."""
    return int(db.kv_get("session_epoch", 0) or 0)


def bump_epoch() -> int:
    nxt = _epoch() + 1
    db.kv_set("session_epoch", nxt)
    return nxt


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def issue_session(username: str, role: str = "viewer",
                  ttl: int = SESSION_TTL_SECONDS) -> dict:
    now = int(time.time())
    payload = {"sub": username, "rol": role, "iat": now,
               "exp": now + ttl, "epc": _epoch()}
    body = _b64(json.dumps(payload, separators=(",", ":")).encode())
    sig = _b64(hmac.new(_signing_key(), body.encode(), hashlib.sha256).digest())
    return {
        "token": f"{body}.{sig}",
        "expires_at": payload["exp"],
        "user": username,
        "role": role,
    }


def verify_session(token: str) -> Optional[dict]:
    try:
        body, sig = token.split(".", 1)
    except ValueError:
        return None
    expected = _b64(hmac.new(_signing_key(), body.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(sig, expected):
        return None
    try:
        payload = json.loads(_unb64(body))
    except (ValueError, json.JSONDecodeError):
        return None
    if payload.get("exp", 0) < int(time.time()):
        return None
    if payload.get("epc", 0) != _epoch():
        return None
    return payload


# ---------------------------------------------------------------- checks


def _valid(token: Optional[str]) -> bool:
    if not token:
        return False
    if settings.api_token and hmac.compare_digest(token, settings.api_token):
        return True
    return verify_session(token) is not None


def _extract(authorization: Optional[str], x_api_token: Optional[str]) -> Optional[str]:
    if x_api_token:
        return x_api_token.strip()
    if authorization:
        parts = authorization.split(None, 1)
        if len(parts) == 2 and parts[0].lower() == "bearer":
            return parts[1].strip()
        return authorization.strip()
    return None


def _no_credentials_configured() -> bool:
    return not settings.api_token and not login_configured()


async def require_token(
    authorization: Optional[str] = Header(default=None),
    x_api_token: Optional[str] = Header(default=None, alias="X-API-Token"),
) -> str:
    token = _extract(authorization, x_api_token)
    if _valid(token):
        return token  # type: ignore[return-value]
    if _no_credentials_configured():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No credentials configured on the server — set ADMIN_PASSWORD "
                   "or API_TOKEN in .env",
        )
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Sign in again",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def require_token_query(
    token: Optional[str] = Query(default=None),
    authorization: Optional[str] = Header(default=None),
    x_api_token: Optional[str] = Header(default=None, alias="X-API-Token"),
) -> str:
    """Downloads open in the browser, which cannot set headers."""
    candidate = token or _extract(authorization, x_api_token)
    if not _valid(candidate):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Sign in again")
    return candidate  # type: ignore[return-value]


def role_of(token: str) -> str:
    """The role a token carries.

    A static API_TOKEN is treated as super admin — it is configured by
    whoever has shell access to the server, so it already implies that level
    of trust.
    """
    if settings.api_token and hmac.compare_digest(token, settings.api_token):
        return "super_admin"
    payload = verify_session(token)
    return (payload or {}).get("rol", "viewer")


def require_role(required: str):
    """Dependency factory: refuse anything below `required`."""
    from . import users

    async def _guard(
        authorization: Optional[str] = Header(default=None),
        x_api_token: Optional[str] = Header(default=None, alias="X-API-Token"),
    ) -> str:
        token = _extract(authorization, x_api_token)
        if not _valid(token):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                                detail="Sign in again")
        role = role_of(token)          # type: ignore[arg-type]
        if not users.has_at_least(role, required):   # type: ignore[arg-type]
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"This needs the {required.replace('_', ' ')} role. "
                       f"You are signed in as {role.replace('_', ' ')}.",
            )
        return token                    # type: ignore[return-value]

    return _guard


require_operator = require_role("operator")
require_super_admin = require_role("super_admin")


async def authorise_websocket(ws: WebSocket) -> bool:
    token = ws.query_params.get("token")
    if not token:
        header = ws.headers.get("authorization") or ws.headers.get("x-api-token")
        token = _extract(header, None)
    if _valid(token):
        return True
    await ws.close(code=4401, reason="Unauthorized")
    return False


# ---------------------------------------------------------------- rate limit

_attempts: dict[str, list[float]] = {}
MAX_ATTEMPTS = 8
WINDOW_SECONDS = 300
LOCKOUT_SECONDS = 300


def check_rate_limit(ip: str) -> Optional[int]:
    """Returns seconds remaining in a lockout, or None if the attempt is allowed."""
    now = time.time()
    tries = [t for t in _attempts.get(ip, []) if now - t < WINDOW_SECONDS]
    _attempts[ip] = tries
    if len(tries) >= MAX_ATTEMPTS:
        return int(LOCKOUT_SECONDS - (now - tries[0]))
    return None


def record_failure(ip: str) -> None:
    _attempts.setdefault(ip, []).append(time.time())


def clear_failures(ip: str) -> None:
    _attempts.pop(ip, None)


def attempt_login(username: str, password: str) -> Optional[dict]:
    from . import users

    row = users.get(username)
    if row is None:
        # Still do the work, so a missing username and a wrong password take
        # the same time and cannot be told apart by an attacker.
        verify_password(password, hash_password("decoy"))
        return None
    if row["disabled"]:
        return None
    if not verify_password(password, row["password_hash"]):
        return None

    users.record_login(row["username"])
    session = issue_session(row["username"], row["role"])
    session["must_change"] = bool(row["must_change"])
    return session
