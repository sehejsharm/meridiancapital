"""WebSocket fan-out.

One poller watches the database for a new engine snapshot or new events and
pushes to every connected dashboard. Clients never poll the database directly,
so a roomful of open browser tabs costs one reader, not N.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from typing import Any

from fastapi import WebSocket

from shared.db import K_SNAPSHOT, Database

POLL_SECONDS = 1.0
# The status carries the server's clock and each process's uptime, so its text
# differed every second and it was pushed every second: 1.6 KB a second to every
# open dashboard, each one replacing the status at the top of the page and
# re-rendering all of it. It is pushed when something other than those clocks
# changes, and otherwise this often, so the clocks in it never go stale.
STATUS_REFRESH_SECONDS = 15.0
_VOLATILE = frozenset({"now_ist", "uptime_sec", "restart_backoff_remaining"})


def stable_status(value: Any) -> Any:
    """The status without its clock-like fields: what "did anything change?" is asked of."""
    if isinstance(value, dict):
        return {k: stable_status(v) for k, v in value.items() if k not in _VOLATILE}
    if isinstance(value, list):
        return [stable_status(v) for v in value]
    return value
# Every quiet second carries a heartbeat. The dashboard judges the link by the
# time since its last message, so with nothing running (no snapshots, no
# events) a 20-second heartbeat made a healthy connection read as stale after
# 1.5s and dead after 12s. A ping is a few dozen bytes.
HEARTBEAT_SECONDS = 1.0


class Hub:
    def __init__(self, db: Database, status_provider, snapshot_key=None):
        self.db = db
        self.status_provider = status_provider
        # Which kv key holds the snapshot to show: the built-in's by default,
        # a running program's when only a program is trading.
        self.snapshot_key = snapshot_key or (lambda: K_SNAPSHOT)
        self.clients: set[WebSocket] = set()
        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None
        self._last_rev: tuple = ("", -1)
        self._last_event_id = 0
        self._last_status: str = ""
        self._status_sent_at = 0.0

    async def start(self) -> None:
        self._last_event_id = max((e["id"] for e in self.db.events(limit=1)), default=0)
        self._task = asyncio.create_task(self._poll(), name="meridian-hub")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    async def connect(self, ws: WebSocket) -> None:
        async with self._lock:
            self.clients.add(ws)
        # The heartbeat is announced so the dashboard can tell a silent, dead
        # socket (a phone back from the background) from a quiet one.
        await self._send(ws, {"type": "hello", "heartbeat": HEARTBEAT_SECONDS,
                              "data": await self._full_state()})

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            self.clients.discard(ws)

    async def _full_state(self) -> dict:
        key = await asyncio.to_thread(self.snapshot_key)
        snapshot = await asyncio.to_thread(self.db.kv_get, key, None)
        status = await asyncio.to_thread(self.status_provider)
        events = await asyncio.to_thread(self.db.events, 60)
        return {"snapshot": snapshot, "status": status, "events": list(reversed(events))}

    async def broadcast(self, message: dict[str, Any]) -> None:
        async with self._lock:
            targets = list(self.clients)
        dead = []
        for ws in targets:
            try:
                await self._send(ws, message)
            except Exception:
                dead.append(ws)
        if dead:
            async with self._lock:
                for ws in dead:
                    self.clients.discard(ws)

    @staticmethod
    async def _send(ws: WebSocket, message: dict) -> None:
        await ws.send_text(json.dumps(message, default=str))

    async def _poll(self) -> None:
        idle = 0.0
        while True:
            try:
                if self.clients:
                    sent = False
                    key = await asyncio.to_thread(self.snapshot_key)
                    rev = (key, await asyncio.to_thread(self.db.kv_rev, key))
                    if rev != self._last_rev:
                        self._last_rev = rev
                        snap = await asyncio.to_thread(self.db.kv_get, key, None)
                        if snap:
                            await self.broadcast({"type": "snapshot", "data": snap})
                            sent = True

                    new_events = await asyncio.to_thread(
                        self.db.events, 100, self._last_event_id
                    )
                    if new_events:
                        self._last_event_id = max(e["id"] for e in new_events)
                        await self.broadcast({"type": "events", "data": list(reversed(new_events))})
                        sent = True

                    status = await asyncio.to_thread(self.status_provider)
                    encoded = json.dumps(stable_status(status), default=str, sort_keys=True)
                    due = time.monotonic() - self._status_sent_at >= STATUS_REFRESH_SECONDS
                    if encoded != self._last_status or due:
                        self._last_status = encoded
                        self._status_sent_at = time.monotonic()
                        await self.broadcast({"type": "status", "data": status})
                        sent = True

                    idle = 0.0 if sent else idle + POLL_SECONDS
                    if idle >= HEARTBEAT_SECONDS:
                        idle = 0.0
                        await self.broadcast({"type": "ping", "ts": time.time()})
            except asyncio.CancelledError:
                raise
            except Exception:
                pass
            await asyncio.sleep(POLL_SECONDS)
