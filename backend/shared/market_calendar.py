"""NSE session calendar used by the auto start/stop scheduler.

Holidays are operator-maintained in the database rather than hardcoded, because
the NSE list changes every year and a stale hardcoded list would silently start
the engine on a closed day. An unconfigured calendar is not a safety problem —
the engine's own staleness guards refuse to trade on a dead feed — it only costs
an idle broker session, so the dashboard surfaces it as a warning, not an error.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

# Engine warm-up needs a few minutes: Angel login, scrip master, 90+ candles.
START_AT = (9, 5)
STOP_AT = (15, 25)


def is_weekend(d: date) -> bool:
    return d.weekday() >= 5


def is_trading_day(d: date, holidays: set[str]) -> bool:
    return not is_weekend(d) and d.isoformat() not in holidays


def next_trading_day(d: date, holidays: set[str], limit: int = 30) -> date | None:
    cur = d + timedelta(days=1)
    for _ in range(limit):
        if is_trading_day(cur, holidays):
            return cur
        cur += timedelta(days=1)
    return None


def _at(d: date, hm: tuple[int, int]) -> datetime:
    return datetime(d.year, d.month, d.day, hm[0], hm[1])


def should_be_running(now: datetime, holidays: set[str]) -> bool:
    """True while the engine is meant to be up for today's session."""
    d = now.date()
    if not is_trading_day(d, holidays):
        return False
    return _at(d, START_AT) <= now < _at(d, STOP_AT)


def session_window(d: date) -> tuple[datetime, datetime]:
    return _at(d, START_AT), _at(d, STOP_AT)


def next_transition(now: datetime, holidays: set[str]) -> dict:
    """What the scheduler will do next, for display on the dashboard."""
    d = now.date()
    if is_trading_day(d, holidays):
        start, stop = session_window(d)
        if now < start:
            return {"action": "start", "at": start.isoformat(timespec="seconds"), "day": d.isoformat()}
        if now < stop:
            return {"action": "stop", "at": stop.isoformat(timespec="seconds"), "day": d.isoformat()}
    nxt = next_trading_day(d, holidays)
    if not nxt:
        return {"action": "none", "at": None, "day": None}
    start, _ = session_window(nxt)
    return {"action": "start", "at": start.isoformat(timespec="seconds"), "day": nxt.isoformat()}


def calendar_configured(holidays: set[str], year: int) -> bool:
    prefix = f"{year}-"
    return any(h.startswith(prefix) for h in holidays)
