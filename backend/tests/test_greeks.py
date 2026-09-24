"""Greeks against textbook values and the identities they must satisfy."""

from __future__ import annotations

import math
from datetime import date, datetime

import pytest

from engine.greeks import greeks, implied_vol, price, years_to_expiry

# Hull, "Options, Futures and Other Derivatives", worked example 15.6:
# S=42, K=40, r=10%, sigma=20%, T=0.5 -> call 4.76, put 0.81.
def test_prices_match_the_textbook():
    assert price(42, 40, 0.5, 0.20, "CE", r=0.10) == pytest.approx(4.76, abs=0.01)
    assert price(42, 40, 0.5, 0.20, "PE", r=0.10) == pytest.approx(0.81, abs=0.01)


def test_implied_vol_recovers_the_volatility_that_made_the_price():
    for right in ("CE", "PE"):
        for vol in (0.08, 0.14, 0.35):
            p = price(25_000, 24_950, 5 / 365, vol, right)
            assert implied_vol(p, 25_000, 24_950, 5 / 365, right) == pytest.approx(vol, abs=1e-4)


def test_put_call_parity_holds():
    s, k, t, r, v = 25_000, 25_050, 6 / 365, 0.065, 0.13
    lhs = price(s, k, t, v, "CE", r) - price(s, k, t, v, "PE", r)
    assert lhs == pytest.approx(s - k * math.exp(-r * t), abs=1e-6)


def test_greeks_have_the_right_shape():
    s, t = 25_000, 5 / 365
    itm_call = greeks(price(s, 24_900, t, 0.13, "CE"), s, 24_900, t, "CE")
    atm_put = greeks(price(s, 25_000, t, 0.13, "PE"), s, 25_000, t, "PE")
    assert 0.5 < itm_call.delta < 1.0            # in the money: more than half
    assert -0.6 < atm_put.delta < -0.4           # at the money: about minus a half
    assert itm_call.gamma > 0 and atm_put.gamma > 0
    assert itm_call.theta < 0 and atm_put.theta < 0   # long options bleed daily
    assert itm_call.vega > 0 and atm_put.vega > 0


def test_call_and_put_deltas_differ_by_one():
    s, k, t = 25_000, 25_000, 5 / 365
    c = greeks(price(s, k, t, 0.13, "CE"), s, k, t, "CE")
    p = greeks(price(s, k, t, 0.13, "PE"), s, k, t, "PE")
    assert c.delta - p.delta == pytest.approx(1.0, abs=0.01)
    assert c.gamma == pytest.approx(p.gamma, rel=1e-3)


def test_vega_is_per_percentage_point():
    s, k, t = 25_000, 25_000, 5 / 365
    g = greeks(price(s, k, t, 0.13, "CE"), s, k, t, "CE")
    bumped = price(s, k, t, 0.14, "CE") - price(s, k, t, 0.13, "CE")
    assert g.vega == pytest.approx(bumped, rel=0.02)


def test_theta_is_per_calendar_day():
    s, k, t = 25_000, 25_000, 5 / 365
    g = greeks(price(s, k, t, 0.13, "CE"), s, k, t, "CE")
    one_day_later = price(s, k, t - 1 / 365, 0.13, "CE") - price(s, k, t, 0.13, "CE")
    assert g.theta == pytest.approx(one_day_later, rel=0.1)


def test_impossible_premiums_give_no_greeks_rather_than_wrong_ones():
    s, k, t = 25_000, 24_000, 5 / 365
    assert greeks(900.0, s, k, t, "CE") is None      # below intrinsic value
    assert greeks(0.0, s, k, t, "CE") is None
    assert greeks(float("nan"), s, k, t, "CE") is None


def test_expiry_day_stays_finite():
    now = datetime(2026, 9, 15, 15, 29, 30)
    t = years_to_expiry(now, date(2026, 9, 15))
    g = greeks(price(25_000, 24_950, t, 0.2, "CE"), 25_000, 24_950, t, "CE")
    assert g is None or all(math.isfinite(x) for x in (g.delta, g.gamma, g.theta, g.vega))


def test_time_to_expiry_runs_to_the_close():
    t = years_to_expiry(datetime(2026, 9, 14, 15, 30), date(2026, 9, 15))
    assert t * 365 == pytest.approx(1.0)
