"""Option Greeks from a live premium, by Black–Scholes.

Angel's quote gives price, volume and open interest but not the Greeks, so
they are derived here the way a broker's option chain derives them: solve for
the implied volatility that reproduces the traded premium, then read delta,
gamma, theta and vega off the model at that volatility.

European options on the index, no dividend yield: NIFTY weekly options are
European and cash-settled, and over a few days to expiry the index's dividend
yield moves these figures by less than their own rounding.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

RISK_FREE = 0.065          # annualised; roughly the 91-day T-bill
YEAR_DAYS = 365.0
MIN_T = 1.0 / (YEAR_DAYS * 24 * 60)   # one minute — expiry-day maths stays finite


def _cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def price(spot: float, strike: float, t: float, vol: float, right: str, r: float = RISK_FREE) -> float:
    t = max(t, MIN_T)
    if vol <= 0:
        fwd = spot - strike * math.exp(-r * t)
        return max(fwd, 0.0) if right == "CE" else max(-fwd, 0.0)
    d1 = (math.log(spot / strike) + (r + 0.5 * vol * vol) * t) / (vol * math.sqrt(t))
    d2 = d1 - vol * math.sqrt(t)
    if right == "CE":
        return spot * _cdf(d1) - strike * math.exp(-r * t) * _cdf(d2)
    return strike * math.exp(-r * t) * _cdf(-d2) - spot * _cdf(-d1)


def implied_vol(premium: float, spot: float, strike: float, t: float, right: str,
                r: float = RISK_FREE) -> float | None:
    """The volatility at which the model reproduces `premium`, or None.

    Bisection rather than Newton: it cannot diverge on deep in- or out-of-the-
    money strikes where vega is near zero, and 60 halvings is well inside a
    quote cycle. None when the premium sits outside what any volatility can
    produce — below intrinsic value, or a stale print.
    """
    if not all(math.isfinite(v) and v > 0 for v in (premium, spot, strike)):
        return None
    t = max(t, MIN_T)
    lo, hi = 1e-4, 5.0
    if premium < price(spot, strike, t, lo, right, r) - 1e-6 or premium > price(spot, strike, t, hi, right, r):
        return None
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        if price(spot, strike, t, mid, right, r) > premium:
            hi = mid
        else:
            lo = mid
    return 0.5 * (lo + hi)


@dataclass(frozen=True)
class Greeks:
    iv: float        # annualised, as a fraction (0.14 = 14%)
    delta: float     # per 1 point in the index
    gamma: float     # delta change per 1 point
    theta: float     # premium change per calendar day, rupees per unit
    vega: float      # premium change per 1 percentage point of volatility


def greeks(premium: float, spot: float, strike: float, t: float, right: str,
           r: float = RISK_FREE) -> Greeks | None:
    vol = implied_vol(premium, spot, strike, t, right, r)
    if vol is None:
        return None
    t = max(t, MIN_T)
    sq = math.sqrt(t)
    d1 = (math.log(spot / strike) + (r + 0.5 * vol * vol) * t) / (vol * sq)
    d2 = d1 - vol * sq
    gamma = _pdf(d1) / (spot * vol * sq)
    vega = spot * _pdf(d1) * sq / 100.0
    if right == "CE":
        delta = _cdf(d1)
        theta = (-spot * _pdf(d1) * vol / (2 * sq) - r * strike * math.exp(-r * t) * _cdf(d2)) / YEAR_DAYS
    else:
        delta = _cdf(d1) - 1.0
        theta = (-spot * _pdf(d1) * vol / (2 * sq) + r * strike * math.exp(-r * t) * _cdf(-d2)) / YEAR_DAYS
    return Greeks(iv=vol, delta=delta, gamma=gamma, theta=theta, vega=vega)


def years_to_expiry(now, expiry_date) -> float:
    """Calendar time to 15:30 IST on expiry day, in years."""
    from datetime import datetime, time

    close = datetime.combine(expiry_date, time(15, 30))
    if now.tzinfo is not None:
        close = close.replace(tzinfo=now.tzinfo)
    return max((close - now).total_seconds(), 0.0) / (YEAR_DAYS * 86400.0)
