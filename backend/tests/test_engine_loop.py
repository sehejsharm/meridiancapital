"""Engine execution paths driven against a stub broker.

These cover the code that actually spends money: sizing an entry, refusing one,
verifying the fill, and booking the exit with Angel's realised P&L rather than a
local guess.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pandas as pd
import pytest

from engine import config as C
from engine.runner import Engine
from engine.state import State


class FakeBroker:
    def __init__(self, premium=140.0, equity=500_000.0, realised=0.0, fills=True):
        self.dry_run = False
        self.premium = premium
        self._equity = equity
        self._realised = realised
        self.fills = fills
        self.orders: list[tuple] = []
        self.client_id = "TEST123"
        self.last_error = None
        self.rl = type("RL", (), {"stats": staticmethod(lambda: {"total_calls": 0})})()

    def funds(self, force=False):
        return self._equity

    def realised_pnl(self, force=False):
        return self._realised

    def ltp(self, exch, tsym, token):
        return self.premium

    def last_fill(self, tsym):
        return self.premium

    def place(self, tsym, token, side, qty, product=C.PRODUCT_TYPE):
        self.orders.append((side, tsym, qty))
        if not self.fills:
            return False, None, "rejected: insufficient margin"
        return True, self.premium, "complete"

    def option_table(self):
        return {}

    def open_positions(self):
        return []


@pytest.fixture
def engine(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "DB_PATH", tmp_path / "engine.db")
    monkeypatch.setattr(C, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(C, "TRADE_LOG", tmp_path / "trades.csv")
    monkeypatch.setattr("engine.state.STATE_FILE", tmp_path / "state.json")

    eng = Engine(mode="live")
    eng.br = FakeBroker()
    eng.equity = 500_000.0
    eng.st = State(session_date="2026-09-10", week_id="2026-W37", start_equity=500_000.0,
                   peak_equity=500_000.0)
    expiry = date(2026, 9, 15)
    eng.table = {
        (expiry, 24950, "CE"): ("NIFTY15SEP2624950CE", "111"),
        (expiry, 25050, "PE"): ("NIFTY15SEP2625050PE", "222"),
    }
    return eng


def fresh_bars(n=200, close=25000.0, age_sec=10):
    now = datetime(2026, 9, 10, 11, 0)
    return pd.DataFrame({
        "ts": [now - timedelta(minutes=n - i) for i in range(n)],
        "close": [close] * n,
    })


T = datetime(2026, 9, 10, 11, 0)


# ── entry ────────────────────────────────────────────────────────────────────
def test_breakout_opens_a_position(engine):
    engine.try_entry(T, 25000.0, "C", 24990.0, 24900.0, fresh_bars())
    assert engine.pos is not None
    assert engine.pos["right"] == "CE" and engine.pos["strike"] == 24950
    assert engine.br.orders == [("BUY", "NIFTY15SEP2624950CE", engine.pos["lots"] * C.LOT_SIZE)]
    assert engine.st.trades_today == 1


def test_put_breakout_buys_the_itm_put(engine):
    engine.try_entry(T, 25000.0, "P", 25100.0, 25010.0, fresh_bars())
    assert engine.pos["right"] == "PE" and engine.pos["strike"] == 25050


def test_entry_uses_the_real_fill_price_not_the_quote(engine):
    engine.br.premium = 140.0
    engine.try_entry(T, 25000.0, "C", 24990.0, 24900.0, fresh_bars())
    assert engine.pos["entry"] == 140.0


def test_stale_candle_feed_blocks_entry(engine):
    bars = fresh_bars()
    bars.loc[bars.index[-1], "ts"] = T - timedelta(seconds=C.MAX_BAR_AGE_SEC + 60)
    engine.try_entry(T, 25000.0, "C", 24990.0, 24900.0, bars)
    assert engine.pos is None and engine.br.orders == []


def test_short_candle_history_blocks_entry(engine):
    engine.try_entry(T, 25000.0, "C", 24990.0, 24900.0, fresh_bars(n=10))
    assert engine.pos is None and engine.br.orders == []


def test_feed_divergence_blocks_entry(engine):
    bars = fresh_bars(close=25000.0)
    spot = 25000.0 + C.MAX_FEED_DIVERGENCE_PTS + 5
    engine.try_entry(T, spot, "C", 24990.0, 24900.0, bars)
    assert engine.pos is None and engine.br.orders == []


def test_premium_above_the_cap_blocks_entry(engine):
    engine.br.premium = C.MAX_PREMIUM + 1
    engine.try_entry(T, 25000.0, "C", 24990.0, 24900.0, fresh_bars())
    assert engine.pos is None and engine.br.orders == []


def test_unsizeable_premium_blocks_entry(engine):
    # A resumed sub-floor account: one lot would exceed the 60% equity cap.
    engine.equity = 20_000.0
    engine.br.premium = 590.0
    engine.try_entry(T, 25000.0, "C", 24990.0, 24900.0, fresh_bars())
    assert engine.pos is None and engine.br.orders == []


def test_missing_contract_blocks_entry(engine):
    engine.table = {}
    engine.try_entry(T, 25000.0, "C", 24990.0, 24900.0, fresh_bars())
    assert engine.pos is None and engine.br.orders == []


def test_rejected_order_leaves_no_position_and_retries(engine):
    engine.br.fills = False
    engine.try_entry(T, 25000.0, "C", 24990.0, 24900.0, fresh_bars())
    assert engine.pos is None
    assert len(engine.br.orders) == 1 + C.ORDER_RETRIES


def test_position_survives_a_restart(engine):
    engine.try_entry(T, 25000.0, "C", 24990.0, 24900.0, fresh_bars())
    assert State.load().position["tsym"] == engine.pos["tsym"]


# ── exit ─────────────────────────────────────────────────────────────────────
def _open(engine, entry=140.0, peak=0.0):
    engine.pos = {
        "ts": T.isoformat(), "view": "C", "right": "CE", "strike": 24950,
        "expiry": "2026-09-15", "tsym": "NIFTY15SEP2624950CE", "token": "111",
        "entry": entry, "spot": 25000.0, "lots": 2, "peak": peak,
    }
    engine.st.position = engine.pos


def test_exit_books_angel_realised_pnl(engine):
    _open(engine)
    engine.st.realised_today = 0.0
    engine.br._realised = 4_200.0
    assert engine.exit_position("TARGET", spot=25090.0, live_prem=190.0)
    trade = engine.db.trades()[0]
    assert trade["net"] == 4_200.0 and trade["pnl_source"] == "angel"
    assert trade["reason"] == "TARGET"
    assert engine.pos is None


def test_exit_falls_back_to_an_estimate_when_angel_is_silent(engine):
    _open(engine)
    engine.br.realised_pnl = lambda force=False: None
    engine.br.premium = 190.0
    assert engine.exit_position("STOP", spot=24900.0)
    trade = engine.db.trades()[0]
    assert trade["pnl_source"] == "estimate"
    assert trade["net"] == pytest.approx(
        (190.0 - 140.0) * 2 * C.LOT_SIZE - trade["charges"], abs=1
    )


def test_failed_exit_keeps_the_position_open(engine):
    _open(engine)
    engine.br.fills = False
    assert engine.exit_position("STOP", spot=24900.0) is False
    assert engine.pos is not None
    assert State.load().position["tsym"] == "NIFTY15SEP2624950CE"


def test_losing_exit_increments_the_loss_streak(engine):
    _open(engine)
    engine.br._realised = -3_000.0
    engine.exit_position("STOP", spot=24900.0, live_prem=80.0)
    assert engine.st.consec_losses == 1


def test_winning_exit_clears_the_loss_streak(engine):
    engine.st.consec_losses = 2
    _open(engine)
    engine.br._realised = 1_000.0
    engine.exit_position("TARGET", spot=25090.0, live_prem=190.0)
    assert engine.st.consec_losses == 0


def test_loss_streak_halts_the_day(engine):
    engine.st.consec_losses = C.CONSEC_LOSS_HALT - 1
    _open(engine)
    engine.br._realised = -1_000.0
    engine.exit_position("STOP", spot=24900.0, live_prem=80.0)
    assert engine.st.halted is True


def test_flatten_halts_further_entries(engine):
    _open(engine)
    engine.br._realised = 500.0
    engine.handle_command("flatten", {})
    assert engine.pos is None and engine.st.halted is True


def test_exit_accumulates_the_weekly_realised_total(engine):
    engine.st.realised_week = -1_000.0
    _open(engine)
    engine.br._realised = -2_000.0
    engine.exit_position("STOP", spot=24900.0, live_prem=80.0)
    assert engine.st.realised_week == -3_000.0


# ── remote control ───────────────────────────────────────────────────────────
def test_halt_and_resume_commands(engine):
    assert "halted" in engine.handle_command("halt", {})
    assert engine.st.halted is True
    engine.handle_command("resume", {})
    assert engine.st.halted is False


def test_resume_can_clear_the_weekly_kill(engine):
    engine.st.week_halted = True
    engine.handle_command("resume", {"week": True})
    assert engine.st.week_halted is False


def test_resume_leaves_the_weekly_kill_alone_by_default(engine):
    engine.st.week_halted = True
    engine.handle_command("resume", {})
    assert engine.st.week_halted is True


def test_stop_is_refused_while_a_position_is_open(engine):
    _open(engine)
    assert "refused" in engine.handle_command("stop", {})
    assert engine.stop_requested is False


def test_forced_stop_is_accepted_with_a_position_open(engine):
    _open(engine)
    engine.handle_command("stop", {"force": True})
    assert engine.stop_requested is True


def test_stop_is_accepted_when_flat(engine):
    engine.handle_command("stop", {})
    assert engine.stop_requested is True


def test_unknown_command_is_reported_not_raised(engine):
    assert "unknown action" in engine.handle_command("self-destruct", {})


def test_commands_are_drained_from_the_queue(engine):
    engine.db.enqueue_command("halt")
    engine.process_commands()
    assert engine.st.halted is True
    assert engine.db.recent_commands()[0]["status"] == "done"


# ── snapshot contract ────────────────────────────────────────────────────────
def test_snapshot_has_every_section_the_dashboard_reads(engine):
    snap = engine.build_snapshot(T, 25000.0, "", 25100.0, 24900.0, fresh_bars(), None, "SCANNING")
    assert set(snap) >= {"ts", "engine", "market", "account", "signal", "position", "guards", "health"}
    assert snap["position"] is None
    assert snap["signal"]["state"] == "inside"


def test_snapshot_reports_a_live_position_with_its_stop(engine):
    _open(engine, entry=100.0, peak=0.50)
    snap = engine.build_snapshot(T, 25050.0, "", 25100.0, 24900.0, fresh_bars(), 150.0, "IN POSITION")
    pos = snap["position"]
    assert pos["gain_pct"] == pytest.approx(0.50)
    assert pos["stop_price"] == pytest.approx(115.0)  # locked +15% after the trail kicks in
    assert pos["stop_state"].startswith("LOCKED")
    assert pos["unrealised"] == pytest.approx(50.0 * 2 * C.LOT_SIZE)  # 50/unit on 2 lots


def test_snapshot_flags_a_break_above_the_channel(engine):
    snap = engine.build_snapshot(T, 25200.0, "C", 25100.0, 24900.0, fresh_bars(), None, "SCANNING")
    assert snap["signal"]["state"] == "break_up"


def test_snapshot_reports_guard_headroom(engine):
    engine.st.realised_today = -5_000.0
    snap = engine.build_snapshot(T, 25000.0, "", 25100.0, 24900.0, fresh_bars(), None, "SCANNING")
    assert snap["guards"]["daily_loss_used"] == -5_000.0
    assert snap["guards"]["daily_loss_limit"] == C.DAILY_LOSS_LIMIT_RS


def test_end_of_day_report_summarises_the_session(engine):
    _open(engine)
    engine.br._realised = 5_000.0
    engine.exit_position("TARGET", spot=25090.0, live_prem=190.0)
    engine.emit_eod_report()
    report = engine.db.kv_get("report:eod:latest")
    assert report["trades"] == 1 and report["wins"] == 1 and report["net"] == 5_000.0
