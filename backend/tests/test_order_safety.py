"""Real-money order handling: an unconfirmed order is never doubled.

Every test here counts the orders that reached "Angel" and reads what Angel
holds afterwards. A market order that does not confirm inside the polling
window is not a failed order — it may be slow, or accepted just as the
connection dropped — and before this, the engine sent a fresh one on top: up
to three BUYs for one entry, up to five SELLs for one exit.
"""

from __future__ import annotations

from datetime import datetime

import pytest

import engine.config as C
from engine.state import State
from tests.test_engine_loop import T, _open, fresh_bars, make_engine


@pytest.fixture
def engine(tmp_path, monkeypatch):
    return make_engine(tmp_path, monkeypatch)

CE = "NIFTY15SEP2624950CE"


def _enter(engine):
    engine.try_entry(T, 25000.0, "C", 24990.0, 24900.0, fresh_bars())


def buys(engine):
    return [o for o in engine.br.orders if o[0] == "BUY"]


def sells(engine):
    return [o for o in engine.br.orders if o[0] == "SELL"]


# ── entry ────────────────────────────────────────────────────────────────────
def test_a_slow_entry_fill_is_not_bought_twice(engine):
    engine.br.script = ["slow"]
    _enter(engine)
    assert len(buys(engine)) == 1, "a slow fill was bought again on top"
    assert engine.pos is not None
    assert engine.br.held[CE] == engine.pos["qty"]


def test_an_entry_accepted_as_the_connection_dropped_is_not_bought_twice(engine):
    engine.br.script = ["dropped"]
    _enter(engine)
    assert len(buys(engine)) == 1
    assert engine.pos is not None and engine.br.held[CE] == engine.pos["qty"]


def test_a_rejected_entry_is_retried_and_holds_exactly_one_position(engine):
    engine.br.script = ["reject", "ok"]
    _enter(engine)
    assert len(buys(engine)) == 2
    assert engine.br.held[CE] == engine.pos["qty"]


def test_an_entry_rejected_every_time_opens_nothing(engine):
    engine.br.fills = False
    _enter(engine)
    assert engine.pos is None
    assert engine.br.held.get(CE, 0) == 0
    assert len(buys(engine)) == 1 + C.ORDER_RETRIES


def test_an_entry_is_not_retried_when_angel_cannot_be_read(engine):
    """Not knowing whether the first order filled, the only safe move is to
    send nothing more."""
    engine.br.script = ["slow"]
    engine.br.blind = True
    _enter(engine)
    assert len(buys(engine)) == 1


def test_a_partial_entry_is_managed_at_its_real_size(engine):
    lot = C.LOT_SIZE

    def partial(tsym, token, side, qty, product=C.PRODUCT_TYPE):
        engine.br.orders.append((side, tsym, qty))
        engine.br.held[tsym] = engine.br.held.get(tsym, 0) + lot   # one lot filled, rest cancelled
        engine.br.last_order_id = None
        return False, None, "pending"

    engine.br.place = partial
    _enter(engine)
    assert len(buys(engine)) == 1
    assert engine.pos["qty"] == lot, "the position must be what Angel holds, not what was asked"


# ── exit ─────────────────────────────────────────────────────────────────────
def test_a_slow_exit_fill_is_not_sold_twice(engine):
    _open(engine)
    engine.br.script = ["slow"]
    assert engine.exit_position("STOP", spot=24900.0) is True
    assert len(sells(engine)) == 1
    assert engine.br.held[CE] == 0, "sold into a short"


def test_an_exit_accepted_as_the_connection_dropped_is_not_sold_twice(engine):
    _open(engine)
    engine.br.script = ["dropped"]
    assert engine.exit_position("STOP", spot=24900.0) is True
    assert len(sells(engine)) == 1
    assert engine.br.held[CE] == 0


def test_a_rejected_exit_is_retried_until_flat(engine):
    _open(engine)
    engine.br.script = ["reject", "ok"]
    assert engine.exit_position("STOP", spot=24900.0) is True
    assert engine.br.held[CE] == 0
    assert len(sells(engine)) == 2


def test_an_exit_never_sells_more_than_is_held(engine):
    _open(engine)
    engine.br.held[CE] = C.LOT_SIZE          # Angel holds one lot; the record says two
    engine.br.script = ["reject", "ok"]
    engine.exit_position("STOP", spot=24900.0)
    assert sells(engine)[-1][2] == C.LOT_SIZE
    assert engine.br.held[CE] == 0


def test_an_exit_is_not_retried_blind_and_the_position_is_kept(engine):
    _open(engine)
    engine.br.script = ["reject"]
    engine.br.blind = True
    assert engine.exit_position("STOP", spot=24900.0) is False
    assert len(sells(engine)) == 1
    assert engine.pos is not None, "an exit that did not happen must leave the position managed"


def test_a_short_is_never_extended(engine):
    _open(engine)
    engine.br.held[CE] = -C.LOT_SIZE
    engine.br.script = ["reject"]
    assert engine.exit_position("STOP", spot=24900.0) is False
    assert len(sells(engine)) == 1


# ── startup ──────────────────────────────────────────────────────────────────
def test_startup_keeps_a_position_when_the_book_cannot_be_read(engine):
    """A failed read used to look like an empty book, and the engine deleted
    its own record — leaving a live position with no stop and no close."""
    _open(engine)
    engine.br.blind = True
    engine.reconcile_broker_position()
    assert engine.st.position.get("tsym") == CE


def test_startup_still_clears_a_position_angel_confirms_is_gone(engine):
    _open(engine)
    engine.br.held[CE] = 0
    engine.reconcile_broker_position()
    assert not engine.st.position


# ── day boundaries ───────────────────────────────────────────────────────────
def test_a_new_day_in_the_same_week_keeps_the_weekly_loss(engine):
    """The scheduler restarts the engine every morning, so the startup path is
    the normal day boundary. Resetting the week there made the weekly loss
    limit trip only if the whole limit was lost in a single day."""
    engine.st.week_id = "2026-W37"
    engine.st.realised_week = -18_000.0
    nxt = engine._carry_into(engine.st, datetime(2026, 9, 11, 9, 5), 480_000.0)
    assert nxt.realised_week == -18_000.0
    assert nxt.session_date == "2026-09-11"
    assert nxt.trades_today == 0


def test_a_new_week_starts_the_weekly_loss_fresh(engine):
    engine.st.week_id = "2026-W37"
    engine.st.realised_week = -18_000.0
    nxt = engine._carry_into(engine.st, datetime(2026, 9, 14, 9, 5), 480_000.0)
    assert nxt.realised_week == 0.0 and nxt.week_id == "2026-W38"


def test_a_position_left_open_overnight_is_carried_and_managed(engine):
    _open(engine)
    nxt = engine._carry_into(engine.st, datetime(2026, 9, 11, 9, 5), 480_000.0)
    assert nxt.position.get("tsym") == CE


def test_bootstrap_uses_the_carry(engine, monkeypatch):
    """The carry must actually be on the startup path, not just exist."""
    import inspect

    from engine import runner

    assert "_carry_into" in inspect.getsource(runner.Engine.bootstrap)


# ── sizing ───────────────────────────────────────────────────────────────────
def test_sizing_uses_the_exchange_lot_not_the_constant(engine):
    small = engine._size(140.0, 50)
    big = engine._size(140.0, 100)
    assert small >= big, "a larger lot must never size to more lots"
    assert engine._size(140.0, C.LOT_SIZE) == engine.strat.size_position(engine.equity, 140.0)


def test_state_file_round_trips_a_carried_position(engine):
    _open(engine)
    engine.st = engine._carry_into(engine.st, datetime(2026, 9, 11, 9, 5), 480_000.0)
    engine.st.save(engine.algo_id)
    assert State.load(engine.algo_id).position["tsym"] == CE


# ── the IP Angel will accept orders from ─────────────────────────────────────
def test_live_start_refuses_when_the_real_ip_is_not_the_declared_one(engine, monkeypatch):
    monkeypatch.setattr(C, "PUBLIC_IP", "223.228.51.98")
    monkeypatch.setattr("engine.broker.real_public_ip", lambda timeout=5.0: "140.238.1.2")
    with pytest.raises(RuntimeError, match="AG7002"):
        engine.check_order_ip()


def test_live_start_proceeds_when_the_ip_matches(engine, monkeypatch):
    monkeypatch.setattr(C, "PUBLIC_IP", "140.238.1.2")
    monkeypatch.setattr("engine.broker.real_public_ip", lambda timeout=5.0: "140.238.1.2")
    engine.check_order_ip()


def test_paper_never_checks_the_ip(engine, monkeypatch):
    engine.dry_run = True
    monkeypatch.setattr(C, "PUBLIC_IP", "1.1.1.1")
    monkeypatch.setattr("engine.broker.real_public_ip", lambda timeout=5.0: "2.2.2.2")
    engine.check_order_ip()


# ── what can be started ──────────────────────────────────────────────────────
STANDALONE = """#!/usr/bin/env python3
import argparse
def target_strike(spot, right): return 0
def effective_stop(g): return 0.45
def size_position(e, p): return 0
def trade(dry_run=True): pass
if __name__ == "__main__":
    ap = argparse.ArgumentParser()
"""


def test_a_standalone_program_is_named_as_such_without_being_run():
    from engine.contract import looks_standalone, missing_members

    assert missing_members(STANDALONE) == ["NAME", "signal()", "guards()"]
    assert looks_standalone(STANDALONE)


def test_the_builtin_satisfies_the_contract():
    from pathlib import Path

    from engine.contract import missing_members

    assert missing_members(Path("engine/builtin_gk50k.py").read_text()) == []


def test_starting_a_standalone_upload_is_refused_with_the_reason(tmp_db):
    from app.fleet import Fleet

    class Sup:
        def __init__(self, db, algo_id="x", mode_provider=None, strategy_path=None):
            self.state = type("S", (), {"running": False, "mode": "paper"})()
            self.strategy_path = strategy_path
            self.started = False

        def desired_mode(self):
            return "paper"

        def refresh(self):
            pass

        def start(self, trigger="manual"):
            self.started = True
            return {"ok": True}

    tmp_db.upsert_algo("og", "OG Real", kind="uploaded")
    vid = tmp_db.add_version("og", STANDALONE, "sha", "tester")
    tmp_db.set_algo_fields("og", active_version=vid)
    fleet = Fleet(tmp_db, supervisor_factory=Sup)
    res = fleet.start("og")
    assert res["ok"] is False
    assert "signal()" in res["detail"] and "standalone" in res["detail"]
    assert fleet.get("og").started is False


# ── one algorithm on real money per account ──────────────────────────────────
def test_a_second_live_algorithm_is_refused(tmp_db):
    from app.fleet import Fleet

    class Sup:
        def __init__(self, db, algo_id="x", mode_provider=None, strategy_path=None):
            self.algo_id = algo_id
            self.state = type("S", (), {"running": False, "mode": "live"})()
            self.strategy_path = strategy_path

        def desired_mode(self):
            return "live"

        def refresh(self):
            pass

        def start(self, trigger="manual"):
            self.state.running = True
            return {"ok": True}

    tmp_db.upsert_algo("second", "Second", kind="builtin")
    fleet = Fleet(tmp_db, supervisor_factory=Sup)
    assert fleet.start("gk50k")["ok"] is True
    res = fleet.start("second")
    assert res["ok"] is False
    assert "already trading real money" in res["detail"]


def test_paper_runs_alongside_a_live_algorithm(tmp_db):
    from app.fleet import Fleet

    modes = {"gk50k": "live", "paper-one": "paper"}

    class Sup:
        def __init__(self, db, algo_id="x", mode_provider=None, strategy_path=None):
            self.algo_id = algo_id
            self.state = type("S", (), {"running": False, "mode": modes.get(algo_id, "paper")})()
            self.strategy_path = strategy_path

        def desired_mode(self):
            return modes.get(self.algo_id, "paper")

        def refresh(self):
            pass

        def start(self, trigger="manual"):
            self.state.running = True
            return {"ok": True}

    tmp_db.upsert_algo("paper-one", "Paper one", kind="builtin")
    fleet = Fleet(tmp_db, supervisor_factory=Sup)
    assert fleet.start("gk50k")["ok"] is True
    assert fleet.start("paper-one")["ok"] is True
