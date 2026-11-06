"""NIFTY candles and the option chain, as the dashboard reads them.

Candles come from a running engine — the same Angel feed the strategy trades
on — and fall back to Yahoo Finance's public ^NSEI series when no engine is
up. Yahoo is a courtesy feed with no key: fine for looking at the index,
never used for a trading decision, and labelled as such.

The chain only exists while an engine is running, because it is read through
that engine's Angel session; there is no public source for NFO quotes worth
trusting.
"""

from __future__ import annotations

import json
import math
import threading
import time
import urllib.request
from datetime import datetime

from engine.clock import now_ist
from engine.market_feed import K_BARS, K_CHAIN

YAHOO_URL = "https://query1.finance.yahoo.com/v8/finance/chart/%5ENSEI?interval=1m&range=1d"
YAHOO_TTL = 30.0
IST_OFFSET = 19_800            # seconds; the chart reads Indian wall-clock
ENGINE_FRESH_SEC = 180.0
CHAIN_STALE_SEC = 60.0

_yahoo = {"at": 0.0, "bars": None, "error": None}
_yahoo_lock = threading.Lock()


def _age(ts: str | None) -> float | None:
    """Seconds since ``ts``. A naive timestamp is IST, as every engine writes it.

    It used to be read as the server's local time — UTC on the VM — which put
    a fresh timestamp 5.5 hours in the future: new data measured -19,800s old,
    and data up to 5.5 hours stale still passed as fresh.
    """
    if not ts:
        return None
    try:
        t = datetime.fromisoformat(ts)
    except ValueError:
        return None
    if t.tzinfo is None:
        return (now_ist() - t).total_seconds()
    return (datetime.now(t.tzinfo) - t).total_seconds()


def _yahoo_bars(fetch=None) -> tuple[list[dict] | None, str | None]:
    with _yahoo_lock:
        if time.time() - _yahoo["at"] < YAHOO_TTL:
            return _yahoo["bars"], _yahoo["error"]
        try:
            raw = (fetch or _fetch)(YAHOO_URL)
            res = json.loads(raw)["chart"]["result"][0]
            q = res["indicators"]["quote"][0]
            bars = [
                {"t": int(ts) + IST_OFFSET, "o": o, "h": h, "l": lo, "c": c}
                for ts, o, h, lo, c in zip(res["timestamp"], q["open"], q["high"], q["low"], q["close"])
                if None not in (o, h, lo, c)
            ]
            _yahoo.update(at=time.time(), bars=bars, error=None)
        except Exception as e:
            _yahoo.update(at=time.time(), bars=None, error=f"{type(e).__name__}")
        return _yahoo["bars"], _yahoo["error"]


def _fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (MeridianCapital desk)"})
    with urllib.request.urlopen(req, timeout=6) as r:  # noqa: S310 - fixed https url
        return r.read()


def nifty_chart(db, fetch=None) -> dict:
    """Today's one-minute NIFTY candles, from the best source available now."""
    engine = db.kv_get(K_BARS, None) or {}
    age = _age(engine.get("ts"))
    if engine.get("bars") and age is not None and age < ENGINE_FRESH_SEC:
        return {"source": "angel", "label": "Angel One, via the running engine",
                "bars": engine["bars"], "age_seconds": round(age, 1)}
    bars, error = _yahoo_bars(fetch)
    if bars:
        return {"source": "yahoo", "label": "Yahoo Finance (public feed, may lag a minute)",
                "bars": bars, "age_seconds": None}
    if engine.get("bars"):
        return {"source": "angel", "label": "Angel One — last candles from before the engine stopped",
                "bars": engine["bars"], "age_seconds": round(age, 1) if age else None, "stale": True}
    return {"source": None, "label": None, "bars": [], "error": error or "no source available"}


def option_chain(db) -> dict:
    chain = db.kv_get(K_CHAIN, None)
    if not chain:
        return {"available": False,
                "reason": "The option chain streams through a running engine's Angel session. Start "
                          "any strategy algorithm — paper is fine — during market hours."}
    age = _age(chain.get("ts"))
    stale = age is None or age > CHAIN_STALE_SEC
    if not stale:
        oi_baseline(db, chain)
    return {"available": True, **chain, **chain_analytics(chain, db.kv_get(_open_key(chain), None)),
            "age_seconds": round(age, 1) if age is not None else None, "stale": stale}


# ── what the chain says beyond each contract's own quote ─────────────────────
# Angel's quote carries open interest but not its change, so the first OI the
# desk sees for each contract each day is kept, and the change is measured from
# there ("since 09:16"), never presented as the change from yesterday's close.
K_CHAIN_OPEN = "market:chain:oi-open:{day}"
SKEW_STRIKES = 3   # out-of-the-money distance for the skew, in strikes


def _open_key(chain: dict) -> str:
    return K_CHAIN_OPEN.format(day=(chain.get("ts") or "")[:10])


def oi_baseline(db, chain: dict | None = None) -> None:
    """Keep the day's first OI reading for every contract the chain shows."""
    chain = chain if chain is not None else db.kv_get(K_CHAIN, None)
    if not chain or not chain.get("ts"):
        return
    key = _open_key(chain)
    base = db.kv_get(key, None) or {"ts": chain["ts"], "oi": {}}
    added = False
    for row in chain.get("rows") or []:
        for side in ("ce", "pe"):
            q = row.get(side) or {}
            tok, oi = q.get("token"), q.get("oi")
            if tok and oi is not None and tok not in base["oi"]:
                base["oi"][tok] = {"oi": oi, "ts": chain["ts"]}
                added = True
    if added:
        db.kv_set(key, base)


def chain_analytics(chain: dict, base: dict | None) -> dict:
    """Put/call ratios, max pain, the heaviest strikes, IV skew and each contract's OI change.

    All of it over the strikes the chain shows (about 8 either side of the money),
    which is where nearly all of a weekly expiry's open interest sits; the panel says so.
    """
    rows = chain.get("rows") or []
    opened = (base or {}).get("oi") or {}
    since = (base or {}).get("ts")

    def val(row, side, key):
        q = row.get(side) or {}
        v = q.get(key)
        return float(v) if isinstance(v, (int, float)) else None

    ce_oi = {r["strike"]: val(r, "ce", "oi") or 0.0 for r in rows}
    pe_oi = {r["strike"]: val(r, "pe", "oi") or 0.0 for r in rows}
    ce_vol = sum(val(r, "ce", "volume") or 0.0 for r in rows)
    pe_vol = sum(val(r, "pe", "volume") or 0.0 for r in rows)
    total_ce, total_pe = sum(ce_oi.values()), sum(pe_oi.values())

    # Max pain: the expiry level at which option holders, all strikes together, would collect least.
    max_pain = None
    if total_ce + total_pe > 0:
        def payout(s):
            return sum(oi * max(0.0, s - k) for k, oi in ce_oi.items()) + sum(oi * max(0.0, k - s) for k, oi in pe_oi.items())
        max_pain = min(ce_oi, key=payout)

    # Each contract's change in OI since the desk first saw it today.
    changes = {}
    for r in rows:
        for side in ("ce", "pe"):
            q = r.get(side) or {}
            first = opened.get(q.get("token") or "")
            if first and q.get("oi") is not None and first.get("ts") == since:
                changes[q["token"]] = q["oi"] - first["oi"]

    atm = chain.get("atm")
    strikes = [r["strike"] for r in rows]
    skew = None
    if atm in strikes:
        i = strikes.index(atm)
        lo, hi = i - SKEW_STRIKES, i + SKEW_STRIKES
        if lo >= 0 and hi < len(rows):
            put_iv, call_iv = val(rows[lo], "pe", "iv"), val(rows[hi], "ce", "iv")
            if put_iv is not None and call_iv is not None:
                skew = {"put_strike": rows[lo]["strike"], "call_strike": rows[hi]["strike"],
                        "put_iv": put_iv, "call_iv": call_iv, "skew": round(put_iv - call_iv, 2)}
    atm_row = next((r for r in rows if r["strike"] == atm), None)
    atm_ivs = [v for v in (val(atm_row, "ce", "iv"), val(atm_row, "pe", "iv")) if v is not None] if atm_row else []

    return {"analytics": {
        "pcr_oi": round(total_pe / total_ce, 2) if total_ce else None,
        "pcr_volume": round(pe_vol / ce_vol, 2) if ce_vol else None,
        "max_pain": max_pain,
        "max_call_oi": max(ce_oi, key=ce_oi.get) if total_ce else None,
        "max_put_oi": max(pe_oi, key=pe_oi.get) if total_pe else None,
        "max_oi": max([*ce_oi.values(), *pe_oi.values()], default=0.0),
        "atm_iv": round(sum(atm_ivs) / len(atm_ivs), 2) if atm_ivs else None,
        "skew": skew,
        "oi_change": changes,
        "oi_since": since if changes else None,
        "strikes_shown": len(rows),
    }}


# ── a standalone program's contract, without asking Angel for anything ───────
STRIKE_STEP = 50
WEEKLY_EXPIRY_WEEKDAY = 1          # NIFTY weeklies expire on Tuesdays
MIN_DTE, MAX_DTE = 2, 8            # the strategy's own expiry window


def next_expiry(today, holidays: set[str]):
    """The weekly expiry the strategy would pick today: the first Tuesday 2–8
    days away, moved back to the previous trading day if it is a holiday.
    An estimate for display — the program reads Angel's contract list."""
    from datetime import timedelta

    from shared.market_calendar import is_trading_day

    for ahead in range(0, 15):
        d = today + timedelta(days=ahead)
        if d.weekday() != WEEKLY_EXPIRY_WEEKDAY:
            continue
        exp = d
        while not is_trading_day(exp, holidays):
            exp -= timedelta(days=1)
        if MIN_DTE <= (exp - today).days <= MAX_DTE:
            return exp
    return None


def _f(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if x == x else None


def program_contract(snap: dict, holidays: set[str], now) -> dict | None:
    """What the option panel can say about a running program from its own
    status alone: the contract held, with Greeks solved from the premium the
    program already reads, or the contracts a breakout would buy. Costs no
    Angel requests, so it never competes with the program's rate budget."""
    from datetime import date

    from engine import greeks as G

    if not snap:
        return None
    sig = snap.get("signal") or {}
    spot = _f(sig.get("spot"))
    pos = snap.get("position") or None
    out = {"ts": snap.get("ts"), "spot": spot, "held": None, "next": None,
           "name": (snap.get("engine") or {}).get("name")}
    if pos:
        side = pos.get("side")
        strike = _f(pos.get("strike"))
        live = _f(pos.get("live_premium"))
        entry = _f(pos.get("entry_premium"))
        qty = int(_f(pos.get("qty")) or 0)
        target_pts = _f(pos.get("target_pts")) or 0.0
        spot_entry = _f(pos.get("spot_entry"))
        try:
            expiry = date.fromisoformat(str(pos.get("expiry"))[:10])
        except ValueError:
            expiry = None
        held = {
            "tsym": pos.get("tsym"), "side": side, "strike": strike,
            "expiry": expiry.isoformat() if expiry else None,
            "dte": (expiry - now.date()).days if expiry else None,
            "lots": pos.get("lots"), "qty": qty, "entry": entry, "live": live,
            "gain_pct": _f(pos.get("gain_pct")), "unrealised": _f(pos.get("unrealised")),
            "peak_pct": _f(pos.get("peak_pct")),
            "stop": _f(pos.get("stop_price")) or None,
            "trail_pct": _f(pos.get("trail_pct")),
            "target_level": (spot_entry + target_pts if side == "CE" else spot_entry - target_pts)
            if spot_entry and target_pts else None,
            "spot_entry": spot_entry, "opened_ts": pos.get("opened_ts"),
            "greeks": None,
        }
        if live and spot and strike and expiry and side in ("CE", "PE"):
            g = G.greeks(live, spot, strike, G.years_to_expiry(now, expiry), side)
            if g:
                held["greeks"] = {
                    "iv": round(g.iv * 100, 2), "delta": round(g.delta, 3), "gamma": round(g.gamma, 5),
                    "theta": round(g.theta, 2), "vega": round(g.vega, 2),
                    # in rupees for the whole position, which is what the operator feels
                    "theta_position": round(g.theta * qty, 0) if qty else None,
                    "delta_position": round(g.delta * qty, 1) if qty else None,
                }
        out["held"] = held
    elif spot:
        atm = int(math.floor(spot / STRIKE_STEP + 0.5) * STRIKE_STEP)
        exp = next_expiry(now.date(), holidays)
        out["next"] = {
            "expiry": exp.isoformat() if exp else None,
            "call": {"strike": atm - STRIKE_STEP, "trigger": _f(sig.get("channel_high"))},
            "put": {"strike": atm + STRIKE_STEP, "trigger": _f(sig.get("channel_low"))},
        }
    return out
