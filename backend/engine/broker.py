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
from collections import deque
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta

import pandas as pd

from engine.clock import now_ist
from engine.config import (
    DB_PATH,
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
    """Per-endpoint pacing against Angel's published caps.

    Cumulative counters answer "how much have we used today"; a gauge needs
    "how hard are we pushing right now", so recent call times are kept in a
    short rolling window and reported as a live rate against the cap.
    """

    WINDOW_SEC = 10.0

    def __init__(self, limits: dict | None = None, shared=None):
        self.limits = dict(limits or RATE_LIMITS)
        # The account-wide budget. Angel's caps are per API key, so with several
        # engines running, pacing only this process would still present the sum
        # of them to Angel.
        self.shared = shared
        self.last = {k: 0.0 for k in self.limits}
        self.count = {k: 0 for k in self.limits}
        self.throttled_until = {k: 0.0 for k in self.limits}
        self.recent: dict[str, deque] = {k: deque() for k in self.limits}
        self.throttle_count = {k: 0 for k in self.limits}
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

        # Then clear it with the account, which every other engine also draws on.
        if self.shared is not None:
            shared_wait = self.shared.acquire(key, cap)
            if shared_wait:
                self.waits += shared_wait
                self.blocked += 1
                self.shared_waits = getattr(self, "shared_waits", 0.0) + shared_wait
        stamped = time.time()
        self.last[key] = stamped
        self.count[key] += 1
        self.recent[key].append(stamped)
        self._trim(key, stamped)

    def penalise(self, key: str, seconds: float = 2.0) -> None:
        self.throttled_until[key] = time.time() + seconds
        self.throttle_count[key] = self.throttle_count.get(key, 0) + 1

    def _trim(self, key: str, now: float) -> None:
        window = self.recent[key]
        cutoff = now - self.WINDOW_SEC
        while window and window[0] < cutoff:
            window.popleft()

    def rate(self, key: str, now: float | None = None) -> float:
        """Calls per second over the rolling window."""
        now = time.time() if now is None else now
        self._trim(key, now)
        return len(self.recent[key]) / self.WINDOW_SEC

    def stats(self) -> dict:
        now = time.time()
        endpoints = []
        for key, cap in sorted(self.limits.items()):
            used = self.rate(key, now)
            endpoints.append(
                {
                    "endpoint": key,
                    "cap_per_sec": cap,
                    "rate_per_sec": round(used, 2),
                    "utilisation": round(min(used / cap, 1.0), 3) if cap else 0.0,
                    "calls": self.count.get(key, 0),
                    "throttled": self.throttle_count.get(key, 0),
                    "cooling_off": now < self.throttled_until.get(key, 0.0),
                    "account_calls_this_second": (
                        self.shared.usage(key) if self.shared is not None else None
                    ),
                }
            )
        return {
            "calls": dict(self.count),
            "total_calls": sum(self.count.values()),
            "waited_sec": round(self.waits, 1),
            "throttles": self.blocked,
            "window_sec": self.WINDOW_SEC,
            "shared_budget": self.shared is not None,
            "shared_waited_sec": round(getattr(self, "shared_waits", 0.0), 1),
            "endpoints": endpoints,
            # The endpoint closest to its cap is what actually limits the loop.
            "peak_utilisation": round(
                max((e["utilisation"] for e in endpoints), default=0.0), 3
            ),
        }


def is_rate_limited(resp) -> bool:
    txt = str(resp)[:300].lower()
    return ("rate" in txt and "limit" in txt) or "too many request" in txt or "access denied" in txt


TERMINAL_ORDER_STATES = ("complete", "rejected", "cancelled")


def _net_qty(row: dict) -> int:
    try:
        return int(float(row.get("netqty") or 0))
    except (TypeError, ValueError):
        return 0


def real_public_ip(timeout: float = 5.0) -> str | None:
    """The address this machine's traffic actually leaves from, or None."""
    for url in ("https://api.ipify.org", "https://checkip.amazonaws.com"):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "meridian"})
            with urllib.request.urlopen(req, timeout=timeout) as r:  # nosec B310 - https pinned
                ip = r.read().decode().strip()
            if ip and len(ip) <= 45:
                return ip
        except Exception:
            continue
    return None


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
        # Every engine on this machine shares one account budget, held in the
        # same database they already use as a bus.
        try:
            from shared.ratelimit import SharedRateLimiter

            shared = SharedRateLimiter(DB_PATH)
        except Exception:  # a limiter that cannot start must not stop trading
            shared = None
        self.rl = RateLimiter(shared=shared)
        self._funds: dict = {"until": 0.0, "v": None}
        self._pos: dict = {"until": 0.0, "v": None}
        self.last_error: str | None = None
        self.last_order_id: str | None = None

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

    def _position_rows(self) -> list[dict] | None:
        """Angel's position book.

        None means the book could not be read, and callers must never treat it
        as "nothing held". Mistaking a failed read for an empty book is how an
        engine forgets a live position after a restart — no stop, no 15:10
        close, and on CARRYFORWARD it rolls into tomorrow.
        """
        try:
            self.rl.acquire("position")
            r = self.api.position()
        except Exception as e:
            self.log(f"position book read failed: {str(e)[:100]}", "warn")
            return None
        if is_rate_limited(r):
            self.rl.penalise("position")
            self.log("position book rate-limited — could not read it", "warn")
            return None
        if not (isinstance(r, dict) and r.get("status")):
            return None
        # A successful read with no positions comes back as data: null.
        return r.get("data") or []

    def open_positions(self) -> list[dict] | None:
        """Position rows with a non-zero net quantity, or None if unreadable."""
        rows = self._position_rows()
        if rows is None:
            return None
        return [p for p in rows if _net_qty(p)]

    def net_qty(self, tsym: str, token: str | None = None) -> int | None:
        """What Angel says is held in one contract right now, or None if unreadable."""
        rows = self._position_rows()
        if rows is None:
            return None
        held = 0
        for p in rows:
            if str(p.get("tradingsymbol", "")) == tsym or (
                token and str(p.get("symboltoken", "")) == str(token)
            ):
                held += _net_qty(p)
        return held

    def cancel_open(self, tsym: str) -> int | None:
        """Cancel every still-working order on this contract.

        Run before any retry, so a slow order that eventually fills cannot be
        joined by its replacement. Returns how many were cancelled, or None if
        the order book could not be read — in which case nothing is certain and
        the caller must not send another order.
        """
        if self.dry_run:
            return 0
        try:
            self.rl.acquire("orderbook")
            r = self.api.orderBook()
        except Exception as e:
            self.log(f"order book read failed: {str(e)[:100]}", "warn")
            return None
        if not (isinstance(r, dict) and r.get("status")):
            return None
        n = 0
        for o in r.get("data") or []:
            if str(o.get("tradingsymbol", "")) != tsym:
                continue
            if str(o.get("status", "")).lower() in TERMINAL_ORDER_STATES:
                continue
            try:
                self.rl.acquire("order")
                self.api.cancelOrder(str(o.get("orderid")), str(o.get("variety") or "NORMAL"))
                n += 1
                self.log(f"cancelled working order {o.get('orderid')} on {tsym} before retrying", "warn")
            except Exception as e:
                self.log(f"cancel failed for order {o.get('orderid')}: {str(e)[:100]}", "error")
                return None
        return n

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
            try:
                lot = int(float(r.get("lotsize") or 0))
            except (TypeError, ValueError):
                lot = 0
            if lot <= 0:
                continue  # unusable contract; a guessed lot size places a wrong-sized order
            tbl[(exp, strike, right)] = (sym, str(r.get("token")), lot)
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
                        if stat in TERMINAL_ORDER_STATES:
                            return stat, filled, avg, text
                        break
            except Exception as e:
                self.log(f"orderBook read failed: {str(e)[:100]}", "warn")
            time.sleep(wait)
        return "pending", 0.0, None, "did not reach a terminal state in time"

    def place(self, tsym: str, token: str, side: str, qty: int, product: str = PRODUCT_TYPE):
        self.last_order_id = None
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
                if "AG7002" in str(resp) or "static ip" in str(resp).lower():
                    self.log(
                        f"ORDER REJECTED — this machine's IP {self.api._public_ip} is not the one "
                        f"whitelisted for your SmartAPI app. Register it at smartapi.angelone.in "
                        f"(My Apps → edit app) and restart. {msg[:120]}",
                        "critical",
                    )
                self.log(f"ORDER REJECTED by Angel: {side} {qty} {tsym} — {msg[:160]}", "error")
                return False, None, f"rejected: {msg[:120]}"
        elif isinstance(resp, str):
            order_id = resp
        if not order_id:
            self.log(f"ORDER returned no order id: {side} {qty} {tsym}", "error")
            return False, None, "no order id returned"

        self.last_order_id = str(order_id)
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
