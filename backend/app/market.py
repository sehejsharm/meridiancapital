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
    return {"available": True, **chain, "age_seconds": round(age, 1) if age is not None else None,
            "stale": age is None or age > CHAIN_STALE_SEC}
