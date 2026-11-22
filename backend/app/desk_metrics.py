"""The Deck's metrics panel: performance, the strategy against NIFTY, and the market.

* **Performance** — over every real-money trade, from Angel's figures: win rate,
  profit factor, expectancy, Sharpe and Sortino per trade, the deepest fall and
  where the account is against its best point, the current streak.
* **Against NIFTY** — each trading day's result as a share of that morning's
  balance, against NIFTY's change that day. Beta is how much the results move
  with NIFTY; alpha is what is left once that is taken out (Jensen's alpha,
  annualised over 252 days). Under 20 days it is shown as too few to mean much.
* **Market** — NIFTY's change today and India VIX (Yahoo's public feed, the
  same one the chart falls back to), and the chain's PCR, ATM IV and max pain.

Display only: it decides nothing about a trade.
"""

from __future__ import annotations

import json
import math
import threading
import time
import urllib.request

from app import ledger

DAILY_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}?interval=1d&range=2y"
DAILY_TTL = 6 * 3600.0
QUOTE_TTL = 60.0
MIN_DAYS = 20
_cache: dict[str, dict] = {}
_lock = threading.Lock()


def _fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (meridian dashboard)"})
    with urllib.request.urlopen(req, timeout=8) as r:  # noqa: S310 - fixed https host
        return r.read()


def daily_closes(sym: str = "%5ENSEI", fetch=None, ttl: float = DAILY_TTL) -> dict[str, float]:
    """{YYYY-MM-DD: close} from Yahoo's daily chart, cached. Empty when unreachable."""
    with _lock:
        hit = _cache.get(sym)
        if hit and time.time() - hit["at"] < ttl:
            return hit["v"]
        try:
            res = json.loads((fetch or _fetch)(DAILY_URL.format(sym=sym)))["chart"]["result"][0]
            closes = res["indicators"]["quote"][0]["close"]
            out = {}
            for ts, c in zip(res["timestamp"], closes):
                if c is not None:
                    out[time.strftime("%Y-%m-%d", time.gmtime(int(ts) + 19_800))] = float(c)
        except Exception:
            out = hit["v"] if hit else {}
        _cache[sym] = {"at": time.time(), "v": out}
        return out


def _f(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def performance(trades: list[dict]) -> dict:
    """Ratios over closed real-money trades, oldest first, from Angel's net where known."""
    closed = sorted((t for t in trades if t.get("mode") == "live" and t.get("exit_ts")),
                    key=lambda t: str(t.get("exit_ts")))
    nets = [n for n in (_f(t.get("net_angel")) if t.get("net_angel") is not None else _f(t.get("net")) for t in closed)
            if n is not None]
    if not nets:
        return {"trades": 0}
    wins = [n for n in nets if n > 0]
    losses = [n for n in nets if n <= 0]
    mean = sum(nets) / len(nets)
    sd = math.sqrt(sum((n - mean) ** 2 for n in nets) / (len(nets) - 1)) if len(nets) > 1 else None
    down = [min(0.0, n) for n in nets]
    dsd = math.sqrt(sum(d * d for d in down) / len(down)) if down else None
    cum = high = dd = 0.0
    for n in nets:
        cum += n
        high = max(high, cum)
        dd = min(dd, cum - high)
    streak, kind = 0, None
    for n in reversed(nets):
        k = "win" if n > 0 else "loss"
        if kind in (None, k):
            kind, streak = k, streak + 1
        else:
            break
    return {
        "trades": len(nets), "net": round(sum(nets), 2), "win_rate": round(len(wins) / len(nets), 4),
        "profit_factor": round(sum(wins) / -sum(losses), 2) if losses and sum(losses) < 0 else None,
        "expectancy": round(mean, 2),
        "avg_win": round(sum(wins) / len(wins), 2) if wins else None,
        "avg_loss": round(sum(losses) / len(losses), 2) if losses else None,
        "sharpe_per_trade": round(mean / sd, 3) if sd else None,
        "sortino_per_trade": round(mean / dsd, 3) if dsd else None,
        "max_drawdown": round(dd, 2), "from_best": round(cum - high, 2),
        "streak": {"kind": kind, "count": streak},
    }


def against_market(trades: list[dict], openings: dict, closes: dict[str, float]) -> dict:
    """Daily results against NIFTY: beta, Jensen's alpha (annualised), correlation, period returns."""
    live = [t for t in trades if t.get("mode") == "live" and t.get("exit_ts")]
    if not live or len(closes) < 2:
        return {"available": False, "reason": "needs real-money trades and NIFTY's daily closes"}
    first = min(str(t["session_date"]) for t in live)
    pnl: dict[str, float] = {}
    for t in live:
        n = _f(t.get("net_angel")) if t.get("net_angel") is not None else _f(t.get("net"))
        if n is not None:
            pnl[str(t["session_date"])] = pnl.get(str(t["session_date"]), 0.0) + n
    days = sorted(d for d in closes if d >= first)
    prev_days = sorted(closes)
    rs, rm, bal = [], [], None
    for d in days:
        i = prev_days.index(d)
        if i == 0:
            continue
        m = closes[d] / closes[prev_days[i - 1]] - 1
        if openings.get(d) is not None:
            bal = float(openings[d])
        if not bal:
            continue
        rs.append(pnl.get(d, 0.0) / bal)
        rm.append(m)
    n = len(rs)
    if n < 2:
        return {"available": False, "reason": "too few trading days yet"}
    ms, mm = sum(rs) / n, sum(rm) / n
    cov = sum((a - ms) * (b - mm) for a, b in zip(rs, rm)) / (n - 1)
    var_m = sum((b - mm) ** 2 for b in rm) / (n - 1)
    var_s = sum((a - ms) ** 2 for a in rs) / (n - 1)
    beta = cov / var_m if var_m else None
    alpha_d = ms - (beta or 0.0) * mm
    corr = cov / math.sqrt(var_m * var_s) if var_m and var_s else None
    strat = math.prod(1 + r for r in rs) - 1
    mkt = math.prod(1 + r for r in rm) - 1
    return {
        "available": True, "days": n, "since": first, "enough": n >= MIN_DAYS,
        "alpha_annual": round(alpha_d * 252, 4), "alpha_daily": round(alpha_d, 6),
        "beta": round(beta, 3) if beta is not None else None, "correlation": round(corr, 3) if corr is not None else None,
        "strategy_return": round(strat, 4), "nifty_return": round(mkt, 4), "excess": round(strat - mkt, 4),
    }


def market(db, chain: dict | None, fetch=None) -> dict:
    nifty = daily_closes("%5ENSEI", fetch)
    vix = daily_closes("%5EINDIAVIX", fetch, ttl=QUOTE_TTL * 30)
    days = sorted(nifty)
    a = (chain or {}).get("analytics") or {}
    out = {
        "nifty_close": nifty[days[-1]] if days else None,
        "nifty_change_pct": round((nifty[days[-1]] / nifty[days[-2]] - 1) * 100, 2) if len(days) >= 2 else None,
        "nifty_as_of": days[-1] if days else None,
        "vix": vix[sorted(vix)[-1]] if vix else None,
        "pcr_oi": a.get("pcr_oi"), "atm_iv": a.get("atm_iv"), "max_pain": a.get("max_pain"),
        "call_wall": a.get("max_call_oi"), "put_wall": a.get("max_put_oi"),
    }
    return out


def build(db, fetch=None) -> dict:
    from app import market as mk

    algo = ledger.live_algo(db)
    rows = ledger.reconcile(db, db.trades(limit=5000, algo_id=algo)) if algo else []
    opens = ledger.openings(db, algo) if algo else {}
    chain = mk.option_chain(db)
    closes = daily_closes("%5ENSEI", fetch)
    return {"performance": performance(rows), "vs_nifty": against_market(rows, opens, closes),
            "market": market(db, chain if chain.get("available") else None, fetch)}
