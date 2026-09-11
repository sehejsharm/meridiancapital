"""WebSocket fan-out.

One poller watches the database for a new engine snapshot or new events and
pushes to every connected dashboard. Clients never poll the database directly,
so a roomful of open browser tabs costs one reader, not N.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Any

from fastapi import WebSocket

from shared.db import K_SNAPSHOT, Database

POLL_SECONDS = 1.0
HEARTBEAT_SECONDS = 20.0


class Hub:
    def __init__(self, db: Database, status_provider):
        self.db = db
        self.status_provider = status_provider
        self.clients: set[WebSocket] = set()
        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None
        self._last_rev = -1
        self._last_event_id = 0
        self._last_status: str = ""

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
        await self._send(ws, {"type": "hello", "data": await self._full_state()})

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            self.clients.discard(ws)

    async def _full_state(self) -> dict:
        snapshot = await asyncio.to_thread(self.db.kv_get, K_SNAPSHOT, None)
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
                    rev = await asyncio.to_thread(self.db.kv_rev, K_SNAPSHOT)
                    if rev != self._last_rev:
                        self._last_rev = rev
                        snap = await asyncio.to_thread(self.db.kv_get, K_SNAPSHOT, None)
                        if snap:
                            await self.broadcast({"type": "snapshot", "data": snap})

                    new_events = await asyncio.to_thread(
                        self.db.events, 100, self._last_event_id
                    )
                    if new_events:
                        self._last_event_id = max(e["id"] for e in new_events)
                        await self.broadcast({"type": "events", "data": list(reversed(new_events))})

                    status = await asyncio.to_thread(self.status_provider)
                    encoded = json.dumps(status, default=str, sort_keys=True)
                    if encoded != self._last_status:
                        self._last_status = encoded
                        await self.broadcast({"type": "status", "data": status})

                    idle += POLL_SECONDS
                    if idle >= HEARTBEAT_SECONDS:
                        idle = 0.0
                        await self.broadcast({"type": "ping"})
            except asyncio.CancelledError:
                raise
            except Exception:
                pass
            await asyncio.sleep(POLL_SECONDS)
