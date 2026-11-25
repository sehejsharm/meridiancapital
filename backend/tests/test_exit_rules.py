"""The desk's restatement of the program's exit rules (display and research only).

Two anchors: the program's own ``effective_stop`` table (its self-test values),
and the research engine's exits on real one-minute paths (tests/fixtures).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app import exit_rules as X

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "exit_paths.json").read_text())


@pytest.mark.parametrize("peak,v8,v10", [
    (0.00, -0.45, -0.45),      # nothing yet: the starting stop
    (0.19, -0.45, -0.45),
    (0.20, 0.00, 0.00),        # break-even
    (0.30, 0.10, 0.10),        # trail: 20% of entry behind the peak
    (0.45, 0.25, 0.25),
    (0.89, 0.69, 0.69),
    (0.90, 0.70, 0.80),        # v10's runner lock: 10% behind from +90%
    (1.50, 1.30, 1.40),
])
def test_the_stop_level_follows_the_programs_ladder(peak, v8, v10):
    assert X.V8.level(peak) == pytest.approx(v8)
    assert X.V9.level(peak) == pytest.approx(v8)          # v9 changed the take-profit, not the stop
    assert X.V10.level(peak) == pytest.approx(v10)


def test_the_stop_never_moves_down_as_the_peak_rises():
    for rs in X.STANDARD:
        last = -9.0
        for i in range(0, 300):
            lv = rs.level(i / 100)
            assert lv >= last - 1e-12, (rs.name, i)
            last = lv


@pytest.mark.parametrize("trade", FIXTURE["trades"], ids=lambda t: t["label"])
def test_the_replay_gives_the_research_engines_exit_on_real_paths(trade):
    bars = [(b[0], b[1]) for b in trade["bars"]]
    for key, rs in (("v8", X.V8), ("v9", X.V9), ("v10", X.V10), ("arm25", X.ARM25)):
        got = X.replay(rs, trade["entry"], trade["entry_time"], bars)
        want = trade["expected"][key]
        assert got["exit_time"] == want["exit_time"], (trade["label"], key)
        assert got["exit"] == pytest.approx(want["exit"], abs=0.011), (trade["label"], key)
        # The fixture's prices are rounded to the paisa; on 65 units that is under a rupee.
        assert got["net"] == pytest.approx(want["net"], abs=1.0), (trade["label"], key)


def test_the_fixture_covers_every_kind_of_exit():
    reasons = set()
    for t in FIXTURE["trades"]:
        for key, rs in (("v8", X.V8), ("v9", X.V9), ("v10", X.V10)):
            reasons.add(X.replay(rs, t["entry"], t["entry_time"], [(b[0], b[1]) for b in t["bars"]])["reason"])
    assert {"STOP", "TRAIL", "LATE_TP", "EOD"} <= reasons


def test_a_hand_worked_path():
    # Bought at 100.00 at 10:30. +25% at 10:32 (stop to 105), back to 104 at 10:33: sold at 104.
    bars = [("10:31", 110.0), ("10:32", 125.0), ("10:33", 104.0), ("10:34", 150.0)]
    r = X.replay(X.V8, 100.0, "10:30", bars)
    assert (r["exit_time"], r["exit"], r["reason"], r["peak_pct"], r["hold_min"]) == ("10:33", 104.0, "TRAIL", 25.0, 3)
    # Gross (104 - 100) x 65 = 260.00. Charges: 40 brokerage; exchange 13,260 x 0.0003503 = 4.6450;
    # SEBI 0.0133; stamp 6,500 x 0.00003 = 0.1950; STT 6,760 x 0.001 = 6.7600;
    # GST 18% of (40 + 4.6450 + 0.0133) = 8.0385. Total 59.65.
    assert r["gross"] == 260.0 and r["charges"] == 59.65 and r["net"] == 200.35


def test_the_late_take_profit_fills_at_its_level_or_at_the_market_when_it_switches_on():
    # Held 180 minutes at 13:30. Below +40% then, reaching it later: a resting sale, filled at 140.
    rising = [("13:29", 130.0), ("13:30", 135.0), ("13:31", 146.0)]
    r = X.replay(X.V9, 100.0, "10:30", rising)
    assert (r["exit_time"], r["exit"], r["reason"]) == ("13:31", 140.0, "LATE_TP")
    # Already above +40% the minute before it switches on: sold at that minute's close.
    above = [("13:29", 152.0), ("13:30", 150.0), ("13:31", 160.0)]
    r = X.replay(X.V9, 100.0, "10:30", above)
    assert (r["exit_time"], r["exit"], r["reason"]) == ("13:30", 150.0, "LATE_TP")
    # v8 has no late take-profit: it rides on.
    assert X.replay(X.V8, 100.0, "10:30", above)["reason"] == "EOD"


def test_no_path_is_no_answer():
    assert X.replay(X.V9, 100.0, "10:30", []) is None
    assert X.replay(X.V9, 0.0, "10:30", [("10:31", 5.0)]) is None


def test_the_rule_set_is_read_from_the_programs_own_settings():
    v10 = {"STOP_FRAC": 0.45, "BE_TRIGGER": 0.20, "TRAIL_FRAC": 0.20, "LATE_TP_AFTER_MIN": 180,
           "LATE_TP_FRAC": 0.40, "RUNNER_AT": 0.90, "RUNNER_FRAC": 0.10, "TARGET_PTS": None, "FORCE_CLOSE": "15:10"}
    rs = X.from_config(v10)
    for peak in (0.0, 0.2, 0.5, 0.9, 1.3):
        assert rs.level(peak) == pytest.approx(X.V10.level(peak))
    v8 = {"STOP_FRAC": 0.45, "BE_TRIGGER": 0.20, "TRAIL_FRAC": 0.20}
    assert X.from_config(v8).late_after_min is None and X.from_config(v8).runner_at is None
    assert X.from_config({"BE_TRIGGER": 0.2}) is None            # not enough known: say nothing


def test_what_the_exit_will_do_next_on_a_fresh_trade():
    p = X.plan(X.V10, entry=200.0, peak=0.05, held_min=30)
    assert p["stage"] == "initial stop"
    assert p["stop_price"] == 110.0 and p["stop_gain"] == -0.45          # 200 x 0.55
    assert p["next"]["key"] == "break_even" and p["next"]["at_price"] == 240.0
    assert [s["key"] for s in p["steps"]] == ["break_even", "runner"]
    assert p["steps"][1]["at_price"] == 380.0                             # +90% of 200
    assert p["late_tp"] == {"after_min": 180, "at_gain": 0.40, "at_price": 280.0, "minutes_left": 150.0, "on": False}
    assert p["locked_in"] == 0.0


def test_the_plan_after_break_even_and_in_the_runner_lock():
    p = X.plan(X.V10, entry=200.0, peak=0.35, held_min=200)
    assert p["stage"] == "trailing" and p["stop_price"] == 230.0 and p["locked_in"] == 0.15
    assert p["next"]["key"] == "runner" and p["late_tp"]["on"] is True and p["late_tp"]["minutes_left"] == 0.0
    p = X.plan(X.V10, entry=200.0, peak=1.0, held_min=60)
    assert p["stage"] == "runner lock" and p["stop_price"] == 380.0 and p["next"] is None
    v8 = X.plan(X.V8, entry=200.0, peak=1.0, held_min=60)
    assert v8["stage"] == "trailing" and v8["stop_price"] == 360.0 and v8["late_tp"] is None


# v18 (25 Nov 2026): the trail starts 27.5% of entry behind the best price and narrows to 20% by 180 minutes
# held. The desk restated it as v17's fixed 20% and showed a tighter stop than the program used.
V18_VALUES = {"STOP_FRAC": 0.30, "BE_TRIGGER": 0.20, "TRAIL_FRAC": 0.20, "TRAIL_START": 0.275, "TRAIL_NARROW_MIN": 180,
              "LATE_TP_AFTER_MIN": 180, "LATE_TP_FRAC": 0.40, "RUNNER_AT": 0.90, "RUNNER_FRAC": 0.10,
              "TARGET_PTS": None, "FORCE_CLOSE": "15:10"}


def test_v18s_trail_width_matches_the_programs_own_self_test():
    rs = X.from_config(V18_VALUES)
    assert rs.trail_start == 0.275 and rs.narrow_min == 180
    for held, w in ((0, 0.275), (60, 0.25), (120, 0.225), (180, 0.20), (400, 0.20)):
        assert rs.width(held) == pytest.approx(w)
    assert rs.width(None) == 0.20                                    # no time: v17's trail


@pytest.mark.parametrize("peak,held,level", [
    (0.50, 0, 0.225),      # program: effective_stop(0.50, held_min=0) == -0.225 (27.5% behind)
    (0.50, 180, 0.30),     # ... and at 180 minutes -0.30 (20% behind)
    (0.25, 0, 0.0),        # below the wider trail the stop sits at entry
    (0.19, 0, -0.30),      # below +20% the -30% stop
    (0.95, 0, 0.85),       # the +90% runner still rides 10% behind
])
def test_v18s_stop_level_is_the_programs(peak, held, level):
    assert X.from_config(V18_VALUES).level(peak, held) == pytest.approx(level)


def test_v18s_stop_only_tightens_with_time_and_with_the_peak():
    rs = X.from_config(V18_VALUES)
    for g in range(-40, 200, 5):
        for h in range(0, 240, 10):
            assert rs.level(g / 100, h + 10) >= rs.level(g / 100, h) - 1e-12
            assert rs.level((g + 5) / 100, h) >= rs.level(g / 100, h) - 1e-12


def test_v18s_plan_shows_the_wider_trail_early_in_the_trade():
    rs = X.from_config(V18_VALUES)
    p = X.plan(rs, entry=200.0, peak=0.50, held_min=60)                # 25% behind at 60 min
    assert p["stop_gain"] == pytest.approx(0.25) and p["stop_price"] == 250.0 and p["trail_now"] == 0.25
    assert p["stage"] == "trailing"
    assert "25.0% of the entry price behind it now (27.5% at entry, narrowing to 20% by 180 min held)" \
        in p["steps"][0]["effect"]
    v17 = X.plan(X.from_config({k: v for k, v in V18_VALUES.items() if not k.startswith("TRAIL_") or k == "TRAIL_FRAC"}),
                 entry=200.0, peak=0.50, held_min=60)
    assert v17["stop_price"] == 260.0                                  # v17: 20% behind, a tighter stop


def test_v18s_replay_uses_the_minutes_held():
    rs = X.from_config(V18_VALUES)
    # Up 50% in the first minute, then back to +24%: v17's 20% trail (stop +30%) sells, v18's 27.5% (+22.5%) holds.
    bars = [("10:31", 300.0), ("10:32", 248.0), ("15:09", 260.0)]
    assert X.replay(rs, 200.0, "10:30", bars)["reason"] == "EOD"
    v17 = X.from_config({k: v for k, v in V18_VALUES.items() if k not in ("TRAIL_START", "TRAIL_NARROW_MIN")})
    assert X.replay(v17, 200.0, "10:30", bars)["reason"] == "TRAIL"
