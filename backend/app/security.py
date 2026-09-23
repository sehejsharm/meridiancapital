"""Authentication: PBKDF2 password verification, JWTs, and short-lived WS tickets.

The dashboard is a single-operator surface, so there is no user table: one
password hash in the environment, one signing key, and bearer tokens on top.
WebSocket connections cannot carry an Authorization header from the browser, so
they present a one-shot ticket minted over authenticated HTTP instead of putting
a long-lived token in a URL that lands in proxy logs.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import settings

PBKDF2_ROUNDS = 320_000
_bearer = HTTPBearer(auto_error=False)


# ── password hashing ─────────────────────────────────────────────────────────
def hash_password(password: str, rounds: int = PBKDF2_ROUNDS) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, rounds)
    return f"pbkdf2_sha256${rounds}${base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, rounds_s, salt_b64, dk_b64 = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), base64.b64decode(salt_b64), int(rounds_s)
        )
        return hmac.compare_digest(dk, base64.b64decode(dk_b64))
    except (ValueError, TypeError):
        return False


# ── minimal JWT (HS256) ──────────────────────────────────────────────────────
def _b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64u_dec(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def issue_token(subject: str, ttl_seconds: int, scope: str = "dashboard") -> str:
    now = int(time.time())
    header = _b64u(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    payload = _b64u(
        json.dumps(
            {"sub": subject, "scope": scope, "iat": now, "exp": now + ttl_seconds,
             "jti": secrets.token_hex(8)},
            separators=(",", ":"),
        ).encode()
    )
    signing_input = f"{header}.{payload}".encode()
    sig = hmac.new(settings.jwt_secret.encode(), signing_input, hashlib.sha256).digest()
    return f"{header}.{payload}.{_b64u(sig)}"


def decode_token(token: str) -> dict:
    try:
        header_b64, payload_b64, sig_b64 = token.split(".")
    except ValueError:
        raise ValueError("malformed token")
    expected = hmac.new(
        settings.jwt_secret.encode(), f"{header_b64}.{payload_b64}".encode(), hashlib.sha256
    ).digest()
    if not hmac.compare_digest(expected, _b64u_dec(sig_b64)):
        raise ValueError("bad signature")
    payload = json.loads(_b64u_dec(payload_b64))
    if int(payload.get("exp", 0)) < int(time.time()):
        raise ValueError("token expired")
    return payload


@dataclass
class Principal:
    subject: str
    scope: str


def _unauthorised(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


async def require_auth(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> Principal:
    if creds is None or not creds.credentials:
        raise _unauthorised("missing bearer token")
    try:
        payload = decode_token(creds.credentials)
    except ValueError as e:
        raise _unauthorised(str(e))
    if payload.get("scope") != "dashboard":
        raise _unauthorised("token is not valid for this endpoint")
    return Principal(subject=str(payload.get("sub", "?")), scope=str(payload.get("scope")))


def authenticate_ws_ticket(ticket: str) -> Principal:
    payload = decode_token(ticket)
    if payload.get("scope") != "ws":
        raise ValueError("not a websocket ticket")
    return Principal(subject=str(payload.get("sub", "?")), scope="ws")


def client_ip(request: Request) -> str:
    """The address a request should be throttled and audited under.

    Every dashboard sign-in arrives from Vercel's servers, and Caddy sets
    X-Forwarded-For to the peer it saw — Vercel, not the person. Keyed on that,
    the login lockout is shared by everyone, and any bot's five wrong guesses
    lock the operator out. The relay therefore forwards the real client address,
    and it is believed only alongside the shared relay secret: a script calling
    this API directly cannot mint its own address to dodge the lockout.
    """
    forwarded = request.headers.get("x-meridian-client-ip", "").strip()
    presented = request.headers.get("x-meridian-relay", "")
    secret = settings.relay_secret
    if forwarded and secret and hmac.compare_digest(presented.encode(), secret.encode()):
        return forwarded[:64]

    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "?"


# ── brute-force throttle on the single login route ───────────────────────────
class LoginThrottle:
    """Rate limit on the single login route.

    The operator credential is a short numeric PIN, so the whole keyspace is
    small enough to walk. These numbers are what make that impractical: five
    tries, then a lockout that doubles on each repeat offence up to an hour.
    At the cap an exhaustive search of a 4-digit PIN takes over two months per
    source address.
    """

    def __init__(self, max_attempts: int = 5, window_sec: int = 300,
                 lockout_sec: int = 900, max_lockout_sec: int = 3600):
        self.max_attempts = max_attempts
        self.window_sec = window_sec
        self.lockout_sec = lockout_sec
        self.max_lockout_sec = max_lockout_sec
        self._hits: dict[str, list[float]] = {}
        self._locked: dict[str, float] = {}
        self._strikes: dict[str, int] = {}

    def check(self, key: str) -> None:
        now = time.time()
        until = self._locked.get(key, 0.0)
        if now < until:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"too many failed attempts; retry in {int(until - now)}s",
            )

    def fail(self, key: str) -> None:
        now = time.time()
        hits = [h for h in self._hits.get(key, []) if now - h < self.window_sec]
        hits.append(now)
        self._hits[key] = hits
        if len(hits) >= self.max_attempts:
            strikes = self._strikes.get(key, 0) + 1
            self._strikes[key] = strikes
            penalty = min(self.lockout_sec * 2 ** (strikes - 1), self.max_lockout_sec)
            self._locked[key] = now + penalty
            self._hits[key] = []

    def succeed(self, key: str) -> None:
        self._hits.pop(key, None)
        self._locked.pop(key, None)
        self._strikes.pop(key, None)


login_throttle = LoginThrottle()
