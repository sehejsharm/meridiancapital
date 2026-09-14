"""GANESH KAVACH 50K as a self-contained strategy module.

This is the reference implementation of the strategy contract, and it is
deliberately written in the same restricted dialect an uploaded algorithm must
use: maths and data libraries only, no imports from the engine, no filesystem,
no network. It therefore passes the identical acceptance gate every upload
faces — which is how the gate itself is validated. A gate that cannot accept a
real, working, backtested strategy is a broken gate.

The constants are duplicated from engine.config rather than imported, because
importing the engine is exactly what an uploaded strategy cannot do.
tests/test_gate.py asserts they stay equal, so drift fails the suite.
"""

from __future__ import annotations

import math

NAME = "GANESH KAVACH 50K"

LOT_SIZE = 75
STRIKE_STEP = 50
STRIKE_OFFSET = -1
DONCHIAN_LB = 90

TARGET_PTS = 90
BE_TRIGGER = 0.25
TRAIL_FRAC = 0.35
STOP_FRAC = 0.45

DEPLOY_FRACTION = 0.40
PER_TRADE_EQUITY_CAP = 0.60
PER_TRADE_RISK_RS = 9_000.0
MAX_LOTS = 10

MAX_TRADES_DAY = 1
DAILY_LOSS_LIMIT_RS = 12_000.0
WEEKLY_LOSS_LIMIT_RS = 25_000.0
CONSEC_LOSS_HALT = 4
MAX_DRAWDOWN_STOP = 0.45
MIN_CAPITAL = 50_000.0

ENTRY_START = (10, 15)
ENTRY_CUTOFF = (14, 0)
FORCE_CLOSE = (15, 10)


def signal(closes) -> str:
    """Donchian breakout. The channel deliberately excludes the current bar."""
    if closes is None:
        return ""
    try:
        n = len(closes)
    except TypeError:
        return ""
    if n < DONCHIAN_LB + 2:
        return ""

    window = closes[-(DONCHIAN_LB + 1) : -1]
    try:
        cur = float(closes.iloc[-1])
        hi = float(window.max())
        lo = float(window.min())
    except AttributeError:  # a plain sequence rather than a pandas Series
        cur = float(closes[-1])
        hi = max(float(x) for x in window)
        lo = min(float(x) for x in window)

    if not (math.isfinite(cur) and math.isfinite(hi) and math.isfinite(lo)):
        return ""
    if cur > hi:
        return "CE"
    if cur < lo:
        return "PE"
    return ""


def target_strike(spot: float, right: str) -> int:
    """One strike in the money from spot — half-up, not banker's rounding."""
    atm = int(math.floor(spot / STRIKE_STEP + 0.5) * STRIKE_STEP)
    return atm + STRIKE_OFFSET * STRIKE_STEP if right == "CE" else atm - STRIKE_OFFSET * STRIKE_STEP


def effective_stop(peak_gain: float) -> float:
    """Stop distance below entry as a fraction of premium. Only ever tightens."""
    if peak_gain >= TRAIL_FRAC:
        return -(peak_gain - TRAIL_FRAC)
    if peak_gain >= BE_TRIGGER:
        return 0.0
    return STOP_FRAC


def size_position(equity: float, premium: float) -> int:
    """Lots to buy, clamped by deploy fraction, equity cap, rupee risk and lot cap."""
    if not (math.isfinite(premium) and math.isfinite(equity)):
        return 0
    if premium <= 0 or equity <= 0:
        return 0
    cost_per_lot = premium * LOT_SIZE
    if cost_per_lot > equity * PER_TRADE_EQUITY_CAP:
        return 0
    if cost_per_lot * STOP_FRAC > PER_TRADE_RISK_RS:
        return 0
    by_fraction = int((equity * DEPLOY_FRACTION) // cost_per_lot)
    by_cap = int((equity * PER_TRADE_EQUITY_CAP) // cost_per_lot)
    by_risk = int(PER_TRADE_RISK_RS // (cost_per_lot * STOP_FRAC))
    return max(0, min(by_fraction, by_cap, by_risk, MAX_LOTS))


def guards() -> dict:
    return {
        "max_trades_day": MAX_TRADES_DAY,
        "daily_loss_limit_rs": DAILY_LOSS_LIMIT_RS,
        "weekly_loss_limit_rs": WEEKLY_LOSS_LIMIT_RS,
        "consec_loss_halt": CONSEC_LOSS_HALT,
        "max_drawdown_stop": MAX_DRAWDOWN_STOP,
        "min_capital": MIN_CAPITAL,
        "entry_start": ENTRY_START,
        "entry_cutoff": ENTRY_CUTOFF,
        "force_close": FORCE_CLOSE,
    }
