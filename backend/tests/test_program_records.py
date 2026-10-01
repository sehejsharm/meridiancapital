"""What a standalone program leaves behind reaches the desk, and only that.

* Its once-a-minute status board stays out of the journal: logged line by line
  it was 1,900 of a day's 2,000 rows, and its "N REJECTED by Angel" counter
  was filed as an error every minute.
* Its own trade log reaches the blotter and reports, once per trade.
* With nothing running, the desk shows the last snapshot recorded, every card
  keeps its last figures, and the equity chart shows the last session.
"""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pytest

from app.program_output import ProgramOutput
from app.program_status import compact_snapshot, live_snapshot_key
from app.program_trades import TradeLogs, read_trade_log, tsym_for
from engine.clock import now_ist
from shared.db import K_SNAPSHOT, snapshot_key

# One board exactly as the operator's script prints it (colour codes removed).
BOARD = """
════════════════════════════════════════════════════════════════
  गणेश कवच  ·  GANESH KAVACH 50K  ·  Config #5 v3
  2026-09-30 14:42:32 IST      IN POSITION
════════════════════════════════════════════════════════════════
  Equity                Rs 43,006.57  (start cash + Angel day P&L)
  Day P&L               Rs -5,700.50  (Angel position book, before charges)
  Angel API             8,262 calls | 6/40 per min | 2358 self-throttled | 121 REJECTED by Angel
  ────────────────────────────────────────────────────────────
  NIFTY spot            22,610.4
  POSITION              CE 22700 (ITM) x1 lot = 65 qty
════════════════════════════════════════════════════════════════
""".strip("\n").split("\n")

REAL = [
    "[14:42:59] exchange STOP filled 65 of 65 on NIFTY06OCT2622700CE @ 111.45 — cancelling the other exit order first",
    "[14:43:05] EXIT SL@EXCH  CE 22700 x65  202.70->111.45  P&L Rs -5931.250",
]

IP_WARNING = [
    "================================================================",
    " IP MISMATCH — orders will be rejected by Angel (AG7002)",
    "================================================================",
    "   code says PUBLIC_IP = 130.210.12.190",
    "   real IP right now   = 137.23.55.120",
]


def run(lines):
    out = ProgramOutput(db=None, fleet=None)
    kept = []
    for line in lines:
        if line.strip():
            kept.extend(out._filter("og", line.strip()))
    return kept


def test_the_status_board_stays_out_of_the_journal():
    assert run(BOARD + REAL + BOARD + REAL) == REAL + REAL


def test_reading_that_starts_inside_a_board_rejoins_it():
    # After an API restart the follower can start part way through a board.
    mid_header = BOARD[2:]           # from the timestamp line on
    mid_body = BOARD[5:]             # from the body on: its lines leak once, then it resyncs
    assert run(mid_header + REAL + BOARD + REAL) == REAL + REAL
    leaked = run(mid_body + REAL + BOARD + REAL)
    assert leaked[-len(REAL):] == REAL and all(r in leaked for r in REAL)
    assert "Equity" not in " ".join(leaked[len(BOARD[5:]):]), "back in step after one board"


def test_other_framed_messages_are_never_swallowed():
    # The script's IP warning is framed with "=", not the board's "═".
    assert run(IP_WARNING + REAL) == [line.strip() for line in IP_WARNING] + REAL


def test_a_border_alone_does_not_hide_what_follows():
    lone = ["═" * 64] + REAL + ["more", "and more"]
    assert run(lone) == REAL + ["more", "and more"]


# ── the program's own trade log ──────────────────────────────────────────────
HEADER = ("entry_ts,exit_ts,view,right,strike,expiry,lots,qty,entry_prem,exit_prem,spot_entry,"
          "spot_exit,peak_pct,gross,charges,net,reason,hold_min,equity")
ROWS = [
    "2026-09-29T11:12,2026-09-29 15:10,P,PE,22650,2026-10-06,1,65,180.0,170.65,22640.2,22612.0,"
    "8.0,-607.75,61.2,-668.95,EOD,238.0,48269.21",
    "2026-09-30T10:22,2026-09-30 14:43,C,CE,22700,2026-10-06,1,65,202.7,111.45,22655.0,22560.4,"
    "1.5,-5931.25,67.0468,-5998.2968,SL@EXCH,261.0,42315.32",
]


def write_log(folder: Path, name: str, rows) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    p = folder / name
    p.write_text(HEADER + "\n" + "\n".join(rows) + "\n", encoding="utf-8")
    return p


def test_the_trade_log_reads_into_the_blotters_shape(tmp_path):
    trades = read_trade_log(write_log(tmp_path, "gk50k_trades.csv", ROWS))
    assert len(trades) == 2
    t = trades[1]
    assert t["session_date"] == "2026-09-30" and t["mode"] == "live"
    assert t["side"] == "CE" and t["strike"] == 22700 and t["qty"] == 65
    assert t["tsym"] == "NIFTY06OCT2622700CE"
    assert t["net"] == pytest.approx(-5998.2968) and t["reason"] == "SL@EXCH"
    paper = read_trade_log(write_log(tmp_path, "gk50k_paper_trades.csv", ROWS[:1]))
    assert paper[0]["mode"] == "paper"


def test_each_trade_is_recorded_once(tmp_db, tmp_path):
    logs = TradeLogs(tmp_db)
    path = write_log(tmp_path, "gk50k_trades.csv", ROWS[:1])
    assert logs.ingest("og", tmp_path) == 1
    assert logs.ingest("og", tmp_path) == 0, "an unchanged file is not re-read"
    path.write_text(HEADER + "\n" + "\n".join(ROWS) + "\n", encoding="utf-8")
    assert logs.ingest("og", tmp_path) == 1, "only the new trade is added"
    assert TradeLogs(tmp_db).ingest("og", tmp_path) == 0, "a fresh follower adds nothing twice"
    rows = tmp_db.trades(algo_id="og")
    assert [r["reason"] for r in rows] == ["SL@EXCH", "EOD"]
    assert sum(r["net"] for r in rows) == pytest.approx(-6667.2468)


def test_junk_in_the_trade_log_is_skipped(tmp_path):
    p = write_log(tmp_path, "gk50k_trades.csv", ["", "garbage,row", ROWS[0]])
    assert len(read_trade_log(p)) == 1
    assert tsym_for(None, "CE", "2026-10-06") is None
    assert tsym_for(22700, "CE", "not a date") == "NIFTY 22700 CE"


# ── with nothing running ─────────────────────────────────────────────────────
class _Sup:
    def __init__(self, running):
        self.state = type("S", (), {"running": running})()


class _Fleet:
    def __init__(self, sups):
        self._s = sups

    def all(self):
        return self._s


def test_with_nothing_running_the_desk_shows_the_last_recorded_snapshot(tmp_db):
    tmp_db.upsert_algo("og", "og", kind="uploaded")
    tmp_db.kv_set(K_SNAPSHOT, {"ts": "2026-08-01T15:20:00", "engine": {"pid": 1}})
    tmp_db.kv_set(snapshot_key("og"), {"ts": "2026-09-30T15:24:30", "engine": {"pid": 2}})
    stopped = _Fleet({"gk50k": _Sup(False), "og": _Sup(False)})
    assert live_snapshot_key(tmp_db, stopped) == snapshot_key("og")
    # The built-in, once running, still takes the desk.
    assert live_snapshot_key(tmp_db, _Fleet({"gk50k": _Sup(True), "og": _Sup(False)})) == K_SNAPSHOT


def test_a_card_keeps_its_last_figures():
    last = compact_snapshot({
        "ts": "2026-09-30T15:24:30", "engine": {"pid": 9, "mode": "live", "phase": "MARKET CLOSED"},
        "account": {"equity": 42315.32, "day_pl": -5931.25, "day_pl_pct": -12.3, "peak_equity": 51099.16},
        "position": None,
    })
    assert last["account"]["equity"] == 42315.32 and last["position"] is None
    assert last["ts"] == "2026-09-30T15:24:30" and last["mode"] == "live"
    assert compact_snapshot(None) is None and compact_snapshot({"engine": {}}) is None
    json.dumps(last)


def test_the_equity_chart_falls_back_to_the_last_session(tmp_db, monkeypatch):
    from app.routers import data

    yesterday = (now_ist() - timedelta(days=1)).strftime("%Y-%m-%d")
    tmp_db.add_equity_sample(yesterday, 42315.32, -5931.25, 51099.16, -5931.25, algo_id="og")
    assert tmp_db.latest_equity_session() == yesterday

    class Ctx:
        db = tmp_db

    monkeypatch.setattr(data, "ctx", lambda: Ctx)
    import asyncio

    out = asyncio.run(data.equity(session_date=None, limit=100))
    assert out["recorded"] is True and out["session_date"] == yesterday
    assert out["intraday"][0]["equity"] == 42315.32
    today = asyncio.run(data.equity(session_date=now_ist().strftime("%Y-%m-%d"), limit=100))
    assert today["intraday"] == [] and today["recorded"] is False
