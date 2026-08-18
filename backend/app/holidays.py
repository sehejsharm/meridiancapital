"""NSE trading holidays.

The scheduler skips these dates so the bot does not burn an Angel One login
on a day the exchange is shut. Weekends are handled separately.

Update this list each December when NSE publishes the next year's calendar:
https://www.nseindia.com/resources/exchange-communication-holidays
Any date not listed simply runs — a wrong entry only costs a skipped session,
never a bad trade, since the algorithm re-checks market hours itself.
"""
from __future__ import annotations

from datetime import date, timedelta

# Equity segment trading holidays (excluding weekends).
NSE_HOLIDAYS: set[str] = {
    # ---- 2026 ----
    "2026-01-26",  # Republic Day
    "2026-03-04",  # Holi
    "2026-03-21",  # Id-ul-Fitr (Ramzan Id)
    "2026-03-26",  # Ram Navami
    "2026-03-31",  # Mahavir Jayanti
    "2026-04-03",  # Good Friday
    "2026-04-14",  # Dr. Ambedkar Jayanti
    "2026-05-01",  # Maharashtra Day
    "2026-05-27",  # Bakri Id
    "2026-06-26",  # Muharram
    "2026-08-15",  # Independence Day
    "2026-08-26",  # Ganesh Chaturthi
    "2026-10-02",  # Mahatma Gandhi Jayanti
    "2026-10-20",  # Dussehra
    "2026-11-09",  # Diwali Laxmi Pujan (muhurat session separate)
    "2026-11-10",  # Diwali Balipratipada
    "2026-11-24",  # Guru Nanak Jayanti
    "2026-12-25",  # Christmas

    # ---- 2027 ----
    "2027-01-26",  # Republic Day
    "2027-03-11",  # Holi
    "2027-03-26",  # Good Friday
    "2027-05-01",  # Maharashtra Day
    "2027-08-15",  # Independence Day
    "2027-10-02",  # Mahatma Gandhi Jayanti
    "2027-12-25",  # Christmas
}


def is_holiday(d: date) -> bool:
    return d.strftime("%Y-%m-%d") in NSE_HOLIDAYS


def is_weekend(d: date) -> bool:
    return d.weekday() >= 5


def is_trading_day(d: date, skip_holidays: bool = True) -> bool:
    if is_weekend(d):
        return False
    if skip_holidays and is_holiday(d):
        return False
    return True


def why_not_trading(d: date, skip_holidays: bool = True) -> str | None:
    if is_weekend(d):
        return d.strftime("%A") + " — market closed"
    if skip_holidays and is_holiday(d):
        return "NSE trading holiday"
    return None


# ---------------------------------------------------------------- expiry

def _last_thursday(year: int, month: int) -> date:
    """The last Thursday of a month, which is the monthly F&O expiry."""
    from calendar import monthrange
    d = date(year, month, monthrange(year, month)[1])
    while d.weekday() != 3:                 # 3 = Thursday
        d -= timedelta(days=1)
    return d


def _shift_for_holiday(d: date, skip_holidays: bool = True) -> date:
    """NSE moves an expiry that lands on a holiday to the previous session."""
    guard = 0
    while (is_weekend(d) or (skip_holidays and is_holiday(d))) and guard < 10:
        d -= timedelta(days=1)
        guard += 1
    return d


def expiry_kind(d: date, skip_holidays: bool = True) -> str:
    """"monthly" | "weekly" | "none" for a given date."""
    if _shift_for_holiday(_last_thursday(d.year, d.month), skip_holidays) == d:
        return "monthly"
    # Any other Thursday — shifted back if that Thursday is a holiday.
    if d.weekday() == 3 or _shift_for_holiday(d + timedelta(days=(3 - d.weekday()) % 7),
                                              skip_holidays) == d:
        thursday = d + timedelta(days=(3 - d.weekday()) % 7)
        if _shift_for_holiday(thursday, skip_holidays) == d:
            return "weekly"
    return "none"


def next_expiry(d: date, skip_holidays: bool = True) -> tuple[date, str]:
    """The next expiry on or after `d`, and whether it is weekly or monthly."""
    probe = d
    for _ in range(45):
        kind = expiry_kind(probe, skip_holidays)
        if kind != "none":
            return probe, kind
        probe += timedelta(days=1)
    return d, "none"


def expiry_state(d: date, skip_holidays: bool = True) -> dict:
    """What the dashboard shows in its market tape."""
    kind = expiry_kind(d, skip_holidays)
    nxt, nxt_kind = next_expiry(d, skip_holidays)
    return {
        "date": d.isoformat(),
        "is_expiry": kind != "none",
        "kind": kind,
        "label": {"monthly": "MONTHLY", "weekly": "WEEKLY", "none": "NONE"}[kind],
        "next_date": nxt.isoformat(),
        "next_kind": nxt_kind,
        "days_to_next": (nxt - d).days,
    }
