"""A standalone program's status file feeds the deck's boxes.

The operator's program already rewrites a JSON status file beside itself every
few seconds. The deck drew every box from the built-in engine's snapshot only,
so while the program traded the equity, day P&L, index strip, card position and
rate budget all read "—". The follower now turns that file into the same
snapshot, and the live feed sends the running program's snapshot.
"""

from __future__ import annotations

import asyncio
import json
import os

import pytest

from app.program_status import live_snapshot_key, program_snapshot, read_status
from shared.db import K_SNAPSHOT, snapshot_key

from tests.test_programs import fleet, register, wait_for  # noqa: F401  (fixture)

# What the operator's script writes (write_status), flat and in a position.
FLAT = {
    "ts": "2026-09-29T10:18:23", "pid": 4242, "version": "og-real",
    "state": "RUNNING", "mode": "LIVE", "phase": "SCANNING", "market_open": True,
    "equity": 51234.5, "cash": 50000.0, "day_pnl": 1234.5, "pnl_source": "angel",
    "peak_equity": 52000.0, "drawdown_pct": -1.47,
    "realised_today": 0.0, "realised_week": -3000.0,
    "trades_today": 0, "max_trades": 1, "consec_losses": 1,
    "halted": False, "week_halted": False, "locked_profit": False,
    "spot": 22582.2, "channel_high": 22806.0, "channel_low": 22573.0, "signal": "",
    "position": None,
    "guards": {"daily_kill": 12000.0, "weekly_kill": 25000.0, "dd_halt_pct": 45,
               "streak_halt": 4, "per_trade_risk": 9000.0, "capital_floor": 0.0},
    "api": {"calls": {"ltp": 30, "candle": 8, "rms": 3}, "waited_sec": 1.5,
            "throttles": 2, "rejected": 4, "per_min": 8},
}
IN_TRADE = {
    **FLAT, "phase": "IN POSITION", "signal": "P", "spot": 22540.0,
    "position": {"right": "PE", "strike": 22600, "lots": 2, "qty": 130, "entry": 150.0,
                 "peak_pct": 12.5, "live_premium": 165.0,
                 "exchange_orders": {"sl": {"oid": "1", "qty": 130, "trig": 82.5, "px": 82.45,
                                            "status": "trigger pending"}},
                 "exchange_target": None},
}

# Every field the dashboard's Snapshot type reads, by section.
SHAPE = {
    "engine": {"version", "mode", "phase", "pid", "uptime_sec", "banner", "build"},
    "market": {"open", "session_date", "entry_window"},
    "account": {"equity", "start_equity", "peak_equity", "week_start_equity", "day_pl",
                "day_pl_pct", "realised_today", "realised_week", "drawdown_pct", "currency"},
    "signal": {"spot", "channel_high", "channel_low", "lookback", "view", "state", "room_up",
               "room_down", "bar_close", "bar_age_sec", "bars_loaded", "divergence_pts",
               "divergence_limit", "next_strike"},
    "guards": {"halted", "week_halted", "locked_profit", "trades_today", "max_trades_day",
               "consec_losses", "consec_loss_halt", "daily_loss_used", "daily_loss_limit",
               "weekly_loss_used", "weekly_loss_limit", "profit_lock_progress",
               "profit_lock_target", "drawdown_stop", "min_capital", "capital_ok",
               "deploy_fraction", "per_trade_equity_cap", "per_trade_risk_rs", "max_lots"},
    "health": {"clock_drift_sec", "broker_connected", "broker_client_id", "api",
               "contracts_loaded", "telemetry_dropped", "last_error"},
}
POSITION = {"tsym", "side", "strike", "expiry", "view", "lots", "qty", "entry_premium",
            "live_premium", "gain_pct", "peak_pct", "unrealised", "notional", "stop_price",
            "stop_pct", "stop_state", "target_pts", "index_move_pts", "spot_entry",
            "opened_ts", "hold_min"}


def test_a_flat_status_fills_every_box_the_deck_draws():
    snap = program_snapshot(FLAT, name="og", mode="live", pid=4242, uptime_sec=90)
    for section, keys in SHAPE.items():
        assert keys <= set(snap[section]), f"{section} is missing {keys - set(snap[section])}"
    assert snap["engine"]["pid"] == 4242 and snap["engine"]["mode"] == "live"
    assert snap["engine"]["phase"] == "SCANNING"
    assert snap["account"]["equity"] == 51234.5
    assert snap["account"]["day_pl"] == 1234.5
    assert snap["account"]["start_equity"] == 50000.0
    sig = snap["signal"]
    assert sig["state"] == "inside"
    assert sig["room_up"] == pytest.approx(223.8) and sig["room_down"] == pytest.approx(9.2)
    assert snap["position"] is None
    assert snap["guards"]["consec_loss_halt"] == 4 and snap["guards"]["weekly_loss_used"] == 3000.0
    api = snap["health"]["api"]
    assert api["total_calls"] == 41 and api["rejected"] == 4
    assert {e["endpoint"] for e in api["endpoints"]} == {"ltp", "candle", "rms"}
    json.dumps(snap)  # it is stored and sent as JSON


def test_an_open_position_shows_on_the_card():
    snap = program_snapshot(IN_TRADE, name="og", mode="live", pid=4242)
    pos = snap["position"]
    assert POSITION <= set(pos)
    assert pos["side"] == "PE" and pos["strike"] == 22600 and pos["qty"] == 130
    assert pos["entry_premium"] == 150.0 and pos["live_premium"] == 165.0
    assert pos["unrealised"] == pytest.approx(1950.0)
    assert pos["gain_pct"] == pytest.approx(10.0)
    assert pos["stop_price"] == 82.5 and pos["stop_pct"] == pytest.approx(45.0)
    assert isinstance(pos["opened_ts"], str) and "T" in pos["opened_ts"]
    assert snap["signal"]["state"] == "break_down"


def test_junk_in_the_status_never_breaks_the_snapshot():
    junk = {"ts": "2026-09-29T10:00:00", "equity": "n/a", "spot": None, "phase": "WHATEVER",
            "position": {"right": "CE", "entry": 0, "live_premium": None}, "api": "nope"}
    snap = program_snapshot(junk, name="x", mode="paper", pid=None)
    assert snap["engine"]["phase"] == "SCANNING"
    assert snap["account"]["equity"] == 0.0
    assert snap["signal"]["state"] == "unknown"
    assert snap["position"]["unrealised"] is None and snap["position"]["gain_pct"] is None
    assert snap["health"]["api"] is None
    json.dumps(snap)


def test_a_status_file_from_an_earlier_run_is_ignored(tmp_path):
    (tmp_path / "gk50k_status.json").write_text(json.dumps({**FLAT, "pid": 1111}))
    assert read_status(tmp_path, pid=2222) is None
    assert read_status(tmp_path, pid=1111)["equity"] == 51234.5


def test_the_newest_status_file_wins(tmp_path):
    old, new = tmp_path / "gk50k_paper_status.json", tmp_path / "gk50k_status.json"
    old.write_text(json.dumps({**FLAT, "equity": 1.0}))
    new.write_text(json.dumps({**FLAT, "equity": 2.0}))
    os.utime(old, (1, 1))
    assert read_status(tmp_path, pid=None)["equity"] == 2.0


STATUS_PROGRAM = '''#!/usr/bin/env python3
import argparse, json, os, time
HERE = os.path.dirname(os.path.abspath(__file__))
STATUS = %r

def trade(dry_run=True):
    n = 0
    try:
        while True:
            n += 1
            s = dict(STATUS, pid=os.getpid(), ts="2026-09-29T10:18:%%02d" %% (n %% 60))
            tmp = os.path.join(HERE, "gk50k_status.json.tmp")
            open(tmp, "w").write(json.dumps(s))
            os.replace(tmp, os.path.join(HERE, "gk50k_status.json"))
            time.sleep(0.1)
    except KeyboardInterrupt:
        pass

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--paper", action="store_true")
    ap.add_argument("--live", action="store_true")
    a = ap.parse_args()
    trade(dry_run=not a.live)
''' % (FLAT,)


def test_a_running_program_feeds_the_desk(fleet, tmp_db):  # noqa: F811
    from app.hub import Hub
    from app.program_output import ProgramOutput

    register(tmp_db, "og", STATUS_PROGRAM, mode="live")
    assert fleet.start("og")["ok"] is True
    pid = fleet.get("og").state.pid
    follower = ProgramOutput(tmp_db, fleet)

    def published():
        follower.pump()
        return tmp_db.kv_get(snapshot_key("og"), None) is not None

    assert wait_for(published), "the program's status never reached the desk"
    snap = tmp_db.kv_get(snapshot_key("og"), None)
    assert snap["engine"]["pid"] == pid, "the card matches its snapshot by pid"
    assert snap["account"]["equity"] == 51234.5
    assert snap["engine"]["mode"] == "live"

    # With only the program running, the desk's live snapshot is the program's.
    assert live_snapshot_key(tmp_db, fleet) == snapshot_key("og")
    assert tmp_db.equity_curve(algo_id="og"), "the equity curve gets its first point"

    class Sock:
        def __init__(self):
            self.sent = []

        async def send_text(self, text):
            self.sent.append(json.loads(text))

    hub = Hub(tmp_db, status_provider=lambda: {}, snapshot_key=lambda: live_snapshot_key(tmp_db, fleet))
    ws = Sock()
    asyncio.run(hub.connect(ws))
    assert ws.sent[0]["data"]["snapshot"]["engine"]["pid"] == pid

    fleet.stop("og", force=True)
    assert live_snapshot_key(tmp_db, fleet) == K_SNAPSHOT, "nothing running: back to the built-in's"
