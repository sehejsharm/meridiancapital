"""While a standalone program trades, the option panel shows the program's own
contract (with Greeks solved from its premium) or what a breakout would buy —
never an empty box, and never at the cost of an Angel request."""

from __future__ import annotations

from datetime import date, datetime

import pytest

from app.market import next_expiry, program_contract
from shared.nse_holidays import NSE_HOLIDAYS

HOLIDAYS = set(NSE_HOLIDAYS)
HELD = {
    "ts": "2026-10-06T13:38:00",
    "signal": {"spot": 22655.0, "channel_high": 22700.0, "channel_low": 22560.0},
    "position": {"tsym": "NIFTY13OCT2622600CE", "side": "CE", "strike": 22600, "expiry": "2026-10-13",
                 "lots": 1, "qty": 65, "entry_premium": 227.0, "live_premium": 240.0, "gain_pct": 5.7,
                 "unrealised": 845.0, "stop_price": 124.85, "target_pts": 90, "spot_entry": 22640.0},
}


@pytest.mark.parametrize("today,expiry", [
    ("2026-10-05", "2026-10-13"),   # Monday: tomorrow's expiry is 1 day out, too close
    ("2026-10-06", "2026-10-13"),   # expiry day itself: next week's
    ("2026-10-11", "2026-10-13"),   # Sunday: two days out is allowed
    ("2026-10-15", "2026-10-19"),   # Tue 20 Oct is Dussehra: expiry moves back to Monday
])
def test_the_expiry_the_strategy_would_pick(today, expiry):
    assert next_expiry(date.fromisoformat(today), HOLIDAYS).isoformat() == expiry


def test_a_held_contract_gets_its_greeks_from_the_programs_own_prices():
    out = program_contract(HELD, HOLIDAYS, datetime(2026, 10, 6, 13, 38))
    h = out["held"]
    assert h["tsym"] == "NIFTY13OCT2622600CE" and h["dte"] == 7
    assert h["target_level"] == 22730.0, "a call's target is 90 points above its entry"
    g = h["greeks"]
    assert 0.5 < g["delta"] < 0.65, "one strike in the money"
    assert g["theta"] < 0 and g["theta_position"] == pytest.approx(g["theta"] * 65, abs=1)
    assert 5 < g["iv"] < 40
    assert out["next"] is None


def test_a_put_target_is_below_its_entry():
    snap = {**HELD, "position": {**HELD["position"], "side": "PE", "strike": 22700}}
    assert program_contract(snap, HOLIDAYS, datetime(2026, 10, 6, 13, 38))["held"]["target_level"] == 22550.0


def test_while_flat_it_says_what_a_breakout_would_buy():
    flat = {k: v for k, v in HELD.items() if k != "position"}
    nxt = program_contract(flat, HOLIDAYS, datetime(2026, 10, 6, 11, 0))["next"]
    assert nxt["call"] == {"strike": 22600, "trigger": 22700.0}   # spot 22,655 -> ATM 22,650, call one below
    assert nxt["put"] == {"strike": 22700, "trigger": 22560.0}
    assert nxt["expiry"] == "2026-10-13"


def test_junk_never_breaks_the_panel():
    assert program_contract({}, HOLIDAYS, datetime(2026, 10, 6, 11, 0)) is None
    out = program_contract({"signal": {}, "position": {"side": "CE", "expiry": "soon"}}, HOLIDAYS,
                           datetime(2026, 10, 6, 11, 0))
    assert out["held"]["greeks"] is None and out["held"]["expiry"] is None
