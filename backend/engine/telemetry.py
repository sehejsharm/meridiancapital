"""Structured event + snapshot publishing from the engine to the dashboard.

Everything the old console dashboard printed is emitted here instead: events go
to the database and to stdout (journald keeps the plain-text trail), snapshots
overwrite a single row that the API watches for revision changes.
"""

from __future__ import annotations

import sys
import time

from engine.clock import now_ist
from shared.db import K_SNAPSHOT, Database

LEVELS = ("debug", "info", "ok", "warn", "error", "critical")


class Telemetry:
    def __init__(self, db: Database, source: str = "engine"):
        self.db = db
        self.source = source
        self._last_snapshot = 0.0
        self.dropped = 0

    def log(self, message: str, level: str = "info", extra: dict | None = None) -> None:
        if level not in LEVELS:
            level = "info"
        line = f"[{now_ist():%Y-%m-%d %H:%M:%S}] {level.upper():<8} {message}"
        print(line, flush=True, file=sys.stderr if level in ("error", "critical") else sys.stdout)
        try:
            self.db.add_event(level, message, extra, self.source)
        except Exception:
            # A logging failure must never take the trading loop down.
            self.dropped += 1

    def snapshot(self, payload: dict, force: bool = False, min_interval: float = 0.0) -> None:
        if not force and min_interval and time.time() - self._last_snapshot < min_interval:
            return
        self._last_snapshot = time.time()
        try:
            self.db.kv_set(K_SNAPSHOT, payload)
        except Exception:
            self.dropped += 1

    def as_broker_logger(self):
        """Adapter matching Broker's ``log(msg, level, extra=None)`` signature."""

        def _log(message: str, level: str = "info", extra: dict | None = None) -> None:
            self.log(message, level, extra)

        return _log
