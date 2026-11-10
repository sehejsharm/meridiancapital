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


# ── the status is pushed when it changes, not when its clock ticks ───────────
def _run_hub(tmp_db, monkeypatch, provider, seconds=0.6, refresh=60.0, during=None):
    monkeypatch.setattr(hub_mod, "POLL_SECONDS", 0.05)
    monkeypatch.setattr(hub_mod, "HEARTBEAT_SECONDS", 0.05)
    monkeypatch.setattr(hub_mod, "STATUS_REFRESH_SECONDS", refresh)
    h = hub_mod.Hub(tmp_db, status_provider=provider)
    ws = FakeSocket()

    async def scenario():
        await h.start()
        await h.connect(ws)
        if during:
            await asyncio.sleep(seconds / 2)
            during()
            await asyncio.sleep(seconds / 2)
        else:
            await asyncio.sleep(seconds)
        await h.stop()

    asyncio.run(scenario())
    return ws.sent


def test_a_ticking_clock_alone_does_not_push_the_status(tmp_db, monkeypatch):
    """It used to: 'now_ist' and every uptime differ each second, so the whole status
    went to every dashboard every second and re-rendered every page."""
    n = {"i": 0}

    def provider():
        n["i"] += 1
        return {"engine": {"running": True, "uptime_sec": n["i"]}, "schedule": {"now_ist": f"t{n['i']}", "enabled": True},
                "fleet": {"algos": [{"algo_id": "og", "running": True, "uptime_sec": n["i"]}]}}

    sent = _run_hub(tmp_db, monkeypatch, provider)
    statuses = [m for m in sent if m["type"] == "status"]
    assert len(statuses) == 1, f"{len(statuses)} status pushes for a status that only aged"
    assert len([m for m in sent if m["type"] == "ping"]) >= 5, "the link still heartbeats"


def test_a_real_change_is_pushed_at_once(tmp_db, monkeypatch):
    state = {"running": False}
    sent = _run_hub(tmp_db, monkeypatch, lambda: {"engine": {"running": state["running"], "uptime_sec": 1}},
                    during=lambda: state.update(running=True))
    statuses = [m["data"]["engine"]["running"] for m in sent if m["type"] == "status"]
    assert statuses == [False, True]


def test_the_status_is_refreshed_periodically_so_its_clock_stays_current(tmp_db, monkeypatch):
    sent = _run_hub(tmp_db, monkeypatch, lambda: {"schedule": {"now_ist": "same"}}, seconds=0.7, refresh=0.2)
    assert 3 <= len([m for m in sent if m["type"] == "status"]) <= 5


def test_stable_status_drops_only_the_clocks():
    s = {"engine": {"running": True, "uptime_sec": 5, "pid": 1}, "schedule": {"now_ist": "x", "last_tick": "y"},
         "fleet": {"algos": [{"uptime_sec": 9, "mode": "live", "restart_backoff_remaining": 3}]}}
    assert hub_mod.stable_status(s) == {"engine": {"running": True, "pid": 1}, "schedule": {"last_tick": "y"},
                                        "fleet": {"algos": [{"mode": "live"}]}}
