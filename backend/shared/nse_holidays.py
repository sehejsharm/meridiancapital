"""NSE's published trading holidays, loaded into the calendar automatically.

These are the closures of the equity and equity-derivatives (F&O) segments as
NSE announced them, including closures it declared after the year's list came
out (15 Jan 2026, for the Maharashtra municipal elections). Only weekday
closures are listed: a weekend is never a session anyway. The Sunday Muhurat
session (8 Nov 2026) is a special evening session the scheduler does not run.

The list ships with the code, so a fresh install knows the year's closures
without anyone typing them in. Each date is loaded once (see
``Database.seed_holidays``): a date the operator removes stays removed, and a
year added here later is picked up on the next restart.

NSE publishes the next year's list in December. Add it here when it does.
"""

from __future__ import annotations

SOURCE = "NSE"

NSE_HOLIDAYS: dict[str, str] = {
    "2026-01-15": "Municipal Corporation Election — Maharashtra",
    "2026-01-26": "Republic Day",
    "2026-03-03": "Holi",
    "2026-03-26": "Shri Ram Navami",
    "2026-03-31": "Shri Mahavir Jayanti",
    "2026-04-03": "Good Friday",
    "2026-04-14": "Dr. Baba Saheb Ambedkar Jayanti",
    "2026-05-01": "Maharashtra Day",
    "2026-05-28": "Bakri Id",
    "2026-06-26": "Muharram",
    "2026-09-14": "Ganesh Chaturthi",
    "2026-10-02": "Mahatma Gandhi Jayanti",
    "2026-10-20": "Dussehra",
    "2026-11-10": "Diwali Balipratipada",
    "2026-11-24": "Prakash Gurpurb Sri Guru Nanak Dev",
    "2026-12-25": "Christmas",
}

# The years the list above is complete for.
YEARS_COVERED = frozenset(int(d[:4]) for d in NSE_HOLIDAYS)
