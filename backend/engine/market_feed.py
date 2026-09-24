"""The dashboard's market view — today's NIFTY candles and the option chain —
published by an engine that is already logged in to Angel.

It runs on its own thread so the trading loop never waits on it, and it adds
no login: it reads through the engine's existing session, and one quote call
every 15 seconds covers the whole chain. With several engines running, one
holds a short lease and publishes for all of them.

The chain is centred on whichever NIFTY option the *account* holds — read
from Angel's position book, so a contract opened by a standalone program is
highlighted too — or, when flat, on the strike this engine would buy.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime

import engine.config as C
from engine.clock import is_market_hours, now_ist
from engine.greeks import RISK_FREE, greeks, years_to_expiry
from engine.strategy import pick_contract

FEED_INTERVAL = 15.0
CHAIN_WIDTH = 8                 # strikes either side of the money
BAR_LIMIT = 400                 # a full session of one-minute candles, and some
LEASE_SEC = 45.0

K_CHAIN = "market:chain"
K_BARS = "market:nifty_bars"
K_OWNER = "market:feed_owner"


def chart_time(ts) -> int:
    """IST wall-clock as seconds since the epoch, read as if it were UTC.

    The chart library draws in UTC; handing it the Indian wall-clock this way
    puts 09:15 at 09:15 on the axis whatever the viewer's own timezone.
    """
    naive = ts.replace(tzinfo=None) if getattr(ts, "tzinfo", None) else ts
    return int((naive - datetime(1970, 1, 1)).total_seconds())


def _is_nifty_option(tsym: str) -> bool:
    s = str(tsym).upper()
    return s.startswith("NIFTY") and s.endswith(("CE", "PE")) and not s.startswith(("NIFTYNXT", "NIFTYIT"))


class MarketFeed:
    def __init__(self, engine):
        self.eng = engine
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.errors = 0
        self.last_error = ""

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="market-feed", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.wait(FEED_INTERVAL):
            try:
                self.tick()
            except Exception as e:  # the dashboard's view must never touch trading
                self.errors += 1
                self.last_error = f"{type(e).__name__}: {e}"[:200]

    # ── one cycle ────────────────────────────────────────────────────────────
    def tick(self, now: datetime | None = None) -> bool:
        now = now or now_ist()
        if not is_market_hours(now) or not self._hold_lease():
            return False
        self.publish_bars(now)
        self.publish_chain(now)
        return True

    def _hold_lease(self) -> bool:
        db, me, t = self.eng.db, self.eng.algo_id, time.time()
        owner = db.kv_get(K_OWNER, None) or {}
        if owner.get("algo") not in (None, me) and float(owner.get("until") or 0) > t:
            return False
        db.kv_set(K_OWNER, {"algo": me, "until": t + LEASE_SEC})
        return True

    def publish_bars(self, now: datetime) -> None:
        df = (getattr(self.eng.br, "_bars", None) or {}).get("df")
        if df is None or not len(df):
            return
        today = df[df["ts"].dt.date == now.date()].tail(BAR_LIMIT)
        if not len(today):
            return
        bars = [
            {"t": chart_time(r.ts), "o": float(r.o), "h": float(r.h), "l": float(r.l), "c": float(r.close)}
            for r in today.itertuples()
        ]
        self.eng.db.kv_set(K_BARS, {"ts": now.isoformat(timespec="seconds"), "source": "angel", "bars": bars})

    def publish_chain(self, now: datetime) -> None:
        eng, br = self.eng, self.eng.br
        table = getattr(eng, "table", None) or {}
        spot = self._spot()
        if not table or not spot:
            return

        by_token = {v[1]: (k, v) for k, v in table.items()}
        highlight = None
        if eng.pos:
            highlight = {"tsym": eng.pos["tsym"], "token": str(eng.pos["token"]), "source": "engine",
                         "qty": eng.pos.get("qty")}
        else:
            held = br.open_positions() or []
            for p in held:
                tsym = str(p.get("tradingsymbol", ""))
                if _is_nifty_option(tsym):
                    highlight = {"tsym": tsym, "token": str(p.get("symboltoken", "")), "source": "account",
                                 "qty": int(float(p.get("netqty") or 0))}
                    break

        if highlight and highlight["token"] in by_token:
            (expiry, h_strike, h_right), _ = by_token[highlight["token"]]
            highlight.update(strike=h_strike, right=h_right, expiry=expiry.isoformat())
        else:
            pick = pick_contract(table, spot, "CE", now.date())
            if pick is None:
                return
            expiry = pick[2]
            if highlight:  # held, but not a contract in today's table — still name it
                highlight.update(strike=None, right=None, expiry=None)

        strikes = sorted({k for (e, k, _) in table if e == expiry})
        if not strikes:
            return
        atm = min(strikes, key=lambda k: abs(k - spot))
        i = strikes.index(atm)
        window = strikes[max(0, i - CHAIN_WIDTH): i + CHAIN_WIDTH + 1]
        if highlight and highlight.get("strike") and highlight["strike"] not in window:
            window = sorted(set(window) | {highlight["strike"]})

        contracts = {(k, r): table[(expiry, k, r)] for k in window for r in ("CE", "PE") if (expiry, k, r) in table}
        quotes = br.quotes("NFO", [v[1] for v in contracts.values()])
        if quotes is None:
            return

        t = years_to_expiry(now, expiry)
        rows = []
        for k in window:
            row = {"strike": k}
            for r in ("CE", "PE"):
                hit = contracts.get((k, r))
                if not hit:
                    row[r.lower()] = None
                    continue
                q = quotes.get(str(hit[1])) or {}
                g = greeks(q.get("ltp") or 0.0, spot, k, t, r) if q.get("ltp") else None
                row[r.lower()] = {
                    "tsym": hit[0], "token": str(hit[1]), **q,
                    "iv": round(g.iv * 100, 2) if g else None,
                    "delta": round(g.delta, 4) if g else None,
                    "gamma": round(g.gamma, 6) if g else None,
                    "theta": round(g.theta, 2) if g else None,
                    "vega": round(g.vega, 2) if g else None,
                }
            rows.append(row)

        lot = next((v[2] for v in contracts.values() if len(v) > 2 and v[2]), C.LOT_SIZE)
        self.eng.db.kv_set(K_CHAIN, {
            "ts": now.isoformat(timespec="seconds"),
            "spot": round(spot, 2), "expiry": expiry.isoformat(), "dte": (expiry - now.date()).days,
            "atm": atm, "lot": lot, "highlight": highlight, "rows": rows,
            "model": f"Black-Scholes on the live premium, r = {RISK_FREE:.1%}, no dividend yield",
            "publisher": self.eng.algo_id,
        })

    def _spot(self) -> float | None:
        spot, at = getattr(self.eng, "last_spot", None), getattr(self.eng, "last_spot_at", 0.0)
        if spot and time.time() - at < 60:
            return float(spot)
        return self.eng.br.ltp(C.INDEX_EXCH, C.INDEX_TSYM, C.INDEX_TOKEN)
