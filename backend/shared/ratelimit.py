"""One rate budget for the whole account, shared across engine processes.

Angel One's caps are per API key, not per process. Each engine used to pace
itself independently, which was correct when there was exactly one — with
several algorithms running, and a shadow doubling one of them, four engines each
pacing to 8 requests a second present 32 to Angel and get the key throttled or
banned.

So the budget lives in SQLite, the same WAL database the engines already use as
a bus, and every broker call reserves from it under BEGIN IMMEDIATE. A sliding
window per endpoint: a call is admitted only if the account has made fewer than
`cap x window` in the last `window` seconds, and otherwise the caller is told
exactly how long to wait for the oldest call to age out.

The cost is one small transaction per broker call. At the caps in play — the
tightest is one call every half second — that is nothing next to the network
round-trip it is pacing, and it is the only way a limit shared between processes
can actually be shared.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS rate_ledger (
    endpoint TEXT NOT NULL,
    ts       REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rate_ledger ON rate_ledger(endpoint, ts);
"""

# Never sleep longer than this in one go, so a caller stays responsive to a
# shutdown signal rather than blocking inside a single long wait.
MAX_SINGLE_WAIT = 1.0


class SharedRateLimiter:
    """Account-wide sliding-window limiter backed by SQLite."""

    def __init__(self, db_path: Path | str, window_sec: float = 1.0):
        self.path = Path(db_path)
        self.window = window_sec
        self._ensure()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=30.0, isolation_level=None)
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    def _ensure(self) -> None:
        try:
            conn = self._connect()
        except sqlite3.Error:
            return
        try:
            conn.executescript(SCHEMA)
        except sqlite3.Error:
            pass
        finally:
            conn.close()

    def reserve(self, endpoint: str, cap_per_sec: float) -> float:
        """Claim one call. Returns 0 if admitted, else seconds to wait.

        The whole read-decide-write runs inside one immediate transaction, so
        two engines cannot both see a free slot and both take it.
        """
        if not cap_per_sec or cap_per_sec <= 0:
            return 0.0
        allowance = max(1, int(cap_per_sec * self.window))
        now = time.time()
        cutoff = now - self.window

        # The connect is inside the guard on purpose: opening the database is
        # itself a failure point (a missing directory, a read-only volume), and
        # a ledger that cannot be reached must not stop the engine trading. It
        # degrades to the in-process limiter, which still paces this caller.
        conn = None
        try:
            conn = self._connect()
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "DELETE FROM rate_ledger WHERE endpoint=? AND ts < ?", (endpoint, cutoff)
            )
            used = conn.execute(
                "SELECT COUNT(*) FROM rate_ledger WHERE endpoint=?", (endpoint,)
            ).fetchone()[0]

            if used < allowance:
                conn.execute(
                    "INSERT INTO rate_ledger(endpoint, ts) VALUES(?,?)", (endpoint, now)
                )
                conn.execute("COMMIT")
                return 0.0

            oldest = conn.execute(
                "SELECT MIN(ts) FROM rate_ledger WHERE endpoint=?", (endpoint,)
            ).fetchone()[0]
            conn.execute("COMMIT")
        except sqlite3.Error:
            return 0.0
        finally:
            if conn is not None:
                try:
                    conn.close()
                except sqlite3.Error:
                    pass

        if oldest is None:
            return 0.0
        return max(0.0, min((oldest + self.window) - time.time(), MAX_SINGLE_WAIT))

    def acquire(self, endpoint: str, cap_per_sec: float, sleep=time.sleep) -> float:
        """Block until this call fits the account's budget. Returns seconds waited."""
        waited = 0.0
        while True:
            wait = self.reserve(endpoint, cap_per_sec)
            if wait <= 0:
                return waited
            sleep(wait)
            waited += wait

    def usage(self, endpoint: str) -> int:
        """Calls the whole account has made in the current window."""
        cutoff = time.time() - self.window
        conn = None
        try:
            conn = self._connect()
            row = conn.execute(
                "SELECT COUNT(*) FROM rate_ledger WHERE endpoint=? AND ts >= ?",
                (endpoint, cutoff),
            ).fetchone()
            return int(row[0]) if row else 0
        except sqlite3.Error:
            return 0
        finally:
            if conn is not None:
                try:
                    conn.close()
                except sqlite3.Error:
                    pass
