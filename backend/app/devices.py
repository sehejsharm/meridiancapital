"""Devices the operators have approved for sign-in, and the approvals still waiting.

The dashboard's credential is a short PIN. A PIN can be guessed from enough
addresses, so on its own it does not get anyone in: the device typing it must be
one an operator approved. A device proves itself with a random secret in an
httpOnly cookie the dashboard sets (the page's script can never read it); only
the secret's SHA-256 is stored here.

A device that is not approved and enters the right PIN is shown a code on its own
screen. An operator approves it by typing that code, on a signed-in device
(Controls, Devices) or on the VM (deploy/devices.sh). Asking for the code means
an approval cannot be given by accident or to a request the operator never saw:
a stranger who knows the PIN can raise a request, but cannot make the operator's
screen show a code to type.

Every session token names its device. Revoking a device ends its sessions at
once, signing out ends that device's sessions, and "sign out everywhere" ends all
of them.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import threading
import time
import unicodedata
from collections import deque

from app.config import settings

# Cookie secrets are 32 random bytes in base64url (43 characters); accept a little
# either side so a longer secret later is not refused.
_SECRET_RX = re.compile(r"^[A-Za-z0-9_-]{32,128}$")
# No 0/O, 1/I/L or U: a code read off one screen and typed on another.
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTVWXYZ23456789"
CODE_LEN = 8
CODE_TTL_MS = 10 * 60 * 1000
# After approval the waiting device collects its session without typing the PIN
# again, if the PIN it typed is this recent.
CLAIM_WINDOW_MS = 15 * 60 * 1000
MAX_CODE_ATTEMPTS = 5
MAX_PENDING = 20
PRUNE_AFTER_MS = 30 * 24 * 3600 * 1000
TOUCH_EVERY_MS = 60 * 1000

# A token from a tool run as root on the VM (deploy/diagnose.sh). It is not a
# device anyone signs in on: only a holder of MERIDIAN_JWT_SECRET can mint one.
CONSOLE_DEVICE = "console"

K_SESSIONS_AFTER = "auth:sessions_after_ms"   # "sign out everywhere": every older token is dead
K_DEVICES_SINCE = "auth:devices_since_ms"     # when approval became required on this desk

APPROVED, PENDING, DENIED, REVOKED = "approved", "pending", "denied", "revoked"


class DeviceError(Exception):
    """A device action that cannot be done; the message is shown to the operator."""


def now_ms() -> int:
    return int(time.time() * 1000)


def valid_secret(secret: str | None) -> bool:
    return bool(secret) and bool(_SECRET_RX.match(secret or ""))


def secret_hash(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def clean_label(label: str | None) -> str:
    """A device's name as the dashboard described it: printable, short, never empty."""
    text = "".join(ch for ch in (label or "") if unicodedata.category(ch)[0] not in ("C", "Z") or ch == " ")
    text = " ".join(text.split())[:60]
    return text or "Unnamed device"


def normalize_code(code: str | None) -> str:
    return "".join(ch for ch in (code or "").upper() if ch.isalnum())


def new_code() -> str:
    raw = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LEN))
    return f"{raw[:4]}-{raw[4:]}"


def _code_hash(device_id: str, code: str) -> str:
    # Keyed, so a copy of the database alone cannot be searched for the code.
    msg = f"{device_id}:{normalize_code(code)}".encode()
    return hmac.new(settings.jwt_secret.encode(), msg, hashlib.sha256).hexdigest()


# ── reads (cached briefly: every API call checks its device) ────────────────
_cache: dict[str, tuple[float, dict | None]] = {}
_cache_lock = threading.Lock()
CACHE_SEC = 3.0


def _forget(device_id: str | None = None) -> None:
    with _cache_lock:
        if device_id is None:
            _cache.clear()
        else:
            _cache.pop(device_id, None)


def get(db, device_id: str, fresh: bool = False) -> dict | None:
    now = time.monotonic()
    if not fresh:
        with _cache_lock:
            hit = _cache.get(device_id)
        if hit and hit[0] > now:
            return hit[1]
    with db.conn() as c:
        r = c.execute("SELECT * FROM devices WHERE id=?", (device_id,)).fetchone()
    row = dict(r) if r else None
    with _cache_lock:
        _cache[device_id] = (now + CACHE_SEC, row)
    return row


def find(db, secret: str) -> dict | None:
    if not valid_secret(secret):
        return None
    with db.conn() as c:
        r = c.execute("SELECT * FROM devices WHERE secret_hash=?", (secret_hash(secret),)).fetchone()
    return dict(r) if r else None


def approved_count(db) -> int:
    with db.conn() as c:
        return int(c.execute("SELECT COUNT(*) FROM devices WHERE status=?", (APPROVED,)).fetchone()[0])


def devices_since(db) -> int:
    """When approval became required here. Set once, the first time it is asked."""
    since = db.kv_get(K_DEVICES_SINCE, None)
    if since is None:
        since = now_ms()
        db.kv_set(K_DEVICES_SINCE, since)
    return int(since)


# ── session checks ──────────────────────────────────────────────────────────
def issued_ms(payload: dict) -> int:
    """When the session behind a token began: its own issue time, or for a
    WebSocket ticket the session it was minted from."""
    for key in ("piat", "iatms"):
        if payload.get(key) is not None:
            return int(payload[key])
    return int(payload.get("iat", 0)) * 1000


def session_problem(db, payload: dict) -> str | None:
    """Why a validly signed token no longer opens the desk, or None when it does."""
    iat = issued_ms(payload)
    if iat <= int(db.kv_get(K_SESSIONS_AFTER, 0) or 0):
        return "every session was signed out — sign in again"
    dev = payload.get("dev")
    if dev == CONSOLE_DEVICE:
        return None
    if dev:
        row = get(db, str(dev))
        if row is None or row["status"] != APPROVED:
            return "this device is no longer approved — sign in again"
        if iat <= int(row["sessions_after_ms"] or 0):
            return "this device was signed out — sign in again"
        return None
    # A session from before approval was required keeps working until it expires
    # (or is renewed, which approves the device it is on). Nothing new is issued
    # without a device, so nothing after that point passes here.
    if iat < devices_since(db):
        return None
    return "this session is not tied to an approved device — sign in again"


# ── writes ──────────────────────────────────────────────────────────────────
def touch(db, device_id: str, ip: str) -> None:
    row = get(db, device_id)
    now = now_ms()
    if row and row.get("last_ip") == ip and now - int(row.get("last_seen_ms") or 0) < TOUCH_EVERY_MS:
        return
    with db.conn() as c:
        c.execute("UPDATE devices SET last_seen_ms=?, last_ip=? WHERE id=?", (now, ip, device_id))
    _forget(device_id)


def _prune(c, now: int) -> None:
    c.execute(
        "DELETE FROM devices WHERE status != ? AND COALESCE(last_seen_ms, created_ms) < ?",
        (APPROVED, now - PRUNE_AFTER_MS),
    )


def request_approval(db, secret: str, label: str, ip: str) -> tuple[dict, str]:
    """A device not yet approved has just entered the right PIN: give it a fresh
    code to show, and remember the PIN was right so it can collect its session
    once approved. Returns (device, code). The code itself is not stored."""
    if not valid_secret(secret):
        raise DeviceError("this sign-in page is out of date — reload it")
    now = now_ms()
    label = clean_label(label)
    code = new_code()
    with db.conn() as c:
        c.execute("BEGIN IMMEDIATE")
        try:
            _prune(c, now)
            row = c.execute("SELECT * FROM devices WHERE secret_hash=?", (secret_hash(secret),)).fetchone()
            if row is not None and row["status"] == APPROVED:
                raise DeviceError("this device is already approved")
            waiting = c.execute(
                "SELECT COUNT(*) FROM devices WHERE status=? AND code_expires_ms > ?", (PENDING, now)
            ).fetchone()[0]
            if (row is None or row["status"] != PENDING) and waiting >= MAX_PENDING:
                raise DeviceError("too many devices are waiting for approval — try again later")
            device_id = row["id"] if row is not None else secrets.token_hex(8)
            if row is None:
                c.execute(
                    """INSERT INTO devices(id, secret_hash, label, status, created_ms, first_ip, last_ip,
                                           last_seen_ms, code_hash, code_expires_ms, code_attempts, pin_ok_ms)
                       VALUES(?,?,?,?,?,?,?,?,?,?,0,?)""",
                    (device_id, secret_hash(secret), label, PENDING, now, ip, ip, now,
                     _code_hash(device_id, code), now + CODE_TTL_MS, now),
                )
            else:
                c.execute(
                    """UPDATE devices SET status=?, label=?, last_ip=?, last_seen_ms=?, code_hash=?,
                              code_expires_ms=?, code_attempts=0, pin_ok_ms=? WHERE id=?""",
                    (PENDING, label, ip, now, _code_hash(device_id, code), now + CODE_TTL_MS, now, device_id),
                )
            c.execute("COMMIT")
        except Exception:
            c.execute("ROLLBACK")
            raise
    _forget(device_id)
    return get(db, device_id, fresh=True) or {}, code


def _approve_row(c, device_id: str, by: str, via: str, now: int) -> None:
    c.execute(
        """UPDATE devices SET status=?, approved_ms=?, approved_by=?, approved_via=?,
                  code_hash=NULL, code_expires_ms=NULL, code_attempts=0 WHERE id=?""",
        (APPROVED, now, by, via[:120], device_id),
    )


def approve(db, device_id: str, code: str, by: str, via: str) -> dict:
    """Approve a waiting device with the code on its screen."""
    now = now_ms()
    with db.conn() as c:
        c.execute("BEGIN IMMEDIATE")
        try:
            row = c.execute("SELECT * FROM devices WHERE id=?", (device_id,)).fetchone()
            if row is None:
                raise DeviceError("no such device")
            if row["status"] == APPROVED:
                raise DeviceError("this device is already approved")
            if row["status"] != PENDING or not row["code_hash"] or int(row["code_expires_ms"] or 0) <= now:
                raise DeviceError("this request has expired — the device has to enter the PIN again")
            if not hmac.compare_digest(_code_hash(device_id, code), row["code_hash"]):
                attempts = int(row["code_attempts"] or 0) + 1
                if attempts >= MAX_CODE_ATTEMPTS:
                    c.execute(
                        """UPDATE devices SET status=?, code_hash=NULL, code_expires_ms=NULL, pin_ok_ms=NULL,
                                  code_attempts=? WHERE id=?""",
                        (DENIED, attempts, device_id),
                    )
                    c.execute("COMMIT")
                    _forget(device_id)
                    raise DeviceError("wrong code too many times — this request was refused")
                c.execute("UPDATE devices SET code_attempts=? WHERE id=?", (attempts, device_id))
                c.execute("COMMIT")
                raise DeviceError("that code does not match the one on the device")
            _approve_row(c, device_id, by, via, now)
            c.execute("COMMIT")
        except Exception:
            # A wrong code commits its attempt count before raising; anything else rolls back.
            if c.in_transaction:
                c.execute("ROLLBACK")
            raise
    _forget(device_id)
    return get(db, device_id, fresh=True) or {}


def approve_by_code(db, code: str, by: str, via: str) -> dict:
    """Approve whichever waiting device shows this code (the console's way in)."""
    now = now_ms()
    with db.conn() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT id, code_hash FROM devices WHERE status=? AND code_expires_ms > ?", (PENDING, now))]
    for r in rows:
        if r["code_hash"] and hmac.compare_digest(_code_hash(r["id"], code), r["code_hash"]):
            return approve(db, r["id"], code, by, via)
    raise DeviceError("no device is waiting with that code (codes last 10 minutes)")


def approve_secret(db, secret: str, label: str, ip: str, by: str, via: str) -> dict:
    """Approve the device holding this secret outright: it proved itself another
    way (a passkey, or a session from before approval was required)."""
    if not valid_secret(secret):
        raise DeviceError("this page is out of date — reload it")
    now = now_ms()
    label = clean_label(label)
    with db.conn() as c:
        c.execute("BEGIN IMMEDIATE")
        try:
            row = c.execute("SELECT * FROM devices WHERE secret_hash=?", (secret_hash(secret),)).fetchone()
            if row is None:
                device_id = secrets.token_hex(8)
                c.execute(
                    """INSERT INTO devices(id, secret_hash, label, status, created_ms, first_ip, last_ip, last_seen_ms)
                       VALUES(?,?,?,?,?,?,?,?)""",
                    (device_id, secret_hash(secret), label, PENDING, now, ip, ip, now),
                )
            else:
                device_id = row["id"]
                c.execute("UPDATE devices SET label=?, last_ip=?, last_seen_ms=? WHERE id=?",
                          (label, ip, now, device_id))
            if row is None or row["status"] != APPROVED:
                _approve_row(c, device_id, by, via, now)
            c.execute("COMMIT")
        except Exception:
            c.execute("ROLLBACK")
            raise
    _forget(device_id)
    return get(db, device_id, fresh=True) or {}


def claim(db, secret: str) -> tuple[str, dict | None]:
    """A waiting device asks whether it has been approved.

    Returns (state, device): "approved" with the device when it may collect a
    session now (once: the PIN it typed is spent), "pending" while it waits, and
    "refused" when it was refused, revoked, or waited too long.
    """
    row = find(db, secret)
    now = now_ms()
    if row is None:
        return "refused", None
    if row["status"] == PENDING:
        if int(row["code_expires_ms"] or 0) > now:
            return "pending", row
        return "refused", row
    if row["status"] != APPROVED or not row["pin_ok_ms"] or now - int(row["pin_ok_ms"]) > CLAIM_WINDOW_MS:
        return "refused", row
    with db.conn() as c:
        cur = c.execute("UPDATE devices SET pin_ok_ms=NULL WHERE id=? AND pin_ok_ms=?", (row["id"], row["pin_ok_ms"]))
        spent = cur.rowcount == 1
    _forget(row["id"])
    return ("approved", row) if spent else ("refused", row)


def deny(db, device_id: str) -> dict:
    with db.conn() as c:
        row = c.execute("SELECT status FROM devices WHERE id=?", (device_id,)).fetchone()
        if row is None:
            raise DeviceError("no such device")
        if row["status"] != PENDING:
            raise DeviceError("this device is not waiting for approval")
        c.execute(
            "UPDATE devices SET status=?, code_hash=NULL, code_expires_ms=NULL, pin_ok_ms=NULL WHERE id=?",
            (DENIED, device_id),
        )
    _forget(device_id)
    return get(db, device_id, fresh=True) or {}


def revoke(db, device_id: str) -> dict:
    now = now_ms()
    with db.conn() as c:
        row = c.execute("SELECT status FROM devices WHERE id=?", (device_id,)).fetchone()
        if row is None:
            raise DeviceError("no such device")
        c.execute(
            """UPDATE devices SET status=?, sessions_after_ms=?, code_hash=NULL, code_expires_ms=NULL,
                      pin_ok_ms=NULL WHERE id=?""",
            (REVOKED, now, device_id),
        )
    _forget(device_id)
    return get(db, device_id, fresh=True) or {}


def end_sessions(db, device_id: str) -> None:
    """Sign this device out: every token it holds stops working."""
    with db.conn() as c:
        c.execute("UPDATE devices SET sessions_after_ms=? WHERE id=?", (now_ms(), device_id))
    _forget(device_id)


def end_all_sessions(db) -> None:
    db.kv_set(K_SESSIONS_AFTER, now_ms())
    _forget()


# ── what the dashboard shows ────────────────────────────────────────────────
def listing(db, current: str | None = None) -> dict:
    now = now_ms()
    with db.conn() as c:
        rows = [dict(r) for r in c.execute("SELECT * FROM devices ORDER BY created_ms")]

    def public(r: dict) -> dict:
        return {
            "id": r["id"], "label": r["label"], "status": r["status"],
            "created_ms": r["created_ms"], "approved_ms": r["approved_ms"],
            "approved_by": r["approved_by"], "approved_via": r["approved_via"],
            "last_seen_ms": r["last_seen_ms"], "last_ip": r["last_ip"], "first_ip": r["first_ip"],
            "current": r["id"] == current,
        }

    approved = [public(r) for r in rows if r["status"] == APPROVED]
    pending = [
        {**public(r), "expires_in": max(0, int((int(r["code_expires_ms"]) - now) / 1000))}
        for r in rows
        if r["status"] == PENDING and int(r["code_expires_ms"] or 0) > now
    ]
    return {"approved": approved, "pending": pending}


# ── guards for devices not yet approved ─────────────────────────────────────
class UnknownDeviceBudget:
    """A ceiling on wrong PINs from devices that are not approved, from all
    addresses together.

    The per-address lockout stops one source; this stops many. When the ceiling
    is reached, PIN sign-in from new devices pauses for a while. Approved devices
    are not affected, so a flood of guesses cannot lock the operator out.
    """

    def __init__(self, max_failures: int = 20, window_sec: int = 3600, pause_sec: int = 3600):
        self.max_failures = max_failures
        self.window_sec = window_sec
        self.pause_sec = pause_sec
        self._fails: deque[float] = deque()
        self._paused_until = 0.0
        self._lock = threading.Lock()

    def paused_for(self) -> int:
        with self._lock:
            return max(0, int(self._paused_until - time.time()))

    def fail(self) -> bool:
        """Record a wrong PIN; True when this one starts a pause."""
        now = time.time()
        with self._lock:
            while self._fails and now - self._fails[0] > self.window_sec:
                self._fails.popleft()
            self._fails.append(now)
            if len(self._fails) >= self.max_failures and now >= self._paused_until:
                self._paused_until = now + self.pause_sec
                self._fails.clear()
                return True
            return False

    def reset(self) -> None:
        with self._lock:
            self._fails.clear()
            self._paused_until = 0.0


unknown_budget = UnknownDeviceBudget()


class AlertGate:
    """At most one alert per device every few minutes, and a ceiling overall, so a
    stranger with the PIN cannot turn approval requests into a flood."""

    def __init__(self, per_device_sec: int = 300, max_per_hour: int = 12):
        self.per_device_sec = per_device_sec
        self.max_per_hour = max_per_hour
        self._last: dict[str, float] = {}
        self._sent: deque[float] = deque()
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.time()
        with self._lock:
            while self._sent and now - self._sent[0] > 3600:
                self._sent.popleft()
            if now - self._last.get(key, 0.0) < self.per_device_sec or len(self._sent) >= self.max_per_hour:
                return False
            self._last[key] = now
            self._sent.append(now)
            if len(self._last) > 1000:
                self._last = {k: v for k, v in self._last.items() if now - v < self.per_device_sec}
            return True

    def reset(self) -> None:
        with self._lock:
            self._last.clear()
            self._sent.clear()


alert_gate = AlertGate()
