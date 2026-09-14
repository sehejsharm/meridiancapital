"""The dashboard's strategy editor: bounds, cross-checks, and the risk gate.

These pin the promises the editor makes to the operator — that a value outside
its bounds cannot be stored, that an incoherent combination is refused as a
combination, that raising risk needs the confirmation phrase, and that reset
really does restore the backtested build.
"""

from __future__ import annotations

import pytest

from engine import config as C
from engine import tuning


@pytest.fixture(autouse=True)
def restore_config():
    """Every test here mutates engine.config; put it back afterwards."""
    saved = {p.key: getattr(C, p.key) for p in tuning.PARAMS}
    yield
    for k, v in saved.items():
        setattr(C, k, v)


# ── the schema itself ────────────────────────────────────────────────────────
def test_defaults_match_the_shipped_config():
    for p in tuning.PARAMS:
        live = getattr(C, p.key)
        expected = f"{live[0]:02d}:{live[1]:02d}" if p.kind == "time" else live
        assert p.default == expected, f"{p.key} default drifted from engine.config"


def test_every_default_sits_inside_its_own_bounds():
    for p in tuning.PARAMS:
        if p.kind == "time":
            continue
        assert p.lo <= p.default <= p.hi, f"{p.key} default is outside its bounds"


def test_defaults_validate_as_a_set():
    assert tuning.validate(tuning.defaults())


# ── bounds ───────────────────────────────────────────────────────────────────
def test_a_value_above_its_ceiling_is_refused():
    with pytest.raises(tuning.TuningError, match="between"):
        tuning.validate({"STOP_FRAC": 0.99})


def test_a_value_below_its_floor_is_refused():
    with pytest.raises(tuning.TuningError, match="between"):
        tuning.validate({"MAX_LOTS": 0})


def test_an_unknown_parameter_is_refused():
    with pytest.raises(tuning.TuningError, match="not a tunable"):
        tuning.validate({"INDEX_TOKEN": "99926000"})


def test_a_non_numeric_value_is_refused():
    with pytest.raises(tuning.TuningError, match="expected a number"):
        tuning.validate({"MAX_LOTS": "ten"})


def test_a_malformed_time_is_refused():
    with pytest.raises(tuning.TuningError, match="HH:MM"):
        tuning.validate({"ENTRY_START": "quarter past ten"})


def test_an_impossible_time_is_refused():
    with pytest.raises(tuning.TuningError, match="not a valid time"):
        tuning.validate({"ENTRY_START": "25:00"})


# ── cross-checks: individually legal, jointly incoherent ─────────────────────
def test_expiry_window_must_not_invert():
    with pytest.raises(tuning.TuningError, match="Min days"):
        tuning.validate({"MIN_DTE": 6, "MAX_DTE": 3})


def test_stop_ladder_must_only_tighten():
    with pytest.raises(tuning.TuningError, match="tightens"):
        tuning.validate({"BE_TRIGGER": 0.60, "TRAIL_FRAC": 0.20})


def test_deploy_fraction_cannot_exceed_the_equity_cap():
    with pytest.raises(tuning.TuningError, match="equity cap"):
        tuning.validate({"DEPLOY_FRACTION": 0.90, "PER_TRADE_EQUITY_CAP": 0.50})


def test_daily_loss_limit_cannot_exceed_the_weekly_one():
    with pytest.raises(tuning.TuningError, match="weekly"):
        tuning.validate({"DAILY_LOSS_LIMIT_RS": 60_000, "WEEKLY_LOSS_LIMIT_RS": 25_000})


def test_session_windows_must_stay_ordered():
    with pytest.raises(tuning.TuningError, match="entry opens"):
        tuning.validate({"ENTRY_START": "14:30", "ENTRY_CUTOFF": "11:00"})


def test_force_close_must_precede_the_market_close():
    with pytest.raises(tuning.TuningError, match="market close"):
        tuning.validate({"FORCE_CLOSE": "15:45"})


def test_entry_cannot_open_before_the_market_does():
    with pytest.raises(tuning.TuningError, match="market open"):
        tuning.validate({"ENTRY_START": "08:00"})


# ── the risk gate ────────────────────────────────────────────────────────────
def test_a_wider_stop_counts_as_riskier():
    assert "STOP_FRAC" in tuning.riskier_keys({"STOP_FRAC": 0.70})


def test_a_tighter_stop_does_not():
    assert tuning.riskier_keys({"STOP_FRAC": 0.20}) == []


def test_a_longer_lookback_is_not_riskier():
    assert tuning.riskier_keys({"DONCHIAN_LB": 150}) == []


def test_a_shorter_lookback_is_riskier():
    assert "DONCHIAN_LB" in tuning.riskier_keys({"DONCHIAN_LB": 30})


def test_a_lower_capital_floor_is_riskier():
    assert "MIN_CAPITAL" in tuning.riskier_keys({"MIN_CAPITAL": 20_000})


def test_a_later_force_close_is_riskier():
    assert "FORCE_CLOSE" in tuning.riskier_keys({"FORCE_CLOSE": "15:25"})


def test_an_earlier_force_close_is_not():
    assert tuning.riskier_keys({"FORCE_CLOSE": "14:30"}) == []


# ── application ──────────────────────────────────────────────────────────────
def test_apply_reports_only_what_actually_moved():
    assert tuning.apply({"TARGET_PTS": C.TARGET_PTS}) == []
    assert tuning.apply({"TARGET_PTS": 120}) == ["TARGET_PTS"]


def test_apply_converts_times_back_to_engine_tuples():
    tuning.apply({"ENTRY_START": "11:30"})
    assert C.ENTRY_START == (11, 30)


def test_applied_values_reach_the_strategy_functions():
    from engine import strategy

    assert strategy.effective_stop(0.0) == C.STOP_FRAC
    tuning.apply({"STOP_FRAC": 0.25})
    assert strategy.effective_stop(0.0) == 0.25


def test_applied_strike_offset_reaches_strike_selection():
    from engine import strategy

    assert strategy.target_strike(25_000, "CE") == 24_950
    tuning.apply({"STRIKE_OFFSET": 0})
    assert strategy.target_strike(25_000, "CE") == 25_000
