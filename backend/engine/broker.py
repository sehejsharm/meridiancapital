"""Angel One SmartAPI access layer: rate limiting, funds, quotes, candles, orders.

Every money figure this module returns comes from Angel One, never from local
arithmetic. The one exception is the display-only charge estimate in
``strategy.round_trip_charges``, used solely when Angel is unreachable at exit.
"""

from __future__ import annotations

import json
import math
import os
import time
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta

import pandas as pd

from engine.clock import now_ist
from engine.config import (
    FUNDS_CACHE_SEC,
    INDEX_EXCH,
    INDEX_TOKEN,
    LOCAL_IP,
    MAC_ADDR,
    POSITION_CACHE_SEC,
    PRODUCT_TYPE,
    PUBLIC_IP,
    RATE_LIMITS,
    SCRIP_CACHE,
    SCRIP_URL,
)


@dataclass
class Credentials:
    api_key: str
    client_id: str
    password: str
    totp_secret: str

    @staticmethod
    def from_env() -> "Credentials":
        need = {
            "ANGEL_API_KEY": "api_key",
            "ANGEL_CLIENT_ID": "client_id",
            "ANGEL_PASSWORD": "password",
            "ANGEL_TOTP_SECRET": "totp_secret",
        }
        vals, missing = {}, []
        for var, fld in need.items():
            v = os.environ.get(var, "").strip()
            if not v:
                missing.append(var)
            vals[fld] = v
        if missing:
            raise RuntimeError(
                f"Missing environment variables: {', '.join(missing)}. "
                "They are never stored in source."
            )
        return Credentials(**vals)

    def redacted(self) -> dict:
        return {"client_id": self.client_id, "api_key": self.api_key[:4] + "…"}


class RateLimiter:
    def __init__(self, limits: dict | None = None):
        self.limits = dict(limits or RATE_LIMITS)
        self.last = {k: 0.0 for k in self.limits}
        self.count = {k: 0 for k in self.limits}
        self.throttled_until = {k: 0.0 for k in self.limits}
        self.waits = 0.0
        self.blocked = 0

    def acquire(self, key: str) -> None:
        cap = self.limits.get(key)
        if not cap:
            return
        now = time.time()
        if now < self.throttled_until[key]:
            wait = self.throttled_until[key] - now
            self.waits += wait
            self.blocked += 1
            time.sleep(wait)
            now = time.time()
        gap = 1.0 / cap
        elapsed = now - self.last[key]
        if elapsed < gap:
            wait = gap - elapsed
            self.waits += wait
            self.blocked += 1
            time.sleep(wait)
        self.last[key] = time.time()
        self.count[key] += 1

    def penalise(self, key: str, seconds: float = 2.0) -> None:
        self.throttled_until[key] = time.time() + seconds

    def stats(self) -> dict:
        return {
            "calls": dict(self.count),
            "total_calls": sum(self.count.values()),
            "waited_sec": round(self.waits, 1),
            "throttles": self.blocked,
        }


def is_rate_limited(resp) -> bool:
    txt = str(resp)[:300].lower()
    return ("rate" in txt and "limit" in txt) or "too many request" in txt or "access denied" in txt


def discover_public_ip(timeout: float = 5.0) -> str:
    """Angel's session headers want the outbound IP. Oracle VMs get theirs at boot."""
    if PUBLIC_IP:
        return PUBLIC_IP
    for url in ("https://api.ipify.org", "https://checkip.amazonaws.com"):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "meridian"})
            with urllib.request.urlopen(req, timeout=timeout) as r:  # nosec B310 - https pinned
                ip = r.read().decode().strip()
            if ip and len(ip) <= 45:
                return ip
        except Exception:
            continue
    return "127.0.0.1"


class Broker:
    """Thin, cached, rate-limited wrapper over SmartConnect."""

    def __init__(self, creds: Credentials, dry_run: bool = True, log=print):
        self.dry_run = dry_run
        self.log = log
        import pyotp
        from SmartApi import SmartConnect

        self.api = SmartConnect(api_key=creds.api_key)
        self.api._local_ip = LOCAL_IP
        self.api._public_ip = discover_public_ip()
        self.api._mac = MAC_ADDR
        sess = self.api.generateSession(
            creds.client_id, creds.password, pyotp.TOTP(creds.totp_secret).now()
        )
        if not (isinstance(sess, dict) and sess.get("status")):
            raise RuntimeError(f"Angel login failed: {str(sess)[:200]}")
        self.client_id = creds.client_id
        self.connected_at = time.time()
        self._bars: dict = {"until": 0.0, "df": None}
        self.rl = RateLimiter()
        self._funds: dict = {"until": 0.0, "v": None}
        self._pos: dict = {"until": 0.0, "v": None}
        self.last_error: str | None = None

    # ── account ──────────────────────────────────────────────────────────────
    def funds(self, force: bool = False) -> float | None:
        if not force and time.time() < self._funds["until"] and self._funds["v"] is not None:
            return self._funds["v"]
        self.rl.acquire("rms")
        r = self.api.rmsLimit() or {}
        if is_rate_limited(r):
            self.rl.penalise("rms")
            self.log("rmsLimit rate-limited — backing off", "warn")
            return self._funds["v"]
        d = r.get("data") or {}
        for k in ("availablecash", "net", "availableIntradayPayin"):
            if d.get(k) not in (None, ""):
                v = float(d[k])
                self._funds = {"until": time.time() + FUNDS_CACHE_SEC, "v": v}
                return v
        return None

    def realised_pnl(self, force: bool = False) -> float | None:
        if not force and time.time() < self._pos["until"] and self._pos["v"] is not None:
            return self._pos["v"]
        try:
            self.rl.acquire("position")
            r = self.api.position() or {}
            if is_rate_limited(r):
                self.rl.penalise("position")
                self.log("position() rate-limited — backing off", "warn")
                return self._pos["v"]
            rows = r.get("data") or []
            total = 0.0
            for p in rows:
                for k in ("realised", "realisedprofit", "pnl", "netvalue"):
                    v = p.get(k)
                    if v not in (None, ""):
                        total += float(v)
                        break
            self._pos = {"until": time.time() + POSITION_CACHE_SEC, "v": total}
            return total
        except Exception:
            return None

    def open_positions(self) -> list[dict]:
        """Raw Angel position book rows with non-zero net quantity."""
        try:
            self.rl.acquire("position")
            r = self.api.position() or {}
            rows = r.get("data") or []
            out = []
            for p in rows:
                try:
                    net_qty = int(float(p.get("netqty") or 0))
                except (TypeError, ValueError):
                    net_qty = 0
                if net_qty:
                    out.append(p)
            return out
        except Exception:
            return []

    def last_fill(self, tsym: str) -> float | None:
        try:
            self.rl.acquire("tradebook")
            r = self.api.tradeBook() or {}
            rows = r.get("data") or []
            fills = [t for t in rows if str(t.get("tradingsymbol", "")) == tsym]
            if not fills:
                return None
            last = fills[-1]
            px = last.get("fillprice") or last.get("tradeprice") or last.get("price")
            if px in (None, ""):
                return None
            v = float(px)
            return v if (v > 0 and math.isfinite(v)) else None
        except Exception:
            return None

    # ── market data ──────────────────────────────────────────────────────────
    def ltp(self, exch: str, tsym: str, token: str) -> float | None:
        try:
            self.rl.acquire("ltp")
            r = self.api.ltpData(exch, tsym, str(token)) or {}
            if is_rate_limited(r):
                self.rl.penalise("ltp")
                return None
            v = (r.get("data") or {}).get("ltp")
            f = float(v) if v is not None else None
            return f if (f and math.isfinite(f) and f > 0) else None
        except Exception:
            return None

    def one_min_bars(self):
        if time.time() < self._bars["until"]:
            return self._bars["df"]
        t = now_ist()
        p = {
            "exchange": INDEX_EXCH,
            "symboltoken": INDEX_TOKEN,
            "interval": "ONE_MINUTE",
            "fromdate": (t - timedelta(days=4)).strftime("%Y-%m-%d %H:%M"),
            "todate": t.strftime("%Y-%m-%d %H:%M"),
        }
        self.rl.acquire("candle")
        try:
            resp = self.api.getCandleData(p) or {}
        except Exception as e:
            self.log(f"candle feed error: {str(e)[:120]}", "warn")
            self._bars["until"] = time.time() + 120
            return self._bars["df"]
        rows = (resp.get("data") if isinstance(resp, dict) else None) or []
        if not rows:
            self.log("candle feed empty — enable Historical Data API for this account", "warn")
            self._bars["until"] = time.time() + 120
            return self._bars["df"]
        df = pd.DataFrame(rows, columns=["ts", "o", "h", "l", "close", "v"])
        df["ts"] = pd.to_datetime(df["ts"]).dt.tz_localize(None)
        df["close"] = pd.to_numeric(df["close"], errors="coerce")
        df = df.dropna(subset=["close"]).sort_values("ts").reset_index(drop=True)
        self._bars = {"until": time.time() + 50, "df": df}
        return df

    def option_table(self) -> dict:
        rows = None
        if SCRIP_CACHE.exists() and time.time() - SCRIP_CACHE.stat().st_mtime < 12 * 3600:
            try:
                rows = json.loads(SCRIP_CACHE.read_text(encoding="utf-8"))
            except Exception:
                rows = None
        if rows is None:
            if not SCRIP_URL.startswith("https://"):
                raise RuntimeError("SCRIP_URL must be https")
            req = urllib.request.Request(SCRIP_URL, headers={"User-Agent": "meridian"})
            with urllib.request.urlopen(req, timeout=45) as r:  # nosec B310 - scheme pinned above
                rows = json.loads(r.read().decode("utf-8"))
            try:
                SCRIP_CACHE.write_text(json.dumps(rows), encoding="utf-8")
            except OSError:
                pass
        tbl = {}
        for r in rows:
            if str(r.get("exch_seg")) != "NFO" or str(r.get("name", "")).upper() != "NIFTY":
                continue
            if str(r.get("instrumenttype")) != "OPTIDX":
                continue
            sym = str(r.get("symbol", ""))
            right = sym[-2:]
            if right not in ("CE", "PE"):
                continue
            try:
                strike = int(round(float(r.get("strike", 0)) / 100.0))
                exp = datetime.strptime(str(r.get("expiry", "")).upper(), "%d%b%Y").date()
            except Exception:
                continue
            tbl[(exp, strike, right)] = (sym, str(r.get("token")))
        return tbl

    # ── orders ───────────────────────────────────────────────────────────────
    def order_status(self, order_id, tries: int = 6, wait: float = 1.5):
        for _ in range(tries):
            try:
                self.rl.acquire("orderbook")
                r = self.api.orderBook() or {}
                rows = r.get("data") or []
                for o in rows:
                    if str(o.get("orderid")) == str(order_id):
                        stat = str(o.get("status", "")).lower()
                        filled = float(o.get("filledshares") or 0)
                        avg = float(o.get("averageprice") or 0) or None
                        text = str(o.get("text") or "")
                        if stat in ("complete", "rejected", "cancelled"):
                            return stat, filled, avg, text
                        break
            except Exception as e:
                self.log(f"orderBook read failed: {str(e)[:100]}", "warn")
            time.sleep(wait)
        return "pending", 0.0, None, "did not reach a terminal state in time"

    def place(self, tsym: str, token: str, side: str, qty: int, product: str = PRODUCT_TYPE):
        if self.dry_run:
            self.log(f"PAPER — would {side} {qty} x {tsym}", "debug")
            return True, None, "paper"
        o = {
            "variety": "NORMAL",
            "tradingsymbol": tsym,
            "symboltoken": str(token),
            "transactiontype": side,
            "exchange": "NFO",
            "ordertype": "MARKET",
            "producttype": product,
            "duration": "DAY",
            "quantity": str(int(qty)),
            "price": "0",
            "squareoff": "0",
            "stoploss": "0",
        }
        self.log(f"ORDER >> {side} {tsym} x{qty}", "warn", extra={"params": o})
        try:
            self.rl.acquire("order")
            resp = self.api.placeOrder(o)
        except Exception as e:
            self.last_error = str(e)[:160]
            self.log(f"ORDER THREW: {side} {qty} {tsym} — {str(e)[:160]}", "error")
            return False, None, f"exception: {str(e)[:120]}"

        self.log(f"ORDER RESPONSE: {str(resp)[:300]}", "warn")
        order_id, msg = None, ""
        if isinstance(resp, dict):
            data = resp.get("data") or {}
            order_id = data.get("orderid") or resp.get("orderid")
            msg = str(resp.get("message") or "")
            if resp.get("status") is False or (msg and "success" not in msg.lower() and not order_id):
                self.log(f"ORDER REJECTED by Angel: {side} {qty} {tsym} — {msg[:160]}", "error")
                return False, None, f"rejected: {msg[:120]}"
        elif isinstance(resp, str):
            order_id = resp
        if not order_id:
            self.log(f"ORDER returned no order id: {side} {qty} {tsym}", "error")
            return False, None, "no order id returned"

        self.log(f"ORDER PLACED: id={order_id} — verifying fill…", "ok")
        stat, filled, avg, text = self.order_status(order_id)
        if stat == "complete" and filled > 0:
            self.log(
                f"ORDER FILLED  {side} {int(filled)} x {tsym} @ {avg if avg else '?'} (id {order_id})",
                "ok",
            )
            return True, avg, "complete"
        if stat == "rejected":
            self.log(f"ORDER REJECTED  {side} {qty} {tsym} — {text[:160]} (id {order_id})", "error")
            return False, None, f"rejected: {text[:120]}"
        self.log(
            f"ORDER NOT CONFIRMED  {side} {qty} {tsym} — status '{stat}' {text[:120]} (id {order_id})",
            "warn",
        )
        return False, None, f"{stat}: {text[:120]}"
