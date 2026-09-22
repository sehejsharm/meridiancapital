"""Pure strategy mathematics — signal, strike selection, stop ladder, sizing, costs.

These functions are deliberately side-effect free and identical in behaviour to
the backtested single-file build. The self-test suite pins every one of them.
"""

from __future__ import annotations

import math
from datetime import date

from engine import config as C


def donchian(closes, lookback: int | None = None):
    """Donchian breakout signal. Returns (view, channel_high, channel_low).

    view is "C" on an upside break, "P" on a downside break, "" otherwise.
    The channel deliberately excludes the current bar.
    """
    lookback = C.DONCHIAN_LB if lookback is None else lookback
    if closes is None or len(closes) < lookback + 2:
        return "", None, None
    cur = float(closes.iloc[-1])
    hi = float(closes.iloc[-(lookback + 1) : -1].max())
    lo = float(closes.iloc[-(lookback + 1) : -1].min())
    if cur > hi:
        return "C", hi, lo
    if cur < lo:
        return "P", hi, lo
    return "", hi, lo


def target_strike(
    spot: float, right: str, offset: int | None = None, step: int | None = None
) -> int:
    """One strike in the money from spot (ITM50 for the validated build)."""
    offset = C.STRIKE_OFFSET if offset is None else offset
    step = C.STRIKE_STEP if step is None else step
    atm = int(math.floor(spot / step + 0.5) * step)
    return atm + offset * step if right == "CE" else atm - offset * step


def effective_stop(
    peak_gain: float,
    base: float | None = None,
    be: float | None = None,
    trail: float | None = None,
) -> float:
    """Stop distance below entry as a fraction of premium.

    Positive = below entry, 0 = break-even, negative = locked-in profit. The
    ladder only ever tightens.
    """
    base = C.STOP_FRAC if base is None else base
    be = C.BE_TRIGGER if be is None else be
    trail = C.TRAIL_FRAC if trail is None else trail
    if peak_gain >= trail:
        return -(peak_gain - trail)
    if peak_gain >= be:
        return 0.0
    return base


def stop_label(es: float) -> str:
    if es < 0:
        return f"LOCKED +{-100 * es:.0f}%"
    if es == 0:
        return "breakeven"
    return f"{-100 * es:.0f}% below entry"


def size_position(
    equity: float,
    premium: float,
    lot: int | None = None,
    fraction: float | None = None,
    max_lots: int | None = None,
    equity_cap: float | None = None,
    risk_rs: float | None = None,
    stop_frac: float | None = None,
) -> int:
    """Lots to buy, clamped by deploy fraction, equity cap, rupee risk and lot cap."""
    lot = C.LOT_SIZE if lot is None else lot
    fraction = C.DEPLOY_FRACTION if fraction is None else fraction
    max_lots = C.MAX_LOTS if max_lots is None else max_lots
    equity_cap = C.PER_TRADE_EQUITY_CAP if equity_cap is None else equity_cap
    risk_rs = C.PER_TRADE_RISK_RS if risk_rs is None else risk_rs
    stop_frac = C.STOP_FRAC if stop_frac is None else stop_frac
    if premium <= 0 or equity <= 0:
        return 0
    if not (math.isfinite(premium) and math.isfinite(equity)):
        return 0
    cost_per_lot = premium * lot
    if cost_per_lot > equity * equity_cap:
        return 0
    if cost_per_lot * stop_frac > risk_rs:
        return 0
    by_fraction = int((equity * fraction) // cost_per_lot)
    by_cap = int((equity * equity_cap) // cost_per_lot)
    by_risk = int(risk_rs // (cost_per_lot * stop_frac))
    return max(0, min(by_fraction, by_cap, by_risk, max_lots))


def round_trip_charges(entry: float, exit_: float, qty: int) -> float:
    """DISPLAY-ONLY fallback estimate, used only when Angel is unreachable."""

    def leg(price: float, side: str) -> float:
        turn = price * qty
        b = 20.0
        stt = turn * 0.000500 if side == "SELL" else 0.0
        exch = turn * 0.00050
        sebi = turn * 0.000001
        ipft = turn * 0.0000005
        stamp = turn * 0.00003 if side == "BUY" else 0.0
        gst = (b + exch + sebi + ipft) * 0.18
        return b + stt + exch + sebi + ipft + stamp + gst

    return leg(entry, "BUY") + leg(exit_, "SELL")


def pick_contract(table: dict, spot: float, right: str, today: date):
    """Nearest qualifying contract: (symbol, token, expiry, strike, lot_size).

    The lot size comes from Angel's scrip master, not from a constant. NSE
    revises it, and sizing on a stale number places an order that is either
    rejected outright (AB4014, quantity not a multiple of the lot) or a
    different size than intended.
    """
    k0 = target_strike(spot, right)
    exps = sorted({e for (e, _, _) in table if C.MIN_DTE <= (e - today).days <= C.MAX_DTE})
    for exp in exps:
        for k in (k0, k0 + C.STRIKE_STEP, k0 - C.STRIKE_STEP):
            hit = table.get((exp, k, right))
            if hit:
                lot = hit[2] if len(hit) > 2 and hit[2] else C.LOT_SIZE
                return hit[0], hit[1], exp, k, lot
    return None


def money(x: float) -> str:
    s = f"Rs {abs(x):,.0f}"
    return ("-" + s) if x < 0 else s
