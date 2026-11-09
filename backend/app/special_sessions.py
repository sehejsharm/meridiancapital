"""Sessions NSE holds outside its calendar (Muhurat trading), kept in the database.

NSE's published ones ship in shared.nse_holidays and are loaded once each, like
the holiday list: one the operator edits keeps its edit, one they remove stays
removed. The operator can add one NSE announces at short notice.
"""

from __future__ import annotations

import re
from datetime import date

K_SPECIAL = "calendar:special_sessions"
K_SEEDED = "calendar:special_sessions:seeded"
_TIME = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def load(db) -> dict[str, dict]:
    return dict(db.kv_get(K_SPECIAL, {}) or {})


def seed(db, shipped: dict[str, dict]) -> list[str]:
    """Load each shipped session once. Returns the days added."""
    seen = set(db.kv_get(K_SEEDED, []) or [])
    fresh = sorted(d for d in shipped if d not in seen)
    if fresh:
        cur = load(db)
        for d in fresh:
            cur.setdefault(d, dict(shipped[d]))
        db.kv_set(K_SPECIAL, cur)
        db.kv_set(K_SEEDED, sorted(seen | set(fresh)))
    return fresh


def validate(day: str, open_: str, close: str) -> None:
    """Raises ValueError with a sentence the dashboard can show."""
    try:
        date.fromisoformat(day)
    except ValueError:
        raise ValueError("day must be an ISO date, e.g. 2026-11-08")
    for name, t in (("open", open_), ("close", close)):
        if not _TIME.match(t or ""):
            raise ValueError(f"{name} must be a 24-hour time, e.g. 14:20")
    if close <= open_:
        raise ValueError("the close must be after the open")


def put(db, day: str, open_: str, close: str, label: str) -> dict[str, dict]:
    validate(day, open_, close)
    cur = load(db)
    cur[day] = {"open": open_, "close": close, "label": (label or "Special session").strip()[:80]}
    db.kv_set(K_SPECIAL, cur)
    return cur


def remove(db, day: str) -> dict[str, dict]:
    cur = load(db)
    cur.pop(day, None)
    db.kv_set(K_SPECIAL, cur)
    return cur
