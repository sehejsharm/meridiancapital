"""Two-person control over switching to real money.

Paper mode is reversible; live mode is not. A single operator with a session
token should not be able to point the algorithm at real capital on their own —
that is the standard institutional control, and it is cheap to implement.

The flow: one super admin requests the switch, a *different* super admin
approves it, and only then does the mode change. A request expires so an
approval granted last month cannot be used today, and every step is written to
an audit trail that nothing in the application deletes.
"""
from __future__ import annotations

import json
import secrets
from datetime import datetime, timedelta
from typing import Any, Optional

from . import db

KV_PENDING = "live_mode_request"
REQUEST_TTL_MINUTES = 30

AUDIT_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    ts       TEXT NOT NULL,
    actor    TEXT NOT NULL,
    action   TEXT NOT NULL,
    detail   TEXT,
    payload  TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit(ts);
"""


def init() -> None:
    with db.connect() as conn:
        conn.executescript(AUDIT_SCHEMA)


def record(actor: str, action: str, detail: str = "", **payload: Any) -> None:
    """Append to the audit trail. Never raises into a request handler."""
    try:
        db.execute(
            "INSERT INTO audit (ts, actor, action, detail, payload) VALUES (?,?,?,?,?)",
            (datetime.now().isoformat(timespec="seconds"), actor, action, detail,
             json.dumps(payload, default=str) if payload else None),
        )
    except Exception:
        pass


def trail(limit: int = 200) -> list[dict]:
    rows = db.query("SELECT * FROM audit ORDER BY id DESC LIMIT ?", (limit,))
    for r in rows:
        r["payload"] = json.loads(r["payload"]) if r["payload"] else None
    return rows


# ---------------------------------------------------------------- requests


def _now() -> datetime:
    return datetime.now()


def pending() -> Optional[dict]:
    """The live-mode request awaiting a second approval, if it is still valid."""
    raw = db.kv_get(KV_PENDING)
    if not raw:
        return None
    try:
        expires = datetime.fromisoformat(raw["expires_at"])
    except (KeyError, TypeError, ValueError):
        db.kv_set(KV_PENDING, None)
        return None
    if _now() >= expires:
        # Expired requests are cleared rather than lingering as a live approval.
        db.kv_set(KV_PENDING, None)
        return None
    return raw


def request_live(actor: str, reason: str = "") -> dict:
    existing = pending()
    if existing:
        raise ValueError(
            f"{existing['requested_by']} already requested this at "
            f"{existing['requested_at'][11:16]}. It needs a different super admin "
            f"to approve, or it lapses on its own."
        )
    entry = {
        "id": secrets.token_urlsafe(8),
        "requested_by": actor,
        "requested_at": _now().isoformat(timespec="seconds"),
        "expires_at": (_now() + timedelta(minutes=REQUEST_TTL_MINUTES)).isoformat(
            timespec="seconds"),
        "reason": reason.strip()[:200],
    }
    db.kv_set(KV_PENDING, entry)
    record(actor, "live_mode_requested", reason or "no reason given", **entry)
    return entry


def cancel(actor: str) -> None:
    existing = pending()
    db.kv_set(KV_PENDING, None)
    if existing:
        record(actor, "live_mode_cancelled",
               f"cancelled {existing['requested_by']}'s request")


def approve(actor: str) -> dict:
    """Approve a pending request. The requester cannot approve their own."""
    entry = pending()
    if not entry:
        raise ValueError("There is no live-mode request to approve, or it has lapsed.")
    if entry["requested_by"].lower() == actor.lower():
        raise ValueError(
            "A live-mode switch needs two different super admins. "
            f"{actor} raised this request, so someone else has to approve it."
        )
    db.kv_set(KV_PENDING, None)
    record(actor, "live_mode_approved",
           f"approved {entry['requested_by']}'s request", **entry)
    return {**entry, "approved_by": actor,
            "approved_at": _now().isoformat(timespec="seconds")}


def state(super_admin_count: int) -> dict:
    """What the dashboard needs to render the control."""
    p = pending()
    return {
        "pending": p,
        "ttl_minutes": REQUEST_TTL_MINUTES,
        # With only one super admin the rule cannot be satisfied, and saying so
        # up front is better than letting someone request a switch nobody can
        # approve.
        "possible": super_admin_count >= 2,
        "super_admins": super_admin_count,
    }
