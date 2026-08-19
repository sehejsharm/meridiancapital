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
import threading
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
    payload  TEXT,
    ip       TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit(ts);
CREATE INDEX IF NOT EXISTS idx_audit_actor ON audit(actor);
CREATE INDEX IF NOT EXISTS idx_audit_action ON audit(action);
"""

# Where the current request came from. A thread-local rather than a parameter
# threaded through every call site: `record()` is called from ~20 places and
# from the supervisor's reader thread, and most of them have no idea a request
# exists. Set by middleware, cleared after each request.
_request = threading.local()


def set_request_ip(ip: Optional[str]) -> None:
    _request.ip = ip


def current_ip() -> Optional[str]:
    return getattr(_request, "ip", None)


def init() -> None:
    with db.connect() as conn:
        conn.executescript(AUDIT_SCHEMA)
        # An audit table written before `ip` existed keeps its rows; the column
        # is added empty rather than the history being rebuilt or dropped.
        cols = {r[1] for r in conn.execute("PRAGMA table_info(audit)")}
        if "ip" not in cols:
            conn.execute("ALTER TABLE audit ADD COLUMN ip TEXT")


def record(actor: str, action: str, detail: str = "", **payload: Any) -> None:
    """Append to the audit trail. Never raises into a request handler."""
    try:
        db.execute(
            "INSERT INTO audit (ts, actor, action, detail, payload, ip) "
            "VALUES (?,?,?,?,?,?)",
            (datetime.now().isoformat(timespec="seconds"), actor, action, detail,
             json.dumps(payload, default=str) if payload else None, current_ip()),
        )
    except Exception:
        pass


def trail(limit: int = 200, action: str = "", actor: str = "",
          start: str = "", end: str = "") -> list[dict]:
    """Filtered, newest first. Every filter is optional and they compose."""
    sql = "SELECT * FROM audit WHERE 1=1"
    params: list[Any] = []
    if action:
        # Matches a whole group as well as one exact action: "user" finds
        # user_created, user_updated and user_deleted.
        # ESCAPE belongs to the LIKE, so it goes inside the parentheses;
        # otherwise the underscore is a single-character wildcard and "user"
        # would also match a hypothetical "userX".
        sql += " AND (action = ? OR action LIKE ? ESCAPE '\\')"
        params += [action, action + "\\_%"]
    if actor:
        sql += " AND actor = ? COLLATE NOCASE"
        params.append(actor)
    if start:
        sql += " AND ts >= ?"
        params.append(start)
    if end:
        # Inclusive of the whole end day when a bare date is given.
        params.append(end if len(end) > 10 else end + "T23:59:59")
        sql += " AND ts <= ?"
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(max(1, min(int(limit), 5000)))

    rows = db.query(sql, params)
    for r in rows:
        r["payload"] = json.loads(r["payload"]) if r["payload"] else None
    return rows


def actors() -> list[str]:
    """Everyone who appears in the trail, for the filter dropdown."""
    return [r["actor"] for r in
            db.query("SELECT DISTINCT actor FROM audit ORDER BY actor")]


def to_csv(rows: list[dict]) -> str:
    """The trail as a file. Compliance asks for this by name."""
    import csv
    import io
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["Timestamp", "Operator", "Action", "Detail", "IP address", "Payload"])
    for r in rows:
        w.writerow([r.get("ts"), r.get("actor"), r.get("action"), r.get("detail"),
                    r.get("ip") or "",
                    json.dumps(r.get("payload"), default=str) if r.get("payload") else ""])
    return buf.getvalue()


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
