"""The live link heartbeats every quiet second.

The dashboard judges the link by the time since its last message. With no
engine running there are no snapshots and few events, and a 20-second
heartbeat made a healthy connection read as stale, then dead.
"""

from __future__ import annotations

import asyncio
import json

from app import hub as hub_mod


class FakeSocket:
    def __init__(self):
        self.sent: list[dict] = []

    async def send_text(self, text):
        self.sent.append(json.loads(text))


def test_a_quiet_link_gets_a_heartbeat_every_second(tmp_db, monkeypatch):
    monkeypatch.setattr(hub_mod, "POLL_SECONDS", 0.05)
    monkeypatch.setattr(hub_mod, "HEARTBEAT_SECONDS", 0.05)
    h = hub_mod.Hub(tmp_db, status_provider=lambda: {"engine": {"running": False}})
    ws = FakeSocket()

    async def scenario():
        await h.start()
        await h.connect(ws)
        await asyncio.sleep(0.6)
        await h.stop()

    asyncio.run(scenario())
    assert ws.sent[0]["type"] == "hello"
    assert ws.sent[0]["heartbeat"] == 0.05, "the hello announces the heartbeat"
    pings = [m for m in ws.sent if m["type"] == "ping"]
    assert len(pings) >= 5, f"only {len(pings)} heartbeats in 0.6s of silence"
    assert all(isinstance(p.get("ts"), float) for p in pings)


def test_the_production_heartbeat_is_one_second():
    assert hub_mod.HEARTBEAT_SECONDS <= 1.0
