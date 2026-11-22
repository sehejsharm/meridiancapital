"""Greeks for every trade, worked out from what each trade recorded.

A trade keeps its premium and the NIFTY level at entry and at exit (or the
desk finds the NIFTY level from the program's own status samples near those
moments). From those, Black-Scholes gives the implied volatility and the
Greeks the contract had at each end, and a first-order split of the trade's
result:

* **NIFTY's move** — the average of the two deltas times the points NIFTY
  moved, times the quantity;
* **time decay** — the average theta times the days held, times the quantity;
* **volatility** — the average vega times the change in implied volatility;
* **the rest** — gamma and everything a first-order split leaves out.

Display only: nothing here reaches a program.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from engine import greeks as G

SPOT_WINDOW_MIN = 5      # a status sample this close to entry or exit stands in for a missing NIFTY level


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None


def _ts(v) -> datetime | None:
    try:
        return datetime.fromisoformat(str(v).replace(" ", "T")[:19])
    except (TypeError, ValueError):
        return None


def at(premium, spot, strike, expiry, side, when: datetime | None, qty) -> dict | None:
    """IV and Greeks of one contract at one moment; None when they cannot be solved."""
    premium, spot, strike = _num(premium), _num(spot), _num(strike)
    if not (premium and spot and strike and when) or side not in ("CE", "PE"):
        return None
    try:
        exp = date.fromisoformat(str(expiry)[:10])
    except ValueError:
        return None
    g = G.greeks(premium, spot, strike, G.years_to_expiry(when, exp), side)
    if g is None:
        return None
    q = int(qty or 0)
    return {"iv": round(g.iv * 100, 2), "delta": round(g.delta, 3), "gamma": round(g.gamma, 5),
            "theta": round(g.theta, 2), "vega": round(g.vega, 2), "spot": round(spot, 2),
            "delta_position": round(g.delta * q, 1) if q else None,
            "theta_position": round(g.theta * q, 0) if q else None,
            "dte": (exp - when.date()).days}


def spot_near(db, algo_id: str | None, when: datetime | None) -> float | None:
    """The NIFTY level a program reported closest to ``when`` (within a few minutes)."""
    if when is None or not algo_id:
        return None
    lo = (when - timedelta(minutes=SPOT_WINDOW_MIN)).isoformat(timespec="seconds")
    hi = (when + timedelta(minutes=SPOT_WINDOW_MIN)).isoformat(timespec="seconds")
    try:
        with db.conn() as c:
            rows = c.execute("SELECT ts, spot FROM equity_samples WHERE algo_id = ? AND ts BETWEEN ? AND ? "
                             "AND spot IS NOT NULL", (algo_id, lo, hi)).fetchall()
    except Exception:
        return None
    best = min(rows, key=lambda r: abs((_ts(r["ts"]) or when) - when), default=None)
    return float(best["spot"]) if best else None


def for_trade(db, t: dict) -> dict:
    """Greeks at entry and exit, and the split of the result. Missing pieces are None, never guessed."""
    entry_ts, exit_ts = _ts(t.get("entry_ts")), _ts(t.get("exit_ts"))
    spot_in = _num(t.get("spot_entry")) or spot_near(db, t.get("algo_id"), entry_ts)
    spot_out = _num(t.get("spot_exit")) or (spot_near(db, t.get("algo_id"), exit_ts) if exit_ts else None)
    qty = t.get("qty") or ((t.get("lots") or 0) * 65)
    side = t.get("side")
    g_in = at(t.get("entry_prem"), spot_in, t.get("strike"), t.get("expiry"), side, entry_ts, qty)
    g_out = at(t.get("exit_prem"), spot_out, t.get("strike"), t.get("expiry"), side, exit_ts, qty) if exit_ts else None
    split = None
    if g_in and g_out and qty:
        avg = lambda k: (g_in[k] + g_out[k]) / 2  # noqa: E731
        held_days = max(0.0, (exit_ts - entry_ts).total_seconds() / 86400)
        move = round(avg("delta") * qty * (spot_out - spot_in), 2)
        decay = round(avg("theta") * qty * held_days, 2)
        vol = round(avg("vega") * qty * (g_out["iv"] - g_in["iv"]), 2)
        gross = _num(t.get("gross"))
        if gross is None and _num(t.get("exit_prem")) is not None:
            gross = (float(t["exit_prem"]) - float(t["entry_prem"])) * qty
        split = {"nifty_move": move, "time_decay": decay, "volatility": vol,
                 "rest": round(gross - move - decay - vol, 2) if gross is not None else None,
                 "gross": round(gross, 2) if gross is not None else None,
                 "nifty_points": round(spot_out - spot_in, 2), "iv_change": round(g_out["iv"] - g_in["iv"], 2)}
    return {"entry": g_in, "exit": g_out, "split": split}


def brief(db, t: dict) -> dict:
    """The few figures a blotter row shows: delta and IV at entry, delta at exit."""
    g = for_trade(db, t)
    return {"delta_in": (g["entry"] or {}).get("delta"), "iv_in": (g["entry"] or {}).get("iv"),
            "delta_out": (g["exit"] or {}).get("delta")}
