"""The original build's self-test suite, as pytest.

Every assertion here was carried over from the single-file build's ``selftest()``.
They pin the backtested behaviour: if one of these fails, the deployed strategy
is no longer the strategy that was validated.
"""

from __future__ import annotations

from datetime import date, timedelta, datetime
from pathlib import Path

import pandas as pd
import pytest

from engine.config import (
    BE_TRIGGER,
    CONSEC_LOSS_HALT,
    DAILY_LOSS_LIMIT_RS,
    DAILY_PROFIT_LOCK_RS,
    DEPLOY_FRACTION,
    DONCHIAN_LB,
    ENTRY_CUTOFF,
    ENTRY_START,
    FORCE_CLOSE,
    LOT_SIZE,
    MAX_DRAWDOWN_STOP,
    MAX_LOTS,
    MAX_TRADES_DAY,
    MAX_DTE,
    MIN_CAPITAL,
    PER_TRADE_EQUITY_CAP,
    PER_TRADE_RISK_RS,
    STOP_FRAC,
    STRIKE_OFFSET,
    STRIKE_STEP,
    TRAIL_FRAC,
    WEEKLY_LOSS_LIMIT_RS,
)
from engine.clock import ge, lt
from engine.strategy import (
    donchian,
    effective_stop,
    pick_contract,
    round_trip_charges,
    size_position,
    target_strike,
)

WORST_TRADE_RS = 9_700  # the backtest's worst single trade, the floor every kill switch clears


# ── signal ───────────────────────────────────────────────────────────────────
def test_breakout_up_gives_call():
    assert donchian(pd.Series(list(range(100)) + [500.0]))[0] == "C"


def test_breakout_down_gives_put():
    assert donchian(pd.Series(list(range(100)) + [-500.0]))[0] == "P"


def test_flat_series_gives_no_signal():
    assert donchian(pd.Series([100.0] * 120))[0] == ""


def test_short_series_is_safe():
    assert donchian(pd.Series([1.0] * 40))[0] == ""


def test_none_series_is_safe():
    assert donchian(None)[0] == ""


def test_channel_excludes_current_bar():
    _, hi, lo = donchian(pd.Series(list(range(200))))
    assert hi == 198 and lo == 109


def test_equal_high_is_not_a_break():
    assert donchian(pd.Series([5.0] * 101))[0] == ""


# ── strike selection ─────────────────────────────────────────────────────────
def test_call_strike_is_itm_below_spot():
    assert target_strike(25010, "CE") == 24950


def test_put_strike_is_itm_above_spot():
    assert target_strike(25010, "PE") == 25050


def test_zero_offset_is_atm():
    assert target_strike(25010, "CE", 0) == 25000


def test_offset_is_in_the_money():
    assert STRIKE_OFFSET < 0


def test_validated_contract_is_exactly_50_points_itm():
    assert STRIKE_OFFSET * STRIKE_STEP == -50


def test_call_lands_50_below_spot():
    assert target_strike(25000, "CE") == 24950


def test_put_lands_50_above_spot():
    assert target_strike(25000, "PE") == 25050


def test_half_up_rounding_at_boundary():
    assert target_strike(25025, "CE", 0) == 25050


def test_rounds_down_below_half():
    assert target_strike(25024, "CE", 0) == 25000


# ── stop ladder ──────────────────────────────────────────────────────────────
def test_stop_is_base_below_trigger():
    assert effective_stop(0.10) == STOP_FRAC


def test_stop_moves_to_entry_at_trigger():
    assert effective_stop(0.25) == 0.0


def test_stop_still_at_entry_just_below_trail():
    assert effective_stop(0.34) == 0.0


def test_stop_locks_profit_past_trail():
    assert effective_stop(0.60) == pytest.approx(-0.25)


def test_stop_rides_the_peak():
    assert effective_stop(1.00) == pytest.approx(-0.65)


def test_stop_never_loosens():
    assert all(
        effective_stop(a) >= effective_stop(b)
        for a, b in zip([0, 0.2, 0.4, 0.6], [0.2, 0.4, 0.6, 0.8])
    )


# ── sizing ───────────────────────────────────────────────────────────────────
def test_sizes_at_least_one_lot_at_the_floor():
    assert size_position(50_000, 140) >= 1


def test_nan_premium_sizes_zero():
    assert size_position(50_000, float("nan")) == 0


def test_infinite_equity_sizes_zero():
    assert size_position(float("inf"), 140) == 0


def test_nan_equity_sizes_zero():
    assert size_position(float("nan"), 140) == 0


def test_size_respects_equity_cap():
    assert size_position(50_000, 140) * 140 * LOT_SIZE <= 50_000 * PER_TRADE_EQUITY_CAP + 1


def test_per_trade_cap_respected_at_low_equity():
    assert size_position(20_000, 140) * 140 * LOT_SIZE <= 20_000 * PER_TRADE_EQUITY_CAP


def test_size_caps_at_max_lots():
    assert size_position(1e9, 10) == MAX_LOTS


def test_size_rejects_zero_premium():
    assert size_position(500_000, 0) == 0


def test_size_is_monotone_in_equity():
    assert size_position(1_000_000, 140) >= size_position(500_000, 140)


def test_risk_cap_blocks_oversized_trade():
    assert size_position(50_000, 1300) == 0


def test_risk_cap_allows_normal_trade():
    assert size_position(50_000, 140) >= 1


# ── charges ──────────────────────────────────────────────────────────────────
def test_charges_are_positive():
    assert round_trip_charges(140, 160, 75) > 0


def test_charges_are_plausible():
    assert 45 < round_trip_charges(140, 160, 75) < 130


def test_charges_scale_with_quantity():
    assert round_trip_charges(140, 160, 150) > round_trip_charges(140, 160, 75)


# ── session windows ──────────────────────────────────────────────────────────
def test_1020_is_inside_entry_window():
    t = datetime(2026, 9, 8, 10, 20)
    assert ge(t, ENTRY_START) and lt(t, ENTRY_CUTOFF)


def test_1010_is_before_entry_window():
    assert not ge(datetime(2026, 9, 8, 10, 10), ENTRY_START)


def test_1430_is_after_entry_cutoff():
    assert not lt(datetime(2026, 9, 8, 14, 30), ENTRY_CUTOFF)


def test_1512_forces_close():
    assert ge(datetime(2026, 9, 8, 15, 12), FORCE_CLOSE)


# ── contract picking ─────────────────────────────────────────────────────────
TABLE = {
    (date(2026, 9, 15), 24950, "CE"): ("A", "1"),
    (date(2026, 9, 15), 25000, "CE"): ("B", "2"),
    (date(2026, 9, 9), 24950, "CE"): ("C", "3"),
}


def test_picks_contract_at_right_dte():
    got = pick_contract(TABLE, 25010, "CE", date(2026, 9, 10))
    assert got and got[3] in (24900, 24950, 25000)


def test_rejects_dte_below_minimum():
    assert pick_contract(TABLE, 25010, "CE", date(2026, 9, 14)) is None


def test_rejects_dte_above_maximum():
    # Every expiry in the table is more than MAX_DTE days away from this date.
    too_early = date(2026, 9, 9) - timedelta(days=MAX_DTE + 1)
    assert pick_contract(TABLE, 25010, "CE", too_early) is None


def test_accepts_dte_exactly_at_the_maximum():
    at_limit = date(2026, 9, 9) - timedelta(days=MAX_DTE)
    assert pick_contract(TABLE, 25010, "CE", at_limit) is not None


# ── guard ladder constants ───────────────────────────────────────────────────
def test_drawdown_stop_is_a_fraction():
    assert 0 < MAX_DRAWDOWN_STOP < 1


def test_daily_kill_exceeds_worst_backtested_trade():
    assert DAILY_LOSS_LIMIT_RS > WORST_TRADE_RS


def test_weekly_kill_above_daily():
    assert WEEKLY_LOSS_LIMIT_RS > DAILY_LOSS_LIMIT_RS


def test_weekly_kill_survives_a_bad_streak():
    assert WEEKLY_LOSS_LIMIT_RS >= DAILY_LOSS_LIMIT_RS * 2


def test_guards_are_ordered():
    assert WORST_TRADE_RS < DAILY_LOSS_LIMIT_RS < WEEKLY_LOSS_LIMIT_RS


def test_streak_halt_is_a_small_integer():
    assert 2 <= CONSEC_LOSS_HALT <= 6


def test_profit_lock_is_positive():
    assert DAILY_PROFIT_LOCK_RS > 0


def test_per_trade_risk_cap_is_set():
    assert PER_TRADE_RISK_RS > 0


def test_one_trade_per_day():
    assert MAX_TRADES_DAY == 1


def test_lookback_is_at_least_90():
    assert DONCHIAN_LB >= 90


def test_entry_starts_at_1015():
    assert ENTRY_START == (10, 15)


def test_per_trade_cap_is_60_percent():
    assert PER_TRADE_EQUITY_CAP == 0.60


def test_deploy_fraction_is_40_percent():
    assert DEPLOY_FRACTION == pytest.approx(0.40)


def test_capital_floor_matches_the_operators_build():
    assert MIN_CAPITAL == 0.0


def test_no_capital_floor_still_never_over_sizes_a_small_account():
    """Dropping the floor means a small account waits, not that it over-bets:
    one lot at Rs 140 costs more than the equity cap allows on Rs 10,000."""
    assert size_position(10_000, 140) == 0


def test_trail_and_breakeven_ordering():
    assert 0 < BE_TRIGGER <= TRAIL_FRAC < 1


# ── secret hygiene ───────────────────────────────────────────────────────────
BACKEND = Path(__file__).resolve().parent.parent


def test_no_broker_credentials_are_baked_into_source():
    """Credentials must come from the environment — never from a checked-in default."""
    offenders = []
    for path in BACKEND.rglob("*.py"):
        if "test" in path.parts or path.name == "conftest.py":
            continue
        text = path.read_text(encoding="utf-8")
        for marker in ("ANGEL_PASSWORD", "ANGEL_TOTP_SECRET", "ANGEL_API_KEY", "ANGEL_CLIENT_ID"):
            if f'setdefault("{marker}"' in text or f"setdefault('{marker}'" in text:
                offenders.append(f"{path.name}: setdefault for {marker}")
    assert not offenders, offenders


def test_credentials_are_read_from_environment():
    src = (BACKEND / "engine" / "broker.py").read_text(encoding="utf-8")
    assert "os.environ.get" in src


def test_paper_is_the_default_mode():
    from engine.runner import main  # noqa: F401

    src = (BACKEND / "engine" / "runner.py").read_text(encoding="utf-8")
    assert 'default=os.environ.get("MERIDIAN_TRADING_MODE", "paper")' in src


def test_no_time_stop_was_introduced():
    src = (BACKEND / "engine" / "runner.py").read_text(encoding="utf-8")
    assert "max_" + "hold" not in src


# ── lot size comes from the exchange, not a constant ─────────────────────────
def test_pick_contract_returns_the_exchange_lot_size():
    """NSE revises the NIFTY lot. Sizing on a stale constant places an order
    that is either rejected (AB4014) or a different size than intended."""
    from engine.strategy import pick_contract

    table = {
        (date(2026, 10, 1), 24950, "CE"): ("NIFTY01OCT2624950CE", "111", 65),
    }
    got = pick_contract(table, 25000.0, "CE", date(2026, 9, 25))
    assert got is not None
    symbol, token, expiry, strike, lot = got
    assert lot == 65, "the scrip master's lot must win over the build constant"
    assert symbol == "NIFTY01OCT2624950CE" and token == "111"


def test_pick_contract_falls_back_when_a_table_has_no_lot():
    from engine import config as C
    from engine.strategy import pick_contract

    legacy = {(date(2026, 10, 1), 24950, "CE"): ("SYM", "111")}
    assert pick_contract(legacy, 25000.0, "CE", date(2026, 9, 25))[4] == C.LOT_SIZE


def test_position_qty_prefers_what_was_actually_bought():
    from engine.runner import position_qty

    assert position_qty({"lots": 2, "lot_sz": 65, "qty": 130}) == 130
    # No recorded qty: use the position's own lot, never today's constant.
    assert position_qty({"lots": 2, "lot_sz": 65}) == 130


def test_position_qty_survives_a_lot_revision_mid_position():
    """A position opened at 65 must still be sold as 65 after the constant moves."""
    from engine import config as C
    from engine.runner import position_qty

    opened_at_65 = {"lots": 1, "lot_sz": 65}
    assert position_qty(opened_at_65) == 65
    assert position_qty(opened_at_65) != 1 * C.LOT_SIZE or C.LOT_SIZE == 65
