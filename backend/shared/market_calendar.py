"""NSE session calendar used by the auto start/stop scheduler.

Holidays live in the database. NSE's published list is loaded into it at
startup (``shared.nse_holidays``) and the operator can add a closure NSE
declares at short notice, or remove one it calls off. A year with no holidays
loaded is not a safety problem — the engine's own staleness guards refuse to
trade on a dead feed — it only costs an idle broker session, so the dashboard
surfaces it as a warning, not an error.
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


def closures(today: date, holidays: dict[str, str]) -> dict:
    """The closures worth telling the operator about, for the deck's banner.

    ``holidays`` maps each closed day to its name. ``today`` is set on a
    holiday. ``ahead`` lists every holiday between today and the next session,
    so the last session before a closure announces it — Friday's included, for
    a Monday holiday — and so does every day of the weekend before one.
    """

    def named(d: str) -> dict:
        return {"day": d, "label": holidays.get(d) or "Exchange holiday"}

    days = set(holidays)
    nxt = next_trading_day(today, days)
    ahead = []
    cur = today + timedelta(days=1)
    while nxt is not None and cur < nxt:
        if cur.isoformat() in days and not is_weekend(cur):
            ahead.append(named(cur.isoformat()))
        cur += timedelta(days=1)
    t = today.isoformat()
    upcoming = sorted(d for d in days if d > t and not is_weekend(date.fromisoformat(d)))
    return {
        "today": named(t) if t in days and not is_weekend(today) else None,
        "ahead": ahead,
        "next_session": nxt.isoformat() if nxt else None,
        "next_holiday": named(upcoming[0]) if upcoming else None,
    }
