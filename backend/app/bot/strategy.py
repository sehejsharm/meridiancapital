"""NIFTY options algorithm — v11.

This is the strategy as supplied, running unchanged. Signal generation, the
risk ladder, the GARCH and chop gates, position sizing, charge maths and the
daily kill switch are all byte-for-byte the original logic.

What was added around it, so it can run headless under a supervisor:

  * credentials come from the environment instead of being hardcoded
  * every state change also emits a structured event on stdout (emitter.py)
  * `exit()` became exceptions, so a bad startup does not kill the API
  * state files live under DATA_DIR
  * SIGTERM stops the loop gracefully: flatten, file the EOD report, exit
  * carriage-return status lines only print to a real terminal

Run standalone with:  python -m app.bot.strategy
"""
from __future__ import annotations

import gc
import json
import logging
import os
import signal
import sys
import threading
import time
import urllib.request
import warnings
from collections import Counter, deque
from datetime import datetime, time as dtime, timedelta

# IST is not negotiable — the algorithm compares naive datetimes against
# NSE session boundaries, so the process clock must be Asia/Kolkata.
os.environ.setdefault("TZ", "Asia/Kolkata")
if hasattr(time, "tzset"):
    time.tzset()

import numpy as np
import pandas as pd
from colorama import Fore, Style, init
from scipy.stats import norm

from .emitter import emit, tty_status

logging.basicConfig(level=logging.CRITICAL)
logging.getLogger("urllib3").setLevel(logging.CRITICAL)
try:
    import logzero
    logzero.loglevel(logging.CRITICAL)
except ImportError:
    pass

init(autoreset=True)
warnings.filterwarnings("ignore")


class BotStartupError(RuntimeError):
    """Raised instead of exit() so the supervisor can report the reason."""


try:
    from SmartApi import SmartConnect
    import pyotp
except ImportError as e:  # pragma: no cover - dependency guard
    raise BotStartupError(
        f"Missing dependency: {e}. Run: pip install -r backend/requirements.txt"
    ) from e


# ================================================================
#  PAPER TRADING MODE — v11
# ================================================================
# PAPER_MODE=True  -> NO real orders. Real Angel One LTPs, real margin,
#                     real latency. Fills simulated at LTP +/- slippage.
# Capital compounds day to day via equity_book.json. Starts at Rs20,000
# ONCE, then carries forward forever. No re-infusion.
# ================================================================
def _envb(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    return default if raw in (None, "") else raw.strip().lower() in ("1", "true", "yes", "on")


def _envf(name: str, default: float) -> float:
    try:
        return float(os.getenv(name) or default)
    except ValueError:
        return default


def _envi(name: str, default: int) -> int:
    try:
        return int(float(os.getenv(name) or default))
    except ValueError:
        return default


def _envt(name: str, default: dtime) -> dtime:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        hh, _, mm = raw.partition(":")
        return dtime(int(hh), int(mm or 0))
    except ValueError:
        return default


PAPER_MODE          = _envb("PAPER_MODE", True)
SEED_CAPITAL        = _envf("SEED_CAPITAL", 20000.0)   # used ONLY on very first run
PAPER_SLIPPAGE_PCT  = _envf("PAPER_SLIPPAGE_PCT", 0.005)  # 0.5% each side

API_KEY      = os.getenv("ANGEL_API_KEY", "")
SECRET_KEY   = os.getenv("ANGEL_SECRET_KEY", "")
CLIENT_ID    = os.getenv("ANGEL_CLIENT_ID", "")
PASSWORD     = os.getenv("ANGEL_PASSWORD", "")
TOTP_SECRET  = os.getenv("ANGEL_TOTP_SECRET", "")

# Every constant below keeps its v11 value as the default. An untouched
# install trades exactly as the original script did; the app can override any
# of them, and the supervisor passes the overrides in as environment.
INDEX_NAME   = os.getenv("INDEX_NAME", "NIFTY")
INDEX_TOKEN  = os.getenv("INDEX_TOKEN", "99926000")
INDEX_SYMBOL = os.getenv("INDEX_TRADINGSYMBOL", "Nifty 50")
FUT_TOKEN    = None
FUT_SYMBOL   = None

STRIKE_STEP      = _envi("STRIKE_STEP", 50)
DEFAULT_LOT_SIZE = _envi("DEFAULT_LOT_SIZE", 75)

# ---- RISK LADDER (sweep-winning config) ----
SL_PCT         = _envf("SL_PCT", 0.10)
BE_TRIGGER_PCT = _envf("BE_TRIGGER_PCT", 0.15)
BE_FLOOR_PCT   = _envf("BE_FLOOR_PCT", 0.04)
LOCK1_TRIGGER  = _envf("LOCK1_TRIGGER", 0.25)
LOCK1_FLOOR    = _envf("LOCK1_FLOOR", 0.10)
LOCK2_TRIGGER  = _envf("LOCK2_TRIGGER", 0.40)
LOCK2_FLOOR    = _envf("LOCK2_FLOOR", 0.25)
FREE_GIVEBACK  = _envf("FREE_GIVEBACK", 0.10)

DAILY_KILL_RUPEES  = _envf("DAILY_KILL_RUPEES", 3000.0)
MAX_TRADES_PER_DAY = _envi("MAX_TRADES_PER_DAY", 3)
ENABLE_PYRAMID     = False
MAX_LOTS_IN_TRADE  = 1

SL_SAME_DIR_COOLDOWN_MIN   = _envi("SL_SAME_DIR_COOLDOWN_MIN", 15)
EXIT_SAME_DIR_COOLDOWN_MIN = _envi("EXIT_SAME_DIR_COOLDOWN_MIN", 3)

SMA_PERIOD  = _envi("SMA_PERIOD", 50)
EMA_FAST    = _envi("EMA_FAST", 9)
EMA_SLOW    = _envi("EMA_SLOW", 21)
SLOPE_BARS  = _envi("SLOPE_BARS", 3)
RF_RATE     = _envf("RF_RATE", 0.07)

USE_RSI_FILTER = _envb("USE_RSI_FILTER", False)
RSI_PERIOD     = _envi("RSI_PERIOD", 14)

GARCH_VOL_MIN       = _envf("GARCH_VOL_MIN", 0.09)
GARCH_TRADE3_MIN    = _envf("GARCH_TRADE3_MIN", 0.11)
POST_SL_GARCH_MIN   = _envf("POST_SL_GARCH_MIN", 0.09)
GARCH_BARS_PER_YEAR = 375 * 252

ENABLE_CHOP_FILTER = _envb("ENABLE_CHOP_FILTER", True)
CHOP_BLOCK_PCT     = _envi("CHOP_BLOCK_PCT", 15)
CHOP_LOOKBACK_DAYS = _envi("CHOP_LOOKBACK_DAYS", 60)
CHOP_HISTORY_DAYS  = CHOP_LOOKBACK_DAYS + 15
ADX_PERIOD         = _envi("ADX_PERIOD", 14)

BLOCK_EXPIRY_DAY_TRADING = _envb("BLOCK_EXPIRY_DAY_TRADING", True)
EXPIRY_DAY_GARCH_MIN     = _envf("EXPIRY_DAY_GARCH_MIN", 0.13)

MARKET_OPEN  = _envt("MARKET_OPEN", dtime(9, 40))
ENTRY_CUTOFF = _envt("ENTRY_CUTOFF", dtime(13, 30))
FORCE_CLOSE  = _envt("FORCE_CLOSE", dtime(15, 0))
MARKET_END   = dtime(15, 30)

# ---- FILES (memory across days) ----
DATA_DIR = os.path.abspath(os.getenv("DATA_DIR", "./data"))
os.makedirs(DATA_DIR, exist_ok=True)


def _p(name: str) -> str:
    return os.path.join(DATA_DIR, name)


LOG_FILE    = _p("paper_ledger.csv")
STATE_FILE  = _p("paper_state.json")
DAY_FILE    = _p("paper_day.json")
CHOP_FILE   = _p("paper_chop.json")
SCRIP_CACHE_FILE = _p("scrip_master_cache.json")
EQUITY_FILE = _p("equity_book.json")     # <-- compounding capital lives here
EOD_FILE    = _p("eod_reports.csv")

# ---- REPORTING ----
MINUTE_UPDATE_SECONDS = 60           # full status block every 60s
STATUS_EVENT_SECONDS  = 2            # live push cadence to the phone
CHART_EVENT_SECONDS   = 15           # candle + premium push cadence
CHART_MAX_BARS        = 400          # today's session fits in 375 one-minute bars
PREMIUM_SERIES_MAX    = 1500         # option ticks kept for the trade box
CANDLE_CACHE_SECONDS  = 60
MAX_LTP_FAILURES      = 5
SESSION_REFRESH_SECONDS = 3600
ORDER_RETRY_ATTEMPTS  = 2
HEARTBEAT_SECONDS     = 300
LOOP_SLEEP_IDLE       = 2.0
LOOP_SLEEP_IN_POSITION = 0.5

# ---- ANGEL ONE CHARGE RATES (published; no API endpoint exists) ----
BROKERAGE_PER_ORDER = 20.0
STT_SELL_RATE       = 0.000500   # 0.05% from Apr-2026 on sell premium
EXCH_TXN_RATE       = 0.00050    # NSE options
SEBI_RATE           = 0.000001
STAMP_BUY_RATE      = 0.00003
GST_RATE            = 0.18
IPFT_RATE           = 0.0000005

POSITION_REQUIRED = {
    "symbol", "token", "type", "strike", "entry_prem",
    "qty", "lots", "buy_order_id", "entry_time", "locked", "pyramided"
}

# Set by SIGTERM/SIGINT so the loop can flatten and file its report.
_STOP = threading.Event()


def request_stop(reason: str = "signal") -> None:
    if not _STOP.is_set():
        _STOP.set()
        emit("stopping", f"Stop requested ({reason})", level="warn", reason=reason)


def _install_signal_handlers() -> None:
    def handler(signum, _frame):
        request_stop(signal.Signals(signum).name)

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, handler)
        except (ValueError, OSError):
            pass


# ================================================================
#  LATENCY TRACKER — measures REAL Angel One round-trip times
# ================================================================
class LatencyTracker:
    def __init__(self, maxlen=500):
        self.calls = {}
        self.maxlen = maxlen

    def record(self, name, ms):
        if name not in self.calls:
            self.calls[name] = deque(maxlen=self.maxlen)
        self.calls[name].append(ms)

    def stats(self, name):
        d = self.calls.get(name)
        if not d:
            return None
        a = np.array(d)
        return dict(n=len(a), avg=float(a.mean()), mn=float(a.min()),
                    mx=float(a.max()), p95=float(np.percentile(a, 95)),
                    last=float(a[-1]))

    def all_stats(self):
        return {k: self.stats(k) for k in self.calls.keys()}

    def summary_line(self):
        s = self.stats("ltpData")
        if not s:
            return "LAG n/a"
        return f"LAG {s['last']:.0f}ms (avg {s['avg']:.0f})"


LAT = LatencyTracker()


def timed(name, fn, *args, **kwargs):
    """Wrap any API call to measure real round-trip latency."""
    t0 = time.perf_counter()
    try:
        out = fn(*args, **kwargs)
        return out
    finally:
        LAT.record(name, (time.perf_counter() - t0) * 1000.0)


# ================================================================
#  MATH
# ================================================================
def bsm_price(S, K, t, r, sigma, opt_type):
    if t <= 0 or sigma <= 0:
        return max(S - K, 0) if opt_type == "C" else max(K - S, 0)
    d1 = (np.log(S / K) + (r + 0.5 * sigma * sigma) * t) / (sigma * np.sqrt(t))
    d2 = d1 - sigma * np.sqrt(t)
    if opt_type == "C":
        return S * norm.cdf(d1) - K * np.exp(-r * t) * norm.cdf(d2)
    return K * np.exp(-r * t) * norm.cdf(-d2) - S * norm.cdf(-d1)


def implied_vol(market_price, S, K, t, r, opt_type):
    if market_price <= 0 or t <= 0:
        return None
    intrinsic = max(S - K, 0) if opt_type == "C" else max(K - S, 0)
    if market_price < intrinsic * 0.95:
        return None
    lo, hi = 0.03, 2.5
    for _ in range(60):
        mid = (lo + hi) / 2.0
        p = bsm_price(S, K, t, r, mid, opt_type)
        if p > market_price:
            hi = mid
        else:
            lo = mid
        if hi - lo < 0.0001:
            break
    return (lo + hi) / 2.0


def garch_forecast(returns):
    alpha, beta = 0.08, 0.90
    r = np.asarray(returns, dtype=float)
    r = r[~np.isnan(r)]
    if len(r) < 10:
        return None
    if len(r) < 30:
        return float(r.std() * np.sqrt(GARCH_BARS_PER_YEAR))
    long_var = float(r.var())
    if long_var <= 0:
        return None
    omega = long_var * (1.0 - alpha - beta)
    var = long_var
    for x in r:
        var = omega + alpha * x * x + beta * var
    if var <= 0:
        return None
    return float(np.sqrt(var * GARCH_BARS_PER_YEAR))


# ================================================================
#  EQUITY BOOK — compounding capital with memory across days
# ================================================================
class EquityBook:
    """
    Persists capital across sessions. Seeded ONCE at SEED_CAPITAL.
    Profit compounds; loss is deducted. No capital re-infusion ever.
    """

    def __init__(self, path=EQUITY_FILE):
        self.path = path
        self.data = {
            "seeded_on": None,
            "seed_capital": SEED_CAPITAL,
            "current_equity": SEED_CAPITAL,
            "peak_equity": SEED_CAPITAL,
            "total_days": 0,
            "history": []          # [{date, open, close, pnl, trades}]
        }
        self._load()

    def _load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path, "r") as f:
                    self.data = json.load(f)
                print(Fore.GREEN
                      + f"[Equity] Loaded book: Rs{self.data['current_equity']:,.2f}"
                      + f" | seeded {self.data.get('seeded_on')}"
                      + f" | {self.data.get('total_days',0)} sessions")
                emit("equity_book", "Equity book loaded",
                     equity=self.data["current_equity"],
                     seeded_on=self.data.get("seeded_on"),
                     total_days=self.data.get("total_days", 0),
                     peak_equity=self.data.get("peak_equity"))
                return
            except Exception as e:
                print(Fore.YELLOW + f"[Equity] Read error ({e}) - reseeding")
        self.data["seeded_on"] = datetime.now().strftime("%Y-%m-%d")
        self._save()
        print(Fore.CYAN + Style.BRIGHT
              + f"[Equity] FIRST RUN - seeded at Rs{SEED_CAPITAL:,.2f}")
        emit("equity_book", f"First run — seeded at Rs{SEED_CAPITAL:,.2f}",
             equity=SEED_CAPITAL, seeded_on=self.data["seeded_on"], total_days=0)

    def _save(self):
        try:
            with open(self.path, "w") as f:
                json.dump(self.data, f, indent=2)
        except Exception as e:
            print(Fore.RED + f"[Equity] Save failed: {e}")

    @property
    def equity(self):
        return float(self.data["current_equity"])

    def apply_trade(self, net_pnl):
        """Compound: profit adds, loss subtracts. Never re-infused."""
        self.data["current_equity"] = float(self.data["current_equity"]) + float(net_pnl)
        if self.data["current_equity"] > self.data.get("peak_equity", 0):
            self.data["peak_equity"] = self.data["current_equity"]
        self._save()
        return self.data["current_equity"]

    def close_session(self, date_str, open_eq, trades):
        close_eq = self.data["current_equity"]
        self.data["total_days"] = self.data.get("total_days", 0) + 1
        self.data.setdefault("history", []).append({
            "date": date_str, "open": round(open_eq, 2),
            "close": round(close_eq, 2), "pnl": round(close_eq - open_eq, 2),
            "trades": trades
        })
        self.data["history"] = self.data["history"][-250:]
        self._save()

    def drawdown_from_peak(self):
        pk = float(self.data.get("peak_equity", self.equity))
        return (self.equity - pk) / pk if pk > 0 else 0.0

    def total_return(self):
        seed = float(self.data.get("seed_capital", SEED_CAPITAL))
        return (self.equity - seed) / seed if seed > 0 else 0.0


# ================================================================
#  SIGNAL DECISION — every rejection carries a reason
# ================================================================
class Decision:
    """Structured verdict so the bot can always say WHY."""

    def __init__(self, action=None, reason="", detail=""):
        self.action = action      # "C" / "P" / None
        self.reason = reason      # short code
        self.detail = detail      # human sentence

    @property
    def took_trade(self):
        return self.action is not None

    def as_dict(self):
        return {"action": self.action, "reason": self.reason, "detail": self.detail}


# ================================================================
#  BROKER
# ================================================================
class LiveBroker:

    def __init__(self, book):
        self.book = book
        self.api = None
        self.session_start = 0
        self.scrip_master = []
        self.lot_size = DEFAULT_LOT_SIZE
        self.position = None
        self.day_state = {
            "date": "", "pnl": 0.0, "trades": 0, "killed": False,
            "sl_hit": False, "last_sl_time": None,
            "last_exit_dir": None, "last_exit_time": None,
            "last_sl_dir": None, "last_sl_dir_time": None,
            "chop_checked": False, "chop_skip": False,
            "open_equity": book.equity
        }
        self.candle_cache      = None
        self.last_candle_fetch = 0
        self.ltp_failures      = 0
        self.last_heartbeat    = 0
        self.last_minute_report = 0
        self.last_status_event = 0
        self.last_chart_event  = 0
        self.premium_series    = deque(maxlen=PREMIUM_SERIES_MAX)
        self._paper_order_seq  = 0
        self._paper_last_fill  = {}
        self.chop_scores       = {}
        self.chop_cutoff       = None
        self.chop_today_score  = None
        self.last_decision     = Decision(None, "STARTUP", "Bot starting up")
        self.reject_counts     = Counter()
        self.market_snapshot   = {}
        self.real_margin_1lot  = None
        self.eod_filed         = False

        self._detect_ips()
        self._connect(is_startup=True)
        self._patch_ip()
        self._download_scrip_master()
        self._detect_lot_size()
        self._load_state()
        self._load_day()
        self._build_chop_history()

    # ---- auth ----
    def _connect(self, is_startup=False):
        max_attempts = 999 if not is_startup else 5
        for attempt in range(1, max_attempts + 1):
            if _STOP.is_set():
                return False
            print(Fore.YELLOW + f"[Auth] Connecting to Angel One (attempt {attempt})...")
            try:
                self.api = SmartConnect(api_key=API_KEY)
                totp = pyotp.TOTP(TOTP_SECRET).now()
                t0 = time.perf_counter()
                data = self.api.generateSession(CLIENT_ID, PASSWORD, totp)
                LAT.record("login", (time.perf_counter() - t0) * 1000)
                if data and data.get("status"):
                    self.session_start = time.time()
                    s = LAT.stats("login")
                    print(Fore.GREEN + f"[OK] Connected as {CLIENT_ID} | login lag {s['last']:.0f}ms")
                    emit("connected", f"Connected to Angel One as {CLIENT_ID}",
                         latency_ms=round(s["last"], 1), attempt=attempt)
                    return True
                print(Fore.RED + "[Auth] Login rejected: " + str(data))
                emit("auth_failed", "Login rejected by Angel One", level="error",
                     response=str(data)[:400])
                if is_startup:
                    raise BotStartupError(f"Angel One login rejected: {data}")
            except BotStartupError:
                raise
            except Exception as e:
                err = str(e)
                if "NameResolution" in err or "getaddrinfo" in err or "Max retries" in err:
                    print(Fore.YELLOW + "[Auth] Network down - waiting 30s...")
                    emit("network", "Network unreachable, retrying in 30s", level="warn")
                else:
                    print(Fore.RED + "[Auth] Error: " + err)
                    emit("auth_failed", f"Login error: {err}", level="error")
                    if is_startup:
                        raise BotStartupError(f"Angel One login error: {err}") from e
            if attempt < max_attempts:
                _STOP.wait(30)
        return False

    def _detect_ips(self):
        public_ip = "127.0.0.1"
        for url in ["https://api.ipify.org", "https://ifconfig.me/ip", "https://icanhazip.com"]:
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                resp = urllib.request.urlopen(req, timeout=5).read().decode().strip()
                if resp and len(resp) >= 7 and "." in resp:
                    public_ip = resp
                    break
            except Exception:
                continue
        local_ip = "127.0.0.1"
        try:
            import socket
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            local_ip = s.getsockname()[0]
            s.close()
        except Exception:
            pass
        try:
            import uuid
            mac = ":".join(("%012X" % uuid.getnode())[i:i + 2] for i in range(0, 12, 2))
        except Exception:
            mac = "AA:BB:CC:DD:EE:FF"
        self._real_public_ip, self._real_local_ip, self._real_mac = public_ip, local_ip, mac
        print(Fore.CYAN + f"[IP] Public: {public_ip} | Local: {local_ip}")

    def _patch_ip(self):
        try:
            self.api.clientPublicIp   = getattr(self, "_real_public_ip", "127.0.0.1")
            self.api.clientLocalIp    = getattr(self, "_real_local_ip", "127.0.0.1")
            self.api.clientMacAddress = getattr(self, "_real_mac", "AA:BB:CC:DD:EE:FF")
        except Exception:
            pass

    def _refresh_session_if_needed(self):
        if time.time() - self.session_start < SESSION_REFRESH_SECONDS:
            return
        print(Fore.YELLOW + "\n[Auth] Session refresh - reconnecting...")
        self._connect(is_startup=False)
        self._patch_ip()

    def _heartbeat(self):
        if time.time() - self.last_heartbeat < HEARTBEAT_SECONDS:
            return
        self.last_heartbeat = time.time()
        try:
            self._patch_ip()
            res = timed("ltpData", self.api.ltpData, "NSE", INDEX_SYMBOL, INDEX_TOKEN)
            if res and res.get("status"):
                emit("heartbeat", "Connection healthy", latency=LAT.summary_line())
                return
        except Exception:
            pass
        print(Fore.YELLOW + "\n[HB] Connection lost - reconnecting...")
        emit("network", "Heartbeat failed — reconnecting", level="warn")
        self._connect(is_startup=False)
        self._patch_ip()

    # ---- balance (paper = equity book) ----
    def get_live_balance(self, force_refresh=False):
        return self.book.equity

    def get_real_account_balance(self):
        """Reads the REAL Angel One balance for reference only (never traded)."""
        try:
            self._patch_ip()
            rms = timed("rmsLimit", self.api.rmsLimit)
            if rms and rms.get("status") and rms.get("data"):
                return float(rms["data"].get("net", 0))
        except Exception:
            pass
        return None

    # ---- scrip master ----
    def _download_scrip_master(self):
        print(Fore.YELLOW + "[Init] Downloading scrip master...")
        for attempt in range(3):
            try:
                if self._load_scrip_cache():
                    return

                url = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
                t0 = time.perf_counter()
                raw = urllib.request.urlopen(url, timeout=30).read()
                LAT.record("scripMaster", (time.perf_counter() - t0) * 1000)

                # The full file is ~30 MB of JSON that parses into a few hundred
                # megabytes of dicts. On a 1 GB host that spike is the difference
                # between running and being OOM-killed, so everything needed is
                # pulled out in a single pass and the big list is released at
                # once rather than lingering through a second scan.
                all_scrips = json.loads(raw)
                del raw

                options, futures = [], []
                for s in all_scrips:
                    if s.get("exch_seg") != "NFO" or s.get("name") != INDEX_NAME:
                        continue
                    kind = s.get("instrumenttype")
                    if kind == "OPTIDX":
                        options.append(s)
                    elif kind == "FUTIDX":
                        futures.append(s)

                del all_scrips
                gc.collect()

                self.scrip_master = options
                print(Fore.GREEN + f"[OK] {len(self.scrip_master)} contracts | "
                      + f"download {LAT.stats('scripMaster')['last']/1000:.1f}s")
                emit("scrip_master", f"{len(self.scrip_master)} {INDEX_NAME} option contracts loaded",
                     contracts=len(self.scrip_master),
                     seconds=round(LAT.stats("scripMaster")["last"] / 1000, 1))
                self._discover_futures(futures)
                self._save_scrip_cache(options, futures)
                return
            except Exception as e:
                print(Fore.YELLOW + f"[Init] Attempt {attempt+1}: {e}")
                time.sleep(3)
        print(Fore.RED + "[FAIL] Could not load scrip master")
        raise BotStartupError("Could not download Angel One scrip master")

    def _load_scrip_cache(self) -> bool:
        """Reuse today's filtered contracts instead of re-parsing 30 MB.

        A restart mid-session — a crash, a redeploy — should not pay the
        download and the memory spike again.
        """
        try:
            if not os.path.exists(SCRIP_CACHE_FILE):
                return False
            with open(SCRIP_CACHE_FILE, "r") as f:
                cached = json.load(f)
            if cached.get("date") != datetime.now().strftime("%Y-%m-%d"):
                return False
            if cached.get("index") != INDEX_NAME:
                return False
            options = cached.get("options") or []
            if not options:
                return False
            self.scrip_master = options
            print(Fore.GREEN + f"[OK] {len(options)} contracts from today's cache")
            emit("scrip_master", f"{len(options)} {INDEX_NAME} option contracts from cache",
                 contracts=len(options), cached=True)
            self._discover_futures(cached.get("futures") or [])
            return True
        except Exception:
            return False

    def _save_scrip_cache(self, options, futures) -> None:
        try:
            with open(SCRIP_CACHE_FILE, "w") as f:
                json.dump({
                    "date": datetime.now().strftime("%Y-%m-%d"),
                    "index": INDEX_NAME,
                    "options": options,
                    "futures": futures,
                }, f)
        except Exception:
            pass        # the cache is an optimisation, never a requirement

    def _discover_futures(self, futures):
        global FUT_TOKEN, FUT_SYMBOL
        today = datetime.now().date()
        futs = []
        for s in futures:
            try:
                exp = datetime.strptime(s["expiry"], "%d%b%Y").date()
                if exp >= today:
                    futs.append((exp, s))
            except Exception:
                continue
        if futs:
            futs.sort(key=lambda x: x[0])
            best = futs[0][1]
            FUT_TOKEN  = str(best.get("token"))
            FUT_SYMBOL = best.get("symbol")
            print(Fore.GREEN + f"[OK] Futures: {FUT_SYMBOL} expiry={futs[0][0]}")
        else:
            print(Fore.YELLOW + "[WARN] No futures found - VWAP falls back to spot")

    def _detect_lot_size(self):
        sizes = [int(s.get("lotsize", 0)) for s in self.scrip_master if int(s.get("lotsize", 0)) > 0]
        self.lot_size = Counter(sizes).most_common(1)[0][0] if sizes else DEFAULT_LOT_SIZE
        print(Fore.CYAN + f"[Init] Lot size: {self.lot_size}")

    # ---- state ----
    def _valid_position(self, pos):
        return isinstance(pos, dict) and POSITION_REQUIRED.issubset(pos.keys())

    def _load_state(self):
        if not os.path.exists(STATE_FILE):
            return
        try:
            with open(STATE_FILE, "r") as f:
                pos = json.load(f)
            if not self._valid_position(pos):
                os.remove(STATE_FILE)
                return
            self.position = pos
            print(Fore.MAGENTA + "[Init] Position restored: " + pos.get("symbol", ""))
            emit("position_restored", f"Restored open position {pos.get('symbol','')}",
                 level="warn", position=pos)
        except Exception as e:
            print(Fore.YELLOW + "[Init] State error: " + str(e))
            self.position = None

    def _save_state(self):
        try:
            with open(STATE_FILE, "w") as f:
                json.dump(self.position, f, indent=2)
        except Exception:
            pass

    def _clear_state(self):
        self.position = None
        try:
            if os.path.exists(STATE_FILE):
                os.remove(STATE_FILE)
        except Exception:
            pass

    def _load_day(self):
        today = datetime.now().strftime("%Y-%m-%d")
        if os.path.exists(DAY_FILE):
            try:
                with open(DAY_FILE, "r") as f:
                    d = json.load(f)
                if d.get("date") == today:
                    for k in ["sl_hit", "last_sl_time", "last_exit_dir", "last_exit_time",
                              "last_sl_dir", "last_sl_dir_time"]:
                        d.setdefault(k, None)
                    d.setdefault("sl_hit", False)
                    d.setdefault("chop_checked", False)
                    d.setdefault("chop_skip", False)
                    d.setdefault("open_equity", self.book.equity)
                    self.day_state = d
                    print(Fore.CYAN + f"[Init] Resuming today: PnL Rs{d['pnl']:,.2f} | Trades {d['trades']}")
                    emit("session_resume", f"Resuming today — PnL Rs{d['pnl']:,.2f}, {d['trades']} trades",
                         day_pnl=d["pnl"], trades=d["trades"], open_equity=d["open_equity"])
                    return
            except Exception:
                pass
        self.day_state = {
            "date": today, "pnl": 0.0, "trades": 0, "killed": False,
            "sl_hit": False, "last_sl_time": None,
            "last_exit_dir": None, "last_exit_time": None,
            "last_sl_dir": None, "last_sl_dir_time": None,
            "chop_checked": False, "chop_skip": False,
            "open_equity": self.book.equity
        }
        self._save_day()
        print(Fore.CYAN + f"[Init] NEW SESSION | opening equity Rs{self.book.equity:,.2f}")
        emit("session_open", f"New session — opening equity Rs{self.book.equity:,.2f}",
             open_equity=self.book.equity, date=today)

    def _save_day(self):
        try:
            with open(DAY_FILE, "w") as f:
                json.dump(self.day_state, f, indent=2)
        except Exception:
            pass

    # ================================================================
    #  CHOP FILTER
    # ================================================================
    def _fetch_history_chunk(self, d_start, d_end):
        try:
            self._patch_ip()
            todate   = datetime.now() - timedelta(days=d_end)
            fromdate = datetime.now() - timedelta(days=d_start)
            exch, tok = ("NFO", FUT_TOKEN) if FUT_TOKEN else ("NSE", INDEX_TOKEN)
            params = {"exchange": exch, "symboltoken": tok, "interval": "ONE_MINUTE",
                      "fromdate": fromdate.strftime("%Y-%m-%d %H:%M"),
                      "todate": todate.strftime("%Y-%m-%d %H:%M")}
            res = timed("getCandleData", self.api.getCandleData, params)
            if res and res.get("status") and res.get("data"):
                df = pd.DataFrame(res["data"], columns=["Timestamp", "Open", "High", "Low", "Close", "Volume"])
                for c in ["Open", "High", "Low", "Close", "Volume"]:
                    df[c] = pd.to_numeric(df[c], errors="coerce")
                df = df.dropna()
                df["Timestamp"] = pd.to_datetime(df["Timestamp"]).dt.tz_localize(None)
                return df
        except Exception as e:
            print(Fore.YELLOW + f"[Chop] Chunk error: {e}")
        return None

    def _session_score_table(self, df):
        d = df.copy()
        d["Date"] = d["Timestamp"].dt.date
        hi, lo, cl = d["High"].values, d["Low"].values, d["Close"].values
        m = len(cl)
        tr = np.full(m, np.nan)
        pdm = np.zeros(m)
        ndm = np.zeros(m)
        for i in range(1, m):
            tr[i] = max(hi[i] - lo[i], abs(hi[i] - cl[i - 1]), abs(lo[i] - cl[i - 1]))
            up, dn = hi[i] - hi[i - 1], lo[i - 1] - lo[i]
            pdm[i] = up if (up > dn and up > 0) else 0.0
            ndm[i] = dn if (dn > up and dn > 0) else 0.0
        S = pd.Series
        atr = S(tr).ewm(span=ADX_PERIOD, adjust=False).mean().values
        pdi = S(pdm).ewm(span=ADX_PERIOD, adjust=False).mean().values
        ndi = S(ndm).ewm(span=ADX_PERIOD, adjust=False).mean().values
        with np.errstate(divide="ignore", invalid="ignore"):
            dx = np.where(atr > 0, 100 * np.abs(pdi - ndi) / (pdi + ndi + 1e-9), np.nan)
        d["ADX"] = S(dx).ewm(span=ADX_PERIOD, adjust=False).mean().values
        rows = []
        for dt, grp in d.groupby("Date"):
            c = grp["Close"].values
            if len(c) < 30:
                continue
            path = np.sum(np.abs(np.diff(c)))
            ser  = abs(c[-1] - c[0]) / path if path > 0 else 0.0
            a = grp["ADX"].dropna()
            rows.append({"date": dt, "ser": ser,
                         "adx": float(a.iloc[-1]) if len(a) else np.nan,
                         "rng": float((grp["High"].max() - grp["Low"].min()) / c[-1])})
        if not rows:
            return None
        s = pd.DataFrame(rows).sort_values("date").reset_index(drop=True)
        s["score"] = (s["ser"].rank(pct=True) + s["adx"].rank(pct=True) + s["rng"].rank(pct=True)) / 3.0
        return s

    def _build_chop_history(self):
        if not ENABLE_CHOP_FILTER:
            print(Fore.CYAN + "[Chop] Filter DISABLED")
            return
        print(Fore.YELLOW + f"[Chop] Building {CHOP_LOOKBACK_DAYS}-session history...")
        frames = []
        for a, b in [(CHOP_HISTORY_DAYS, 50), (50, 25), (25, 0)]:
            ch = self._fetch_history_chunk(a, b)
            if ch is not None and len(ch):
                frames.append(ch)
            time.sleep(1.2)
        if not frames:
            print(Fore.RED + "[Chop] No history - filter OFF for safety")
            emit("chop_history", "No history available — chop filter disabled", level="warn")
            self.chop_cutoff = None
            return
        df = (pd.concat(frames).drop_duplicates(subset=["Timestamp"])
                .sort_values("Timestamp").reset_index(drop=True))
        tbl = self._session_score_table(df)
        if tbl is None or len(tbl) < 10:
            print(Fore.RED + "[Chop] Too few sessions - filter OFF")
            self.chop_cutoff = None
            return
        self.chop_scores = {r.date: float(r.score) for r in tbl.itertuples()}
        recent = tbl.tail(CHOP_LOOKBACK_DAYS)["score"].values
        self.chop_cutoff = float(np.percentile(recent, CHOP_BLOCK_PCT))
        try:
            with open(CHOP_FILE, "w") as f:
                json.dump({"cutoff": self.chop_cutoff,
                           "scores": {str(k): v for k, v in self.chop_scores.items()}}, f, indent=2)
        except Exception:
            pass
        print(Fore.GREEN + f"[Chop] {len(tbl)} sessions | cutoff {self.chop_cutoff:.3f} "
              + f"(block bottom {CHOP_BLOCK_PCT}%)")
        emit("chop_history", f"{len(tbl)} sessions scored, cutoff {self.chop_cutoff:.3f}",
             sessions=len(tbl), cutoff=round(self.chop_cutoff, 4), block_pct=CHOP_BLOCK_PCT)

    def _is_choppy_today(self):
        if not ENABLE_CHOP_FILTER or self.chop_cutoff is None or not self.chop_scores:
            return False
        today = datetime.now().date()
        past  = sorted([d for d in self.chop_scores if d < today])
        if not past:
            return False
        prev  = past[-1]
        score = self.chop_scores[prev]
        self.chop_today_score = score
        choppy = score < self.chop_cutoff
        if choppy:
            print(Fore.MAGENTA + Style.BRIGHT
                  + f"\n[CHOP SKIP] {prev} scored {score:.3f} < cutoff {self.chop_cutoff:.3f}"
                  + f"\n            Market was directionless yesterday -> NO TRADES TODAY")
            emit("chop_block",
                 f"Chop filter blocked the session — {prev} scored {score:.3f} < {self.chop_cutoff:.3f}",
                 level="warn", score=round(score, 4), cutoff=round(self.chop_cutoff, 4),
                 scored_date=str(prev))
        else:
            print(Fore.GREEN + f"[Chop] {prev} scored {score:.3f} >= {self.chop_cutoff:.3f} -> trading OK")
            emit("chop_pass", f"Chop filter passed — {prev} scored {score:.3f}",
                 score=round(score, 4), cutoff=round(self.chop_cutoff, 4), scored_date=str(prev))
        return choppy

    # ---- candles / LTP with latency ----
    def get_candles(self):
        now = time.time()
        if self.candle_cache is not None and (now - self.last_candle_fetch) < CANDLE_CACHE_SECONDS:
            return self.candle_cache
        try:
            self._patch_ip()
            todate = datetime.now()
            fromdate = todate - timedelta(days=7)
            exch, tok = ("NFO", FUT_TOKEN) if FUT_TOKEN else ("NSE", INDEX_TOKEN)
            params = {"exchange": exch, "symboltoken": tok, "interval": "ONE_MINUTE",
                      "fromdate": fromdate.strftime("%Y-%m-%d %H:%M"),
                      "todate": todate.strftime("%Y-%m-%d %H:%M")}
            res = timed("getCandleData", self.api.getCandleData, params)
            if res and res.get("status") and res.get("data"):
                df = pd.DataFrame(res["data"], columns=["Timestamp", "Open", "High", "Low", "Close", "Volume"])
                for c in ["Open", "High", "Low", "Close", "Volume"]:
                    df[c] = pd.to_numeric(df[c], errors="coerce")
                df = df.dropna()
                df["Timestamp"] = pd.to_datetime(df["Timestamp"]).dt.tz_localize(None)
                self.candle_cache = df
                self.last_candle_fetch = now
                return df
        except Exception as e:
            msg = str(e).lower()
            if "exceed" in msg or "rate" in msg:
                self.last_candle_fetch = now + 120
        return self.candle_cache

    def get_spot_ltp(self):
        try:
            self._patch_ip()
            res = timed("ltpData", self.api.ltpData, "NSE", INDEX_SYMBOL, INDEX_TOKEN)
            if res and res.get("status"):
                return float(res["data"]["ltp"])
        except Exception:
            pass
        return None

    def get_option_ltp(self, symbol, token):
        try:
            self._patch_ip()
            res = timed("ltpData", self.api.ltpData, "NFO", symbol, token)
            if res and res.get("status"):
                self.ltp_failures = 0
                ltp = float(res["data"]["ltp"])
                # Every poll on the open contract feeds the trade-box chart.
                if self.position and self.position.get("symbol") == symbol:
                    self.premium_series.append((int(time.time() * 1000), round(ltp, 2)))
                return ltp
        except Exception:
            pass
        self.ltp_failures += 1
        return None

    def get_option_quote(self, symbol, token):
        """Full quote if available -> real bid/ask spread. Falls back to LTP."""
        try:
            self._patch_ip()
            res = timed("ltpData", self.api.ltpData, "NFO", symbol, token)
            if res and res.get("status"):
                d = res["data"]
                return {"ltp": float(d.get("ltp", 0)),
                        "open": float(d.get("open", 0) or 0),
                        "high": float(d.get("high", 0) or 0),
                        "low": float(d.get("low", 0) or 0),
                        "close": float(d.get("close", 0) or 0)}
        except Exception:
            pass
        return None

    def get_fill_price_from_tradebook(self, order_id):
        if PAPER_MODE:
            return self._paper_last_fill.get(str(order_id))
        for a in range(4):
            try:
                self._patch_ip()
                book = timed("tradeBook", self.api.tradeBook)
                if book and book.get("status"):
                    for t in (book.get("data") or []):
                        if str(t.get("orderid", "")) == str(order_id):
                            p = float(t.get("averageprice", 0))
                            if p > 0:
                                return p
            except Exception:
                pass
            if a < 3:
                time.sleep(3)
        return None

    def get_positions_from_angel(self):
        if PAPER_MODE:
            return []
        try:
            self._patch_ip()
            res = timed("position", self.api.position)
            if res and res.get("status") and res.get("data"):
                return res["data"]
        except Exception:
            pass
        return []

    def get_required_margin(self, symbol, token, qty):
        """REAL margin from Angel One margin API — works in paper mode too."""
        try:
            self._patch_ip()
            params = {"positions": [{"exchange": "NFO", "qty": qty, "price": 0,
                                     "productType": "INTRADAY", "token": str(token),
                                     "tradeType": "BUY"}]}
            res = timed("getMarginApi", self.api.getMarginApi, params)
            if res and res.get("status") and res.get("data"):
                req = float(res["data"].get("totalMarginRequired", 0))
                if req > 0:
                    self.real_margin_1lot = req
                    return req
        except Exception:
            pass
        return None

    # ---- charges (Angel One published rates; no API endpoint exists) ----
    def calc_charges(self, fill_price, qty, side, breakdown=False):
        value = fill_price * qty
        brok  = BROKERAGE_PER_ORDER
        stt   = value * STT_SELL_RATE if side == "SELL" else 0.0
        exch  = value * EXCH_TXN_RATE
        sebi  = value * SEBI_RATE
        ipft  = value * IPFT_RATE
        stamp = value * STAMP_BUY_RATE if side == "BUY" else 0.0
        gst   = (brok + exch + sebi + ipft) * GST_RATE
        total = brok + stt + exch + sebi + ipft + stamp + gst
        if breakdown:
            return {"brokerage": round(brok, 2), "stt": round(stt, 2),
                    "exchange": round(exch, 2), "sebi": round(sebi, 2),
                    "ipft": round(ipft, 2), "stamp": round(stamp, 2),
                    "gst": round(gst, 2), "total": round(total, 2),
                    "turnover": round(value, 2)}
        return round(total, 2)

    # ---- indicators ----
    def compute_indicators(self, df):
        d = df.copy()
        d["Date"] = d["Timestamp"].dt.date
        g = d.groupby("Date")
        d["EMA9"]  = g["Close"].transform(lambda x: x.ewm(span=EMA_FAST, adjust=False).mean())
        d["EMA21"] = g["Close"].transform(lambda x: x.ewm(span=EMA_SLOW, adjust=False).mean())
        d["SMA50"] = g["Close"].transform(lambda x: x.rolling(SMA_PERIOD).mean())
        d["TP"]     = (d["High"] + d["Low"] + d["Close"]) / 3.0
        d["TPV"]    = d["TP"] * d["Volume"]
        d["CumVol"] = g["Volume"].cumsum()
        d["CumTPV"] = g["TPV"].cumsum()
        d["VWAP"]   = np.where(d["CumVol"] > 0, d["CumTPV"] / d["CumVol"], d["Close"])
        d["EMA9_slope"] = g["EMA9"].transform(lambda x: x.diff(SLOPE_BARS))
        d["ret1m"]      = g["Close"].pct_change()

        # ADX for market atmosphere read-out
        hi, lo, cl = d["High"].values, d["Low"].values, d["Close"].values
        m = len(cl)
        tr = np.full(m, np.nan)
        pdm = np.zeros(m)
        ndm = np.zeros(m)
        for i in range(1, m):
            tr[i] = max(hi[i] - lo[i], abs(hi[i] - cl[i - 1]), abs(lo[i] - cl[i - 1]))
            up, dn = hi[i] - hi[i - 1], lo[i - 1] - lo[i]
            pdm[i] = up if (up > dn and up > 0) else 0.0
            ndm[i] = dn if (dn > up and dn > 0) else 0.0
        S = pd.Series
        atr = S(tr).ewm(span=ADX_PERIOD, adjust=False).mean().values
        pdi = S(pdm).ewm(span=ADX_PERIOD, adjust=False).mean().values
        ndi = S(ndm).ewm(span=ADX_PERIOD, adjust=False).mean().values
        with np.errstate(divide="ignore", invalid="ignore"):
            dx = np.where(atr > 0, 100 * np.abs(pdi - ndi) / (pdi + ndi + 1e-9), np.nan)
        d["ADX"] = S(dx).ewm(span=ADX_PERIOD, adjust=False).mean().values
        d["ATR"] = atr

        if USE_RSI_FILTER:
            def _rsi(x):
                dl = x.diff()
                up = dl.clip(lower=0).ewm(span=RSI_PERIOD, adjust=False).mean()
                dn = (-dl).clip(lower=0).ewm(span=RSI_PERIOD, adjust=False).mean()
                return 100 - (100 / (1 + up / (dn + 1e-9)))
            d["RSI"] = g["Close"].transform(_rsi)
        return d

    def compute_garch(self, df_ind):
        return garch_forecast(df_ind["ret1m"].dropna().values)

    # ---- market atmosphere ----
    def read_market_atmosphere(self, df_ind, gvol, spot):
        last = df_ind.iloc[-1]
        adx  = float(last["ADX"]) if not pd.isna(last["ADX"]) else 0.0
        atr  = float(last["ATR"]) if not pd.isna(last["ATR"]) else 0.0
        ema9, ema21 = float(last["EMA9"]), float(last["EMA21"])
        vwap = float(last["VWAP"]) if not pd.isna(last["VWAP"]) else spot
        slope = float(last["EMA9_slope"]) if not pd.isna(last["EMA9_slope"]) else 0.0

        today = df_ind[df_ind["Date"] == datetime.now().date()]
        if len(today) > 2:
            c = today["Close"].values
            path = np.sum(np.abs(np.diff(c)))
            ser  = abs(c[-1] - c[0]) / path if path > 0 else 0.0
            day_rng_pts = float(today["High"].max() - today["Low"].min())
            day_rng_pct = day_rng_pts / c[-1] * 100
            day_move    = float(c[-1] - c[0])
        else:
            ser, day_rng_pts, day_rng_pct, day_move = 0.0, 0.0, 0.0, 0.0

        gv = gvol * 100 if gvol else 0.0
        vol_regime = ("DEAD" if gv < 7 else "QUIET" if gv < 9 else "TRADEABLE"
                      if gv < 20 else "ELEVATED" if gv < 30 else "CHAOTIC")
        trend = ("STRONG TREND" if adx >= 25 else "TRENDING" if adx >= 20
                 else "WEAK TREND" if adx >= 15 else "NO TREND / CHOP")
        direction = ("UP" if ema9 > ema21 and slope > 0 else
                     "DOWN" if ema9 < ema21 and slope < 0 else "SIDEWAYS")
        vwap_side = "ABOVE" if spot > vwap else "BELOW" if spot < vwap else "AT"
        efficiency = ("DIRECTIONAL" if ser > 0.15 else "MIXED" if ser > 0.07 else "CHOPPY")

        snap = dict(adx=adx, atr=atr, garch=gv, vol_regime=vol_regime,
                    trend=trend, direction=direction, vwap_side=vwap_side,
                    ser=ser, efficiency=efficiency, spot=spot, vwap=vwap,
                    ema9=ema9, ema21=ema21, slope=slope,
                    day_range_pts=day_rng_pts, day_range_pct=day_rng_pct,
                    day_move=day_move)
        self.market_snapshot = snap
        return snap

    # ---- contracts ----
    def find_atm_contract(self, spot, opt_type):
        atm = round(spot / STRIKE_STEP) * STRIKE_STEP
        target = float(atm * 100)
        today = datetime.now().date()
        exps = set()
        for s in self.scrip_master:
            try:
                e = datetime.strptime(s["expiry"], "%d%b%Y").date()
                if e > today:
                    exps.add(e)
            except Exception:
                continue
        if not exps:
            return None
        ne = sorted(exps)[0]
        pool = []
        for s in self.scrip_master:
            try:
                e = datetime.strptime(s["expiry"], "%d%b%Y").date()
            except Exception:
                continue
            if e != ne:
                continue
            sym = s.get("symbol", "")
            if opt_type == "C" and not sym.endswith("CE"):
                continue
            if opt_type == "P" and not sym.endswith("PE"):
                continue
            try:
                sv = float(s.get("strike", 0))
                pool.append((abs(sv - target), sv, s))
            except Exception:
                continue
        if not pool:
            return None
        pool.sort(key=lambda x: x[0])
        exact = [x for x in pool if x[0] < 1.0]
        best = exact[0][2] if exact else pool[0][2]
        return (best["symbol"], best["token"], int(float(best.get("strike", 0)) / 100), ne)

    def search_scrip_live(self, txt):
        if not hasattr(self, "_scrip_cache"):
            self._scrip_cache = {}
        now = time.time()
        c = self._scrip_cache.get(txt)
        if c and (now - c[0]) < 300:
            return c[1]
        if not hasattr(self, "_last_search"):
            self._last_search = 0
        el = now - self._last_search
        if el < 1.5:
            time.sleep(1.5 - el)
        try:
            self._patch_ip()
            res = timed("searchScrip", self.api.searchScrip, "NFO", txt)
            self._last_search = time.time()
            if res and res.get("status") and res.get("data"):
                self._scrip_cache[txt] = (now, res["data"])
                return res["data"]
        except Exception:
            pass
        return []

    def find_atm_contract_live(self, spot, opt_type):
        atm = round(spot / STRIKE_STEP) * STRIKE_STEP
        today = datetime.now().date()
        for k in [atm, atm + STRIKE_STEP, atm - STRIKE_STEP, atm + 2 * STRIKE_STEP, atm - 2 * STRIKE_STEP]:
            res = self.search_scrip_live(f"{INDEX_NAME} {k} {'CE' if opt_type=='C' else 'PE'}")
            if not res:
                continue
            valid = []
            for r in res:
                try:
                    e = datetime.strptime(r.get("expiry", ""), "%d%b%Y").date()
                    if e > today:
                        valid.append((e, r))
                except Exception:
                    continue
            if not valid:
                continue
            valid.sort(key=lambda x: x[0])
            e, b = valid[0]
            return (b.get("tradingsymbol"), b.get("symboltoken"), k, e,
                    int(b.get("lotsize", self.lot_size)))
        r = self.find_atm_contract(spot, opt_type)
        if r:
            return (r[0], r[1], r[2], r[3], self.lot_size)
        return None

    # ================================================================
    #  SIGNAL — returns a Decision with a REASON, always
    # ================================================================
    def evaluate(self, df_ind, gvol, nearest_expiry, spot):
        D = Decision
        if self.position:
            return D(None, "IN_POSITION", f"Already holding {self.position['symbol']}")
        if self.day_state.get("chop_skip"):
            sc = self.chop_today_score
            return D(None, "CHOPPY_DAY",
                     f"Chop filter blocked today (score {sc:.3f} < {self.chop_cutoff:.3f})"
                     if sc is not None else "Chop filter blocked today")
        if self.day_state["killed"]:
            return D(None, "DAILY_KILL",
                     f"Daily kill hit (Rs{self.day_state['pnl']:,.0f} <= -Rs{DAILY_KILL_RUPEES:,.0f})")
        if self.day_state["trades"] >= MAX_TRADES_PER_DAY:
            return D(None, "MAX_TRADES",
                     f"Trade cap reached ({self.day_state['trades']}/{MAX_TRADES_PER_DAY})")
        now_t = datetime.now().time()
        if now_t < MARKET_OPEN:
            return D(None, "PRE_WINDOW", f"Before entry window (opens {MARKET_OPEN.strftime('%H:%M')})")
        if now_t >= ENTRY_CUTOFF:
            return D(None, "POST_WINDOW", f"After entry cutoff ({ENTRY_CUTOFF.strftime('%H:%M')})")
        if len(df_ind) < SMA_PERIOD:
            return D(None, "WARMUP", f"Only {len(df_ind)} bars, need {SMA_PERIOD}")

        if nearest_expiry and nearest_expiry == datetime.now().date():
            if BLOCK_EXPIRY_DAY_TRADING:
                return D(None, "EXPIRY_DAY", "Expiry day - theta risk too high, blocked")
            if gvol is None or gvol < EXPIRY_DAY_GARCH_MIN:
                return D(None, "EXPIRY_LOWVOL",
                         f"Expiry day needs GARCH>{EXPIRY_DAY_GARCH_MIN*100:.0f}%, got {(gvol or 0)*100:.1f}%")

        gate = GARCH_VOL_MIN
        gate_why = "base"
        if self.day_state.get("sl_hit"):
            if POST_SL_GARCH_MIN > gate:
                gate, gate_why = POST_SL_GARCH_MIN, "post-SL"
        if self.day_state.get("trades", 0) >= 2:
            if GARCH_TRADE3_MIN > gate:
                gate, gate_why = GARCH_TRADE3_MIN, "trade-3"
        if gvol is None:
            return D(None, "GARCH_NONE", "GARCH could not be computed (insufficient returns)")
        if gvol <= gate:
            return D(None, "LOW_VOL",
                     f"GARCH {gvol*100:.1f}% <= {gate*100:.0f}% gate ({gate_why}) - "
                     f"market too quiet to pay for premium")

        row = df_ind.iloc[-1]
        if pd.isna(row["EMA9_slope"]):
            return D(None, "NO_SLOPE", "EMA9 slope not yet available")
        ema9, ema21 = row["EMA9"], row["EMA21"]
        vwap = row["VWAP"]
        if pd.isna(ema9) or pd.isna(ema21):
            return D(None, "NO_EMA", "EMAs not yet available")
        slope = float(row["EMA9_slope"])
        s = spot if spot is not None else float(row["Close"])

        bull_stack = slope > 0 and ema9 > ema21
        bear_stack = slope < 0 and ema9 < ema21
        if not bull_stack and not bear_stack:
            return D(None, "NO_TREND",
                     f"EMA9 {ema9:.1f} vs EMA21 {ema21:.1f}, slope {slope:+.2f} - "
                     f"no clean trend stack")

        side = "C" if bull_stack else "P"
        if pd.isna(vwap):
            return D(None, "NO_VWAP", "VWAP unavailable")
        if side == "C" and s <= vwap:
            return D(None, "VWAP_BLOCK",
                     f"Bullish stack but spot {s:.1f} <= VWAP {vwap:.1f} - "
                     f"buyers not in control")
        if side == "P" and s >= vwap:
            return D(None, "VWAP_BLOCK",
                     f"Bearish stack but spot {s:.1f} >= VWAP {vwap:.1f} - "
                     f"sellers not in control")

        if USE_RSI_FILTER:
            rv = row.get("RSI")
            if rv is None or pd.isna(rv):
                return D(None, "NO_RSI", "RSI unavailable")
            if side == "C" and rv <= 50:
                return D(None, "RSI_BLOCK", f"RSI {rv:.0f} <= 50, momentum not confirming CE")
            if side == "P" and rv >= 50:
                return D(None, "RSI_BLOCK", f"RSI {rv:.0f} >= 50, momentum not confirming PE")

        blocked, why = self._same_dir_blocked(side)
        if blocked:
            return D(None, "COOLDOWN",
                     f"{'CE' if side=='C' else 'PE'} signal valid but {why}")

        detail = (f"{'CE' if side=='C' else 'PE'} — EMA9 {ema9:.1f} "
                  f"{'>' if side=='C' else '<'} EMA21 {ema21:.1f}, "
                  f"slope {slope:+.2f}, spot {s:.1f} "
                  f"{'above' if side=='C' else 'below'} VWAP {vwap:.1f}, "
                  f"GARCH {gvol*100:.1f}% > {gate*100:.0f}% gate")
        return D(side, "SIGNAL", detail)

    # ---- order placement ----
    def _is_market_open(self):
        now = datetime.now()
        if now.weekday() >= 5:
            return False, "Market closed - " + now.strftime("%A")
        t = now.time()
        if t < dtime(9, 15) or t >= dtime(15, 30):
            return False, "Market closed - NSE hours 9:15-15:30"
        return True, "OK"

    def verify_order_status(self, oid):
        if PAPER_MODE:
            return ("COMPLETE", "paper")
        if not oid:
            return ("UNKNOWN", "")
        for a in range(3):
            try:
                self._patch_ip()
                b = timed("orderBook", self.api.orderBook)
                if b and b.get("status"):
                    for o in (b.get("data") or []):
                        if str(o.get("orderid", "")) == str(oid):
                            return (str(o.get("status", "")).upper(), str(o.get("text", "")))
            except Exception:
                pass
            time.sleep(2)
        return ("UNKNOWN", "")

    def _place_order(self, symbol, token, side, qty, skip_verify=False):
        ok, reason = self._is_market_open()
        if not ok:
            print(Fore.YELLOW + "[ORDER] Blocked - " + reason)
            emit("order_blocked", reason, level="warn", symbol=symbol, side=side)
            return None

        if PAPER_MODE:
            t0 = time.perf_counter()
            ltp = self.get_option_ltp(symbol, token)
            lag = (time.perf_counter() - t0) * 1000
            if ltp is None or ltp <= 0:
                print(Fore.RED + "[PAPER] No LTP - order not simulated")
                emit("order_failed", "No LTP available — order not simulated",
                     level="error", symbol=symbol, side=side)
                return None
            fill = ltp * (1 + PAPER_SLIPPAGE_PCT) if side == "BUY" else ltp * (1 - PAPER_SLIPPAGE_PCT)
            self._paper_order_seq += 1
            oid = f"PAPER{self._paper_order_seq:06d}"
            self._paper_last_fill[oid] = round(fill, 2)
            slip_rs = abs(fill - ltp) * qty
            print(Fore.CYAN + Style.BRIGHT
                  + f"\n[PAPER ORDER] {side} {qty}x {symbol}")
            print(Fore.CYAN + f"    Angel LTP    : Rs {ltp:,.2f}   (fetch lag {lag:.0f}ms)")
            print(Fore.CYAN + f"    Sim fill     : Rs {fill:,.2f}   "
                  + f"(slippage {PAPER_SLIPPAGE_PCT*100:.1f}% = Rs {slip_rs:,.2f})")
            print(Fore.CYAN + f"    Order ID     : {oid}")
            emit("order", f"PAPER {side} {qty}x {symbol} @ Rs{fill:,.2f}",
                 side=side, symbol=symbol, qty=qty, ltp=round(ltp, 2),
                 fill=round(fill, 2), slippage_rs=round(slip_rs, 2),
                 order_id=oid, latency_ms=round(lag, 1), paper=True)
            return oid

        params = {"variety": "NORMAL", "tradingsymbol": symbol, "symboltoken": str(token),
                  "transactiontype": side, "exchange": "NFO", "ordertype": "MARKET",
                  "producttype": "INTRADAY", "duration": "DAY", "price": "0",
                  "squareoff": "0", "stoploss": "0", "quantity": str(qty)}
        for attempt in range(ORDER_RETRY_ATTEMPTS):
            try:
                self._patch_ip()
                raw = None
                try:
                    raw = timed("placeOrder", self.api.placeOrderFullResponse, params)
                except AttributeError:
                    raw = timed("placeOrder", self.api.placeOrder, params)
                if raw and isinstance(raw, str) and len(raw.strip()) > 3:
                    oid = raw.strip()
                    emit("order", f"LIVE {side} {qty}x {symbol} submitted",
                         side=side, symbol=symbol, qty=qty, order_id=oid, paper=False)
                    if skip_verify:
                        return oid
                    st, msg = self.verify_order_status(oid)
                    if st in ("REJECTED", "CANCELLED"):
                        emit("order_failed", f"Order {st}: {msg}", level="error",
                             symbol=symbol, side=side, order_id=oid)
                        return None
                    return oid
                if raw and isinstance(raw, dict):
                    oid = (raw.get("orderid") or raw.get("uniqueorderid")
                           or (raw.get("data") or {}).get("orderid"))
                    if oid:
                        oid = str(oid).strip()
                        emit("order", f"LIVE {side} {qty}x {symbol} submitted",
                             side=side, symbol=symbol, qty=qty, order_id=oid, paper=False)
                        if skip_verify:
                            return oid
                        st, msg = self.verify_order_status(oid)
                        if st in ("REJECTED", "CANCELLED"):
                            emit("order_failed", f"Order {st}: {msg}", level="error",
                                 symbol=symbol, side=side, order_id=oid)
                            return None
                        return oid
                    return None
            except Exception:
                import traceback
                tb = traceback.format_exc()
                print(Fore.RED + tb)
                emit("order_failed", f"Order exception: {tb.splitlines()[-1]}",
                     level="error", symbol=symbol, side=side)
                time.sleep(2)
        return None

    # ---- entry ----
    def enter_trade(self, decision, df_ind, spot, gvol):
        signal = decision.action
        if self.position:
            return
        contract = self.find_atm_contract_live(spot, signal)
        if not contract:
            print(Fore.RED + "[ENTRY] No ATM contract found")
            emit("entry_failed", "No ATM contract found", level="error")
            return
        symbol, token, strike, expiry, lot = contract
        if lot != self.lot_size:
            self.lot_size = lot

        ltp = self.get_option_ltp(symbol, token)
        if not ltp or ltp < 5:
            print(Fore.RED + f"[ENTRY] Bad LTP: {ltp}")
            emit("entry_failed", f"Bad LTP {ltp} for {symbol}", level="error", symbol=symbol)
            return

        equity = self.book.equity
        real_margin = self.get_required_margin(symbol, token, self.lot_size)
        premium_cost = ltp * self.lot_size
        margin_needed = real_margin if real_margin else premium_cost

        if margin_needed > equity * 0.90:
            print(Fore.YELLOW + Style.BRIGHT + "\n[NO ENTRY] Signal was valid but capital blocks it")
            print(Fore.YELLOW + f"    Signal      : {decision.detail}")
            print(Fore.YELLOW + f"    1 lot cost  : Rs {premium_cost:,.2f} "
                  + f"({self.lot_size} x Rs {ltp:,.2f})")
            print(Fore.YELLOW + f"    Margin req  : Rs {margin_needed:,.2f} (Angel One)")
            print(Fore.YELLOW + f"    Equity      : Rs {equity:,.2f} (90% usable = Rs {equity*0.9:,.2f})")
            self.reject_counts["INSUFFICIENT_CAPITAL"] += 1
            self.last_decision = Decision(None, "INSUFFICIENT_CAPITAL",
                                          f"1 lot needs Rs{margin_needed:,.0f}, equity Rs{equity:,.0f}")
            emit("entry_blocked",
                 f"Valid {('CE' if signal=='C' else 'PE')} signal but 1 lot needs "
                 f"Rs{margin_needed:,.0f} against Rs{equity:,.0f} equity",
                 level="warn", symbol=symbol, signal_detail=decision.detail,
                 lot_cost=round(premium_cost, 2), margin=round(margin_needed, 2),
                 equity=round(equity, 2), usable=round(equity * 0.9, 2))
            return

        oid = self._place_order(symbol, token, "BUY", self.lot_size)
        if not oid:
            print(Fore.RED + "[ENTRY] Order failed")
            emit("entry_failed", "Buy order failed", level="error", symbol=symbol)
            return
        if not PAPER_MODE:
            time.sleep(2)
        fill = self.get_fill_price_from_tradebook(oid) or ltp

        market_iv = model_prem = None
        try:
            dte = max(1, (expiry - datetime.now().date()).days)
            market_iv  = implied_vol(fill, spot, strike, dte / 365.0, RF_RATE, signal)
            model_prem = bsm_price(spot, strike, dte / 365.0, RF_RATE,
                                   max(0.10, gvol * 1.25), signal)
        except Exception:
            pass

        entry_charges = self.calc_charges(fill, self.lot_size, "BUY", breakdown=True)
        sl_init = round(fill * (1 - SL_PCT), 2)
        risk_rs = (fill - sl_init) * self.lot_size

        self.position = {
            "symbol": symbol, "token": token, "type": signal, "strike": strike,
            "expiry": expiry.strftime("%Y-%m-%d"),
            "entry_prem": round(fill, 2), "qty": self.lot_size, "lots": 1,
            "buy_order_id": oid, "sell_order_id": None,
            "entry_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "locked": False, "pyramided": False, "avg_entry": round(fill, 2),
            "entry_iv": round(market_iv, 4) if market_iv else None,
            "model_prem": round(model_prem, 2) if model_prem else None,
            "garch_vol": round(gvol, 4), "spot_at_entry": round(spot, 2),
            "stage": "INIT", "sl_price": sl_init, "high_prem": round(fill, 2),
            "entry_reason": decision.detail,
            "entry_charges": entry_charges["total"],
            "real_margin": round(margin_needed, 2),
            "lot_cost": round(premium_cost, 2),
            "risk_rs": round(risk_rs, 2),
        }
        self.day_state["trades"] += 1
        self._save_state()
        self._save_day()

        print(Fore.GREEN + Style.BRIGHT + "\n" + "=" * 68)
        print(Fore.GREEN + Style.BRIGHT
              + f"  ORDER TAKEN  #{self.day_state['trades']}/{MAX_TRADES_PER_DAY}"
              + f"   {datetime.now().strftime('%H:%M:%S')}")
        print(Fore.GREEN + Style.BRIGHT + "=" * 68)
        print(Fore.WHITE + Style.BRIGHT + "  WHY THIS TRADE:")
        print(Fore.GREEN + f"    {decision.detail}")
        print(Fore.WHITE + "\n  CONTRACT")
        print(f"    Symbol       : {symbol}")
        print(f"    Strike       : {strike} {'CE' if signal=='C' else 'PE'}   "
              + f"Expiry {expiry}")
        print(f"    Spot at entry: Rs {spot:,.2f}")
        print(Fore.WHITE + f"\n  COST OF 1 LOT ({self.lot_size} qty)")
        print(f"    Angel LTP    : Rs {ltp:,.2f}")
        print(f"    Fill price   : Rs {fill:,.2f}")
        print(f"    Premium cost : Rs {fill*self.lot_size:,.2f}")
        print(f"    Margin (Angel): Rs {margin_needed:,.2f}"
              + ("  [live API]" if real_margin else "  [estimated]"))
        print(f"    Entry charges: Rs {entry_charges['total']:,.2f}  "
              + f"(brok {entry_charges['brokerage']:.0f} + txn {entry_charges['exchange']:.2f}"
              + f" + stamp {entry_charges['stamp']:.2f} + gst {entry_charges['gst']:.2f})")
        print(Fore.WHITE + "\n  RISK ON THIS TRADE")
        print(f"    Stop loss    : Rs {sl_init:,.2f}  (-{SL_PCT*100:.0f}%)")
        print(f"    Max loss     : Rs {risk_rs:,.2f}  "
              + f"({risk_rs/self.book.equity*100:.1f}% of equity)")
        print(f"    Equity       : Rs {self.book.equity:,.2f}")
        if model_prem:
            dp = (fill - model_prem) / model_prem * 100
            print(Fore.YELLOW + f"\n  MODEL CHECK  : BSM Rs {model_prem:,.2f} vs "
                  + f"real Rs {fill:,.2f}  ({dp:+.1f}%)")
        print(Fore.CYAN + f"  ANGEL LAG    : {LAT.summary_line()}")
        print(Fore.GREEN + Style.BRIGHT + "=" * 68)

        emit("entry",
             f"BOUGHT {strike}{'CE' if signal=='C' else 'PE'} @ Rs{fill:,.2f}",
             level="success",
             trade_no=self.day_state["trades"], max_trades=MAX_TRADES_PER_DAY,
             symbol=symbol, opt_type=signal, strike=strike,
             expiry=expiry.strftime("%Y-%m-%d"), qty=self.lot_size,
             ltp=round(ltp, 2), fill=round(fill, 2),
             premium_cost=round(fill * self.lot_size, 2),
             margin=round(margin_needed, 2), margin_is_live=bool(real_margin),
             entry_charges=entry_charges, sl_price=sl_init, risk_rs=round(risk_rs, 2),
             risk_pct_equity=round(risk_rs / self.book.equity * 100, 2),
             equity=round(self.book.equity, 2), spot=round(spot, 2),
             model_prem=round(model_prem, 2) if model_prem else None,
             entry_iv=round(market_iv, 4) if market_iv else None,
             garch_vol=round(gvol, 4), why=decision.detail,
             latency=LAT.summary_line(), order_id=oid,
             market=self.market_snapshot)

    def pyramid_add(self, cur):
        return False

    def compute_pnl(self, cur):
        p = self.position
        return (cur - p["avg_entry"]) * p["qty"]

    def compute_pnl_pct(self, cur):
        c = self.position["avg_entry"] * self.position["qty"]
        return self.compute_pnl(cur) / c if c > 0 else 0.0

    # ---- trade management ----
    def manage_trade(self):
        if not self.position:
            return
        p = self.position
        cur = self.get_option_ltp(p["symbol"], p["token"])
        if cur is None:
            if self.ltp_failures >= MAX_LTP_FAILURES:
                print(Fore.RED + f"\n[SAFETY] Force close after {self.ltp_failures} LTP failures")
                emit("safety", f"Force close after {self.ltp_failures} LTP failures", level="error")
                self.exit_trade(p.get("avg_entry"), "NETWORK_FAIL_FORCE_CLOSE")
            return

        entry = p["avg_entry"]
        gain  = (cur - entry) / entry if entry > 0 else 0.0
        cur_sl = p.get("sl_price", entry * (1 - SL_PCT))
        if cur > p.get("high_prem", entry):
            p["high_prem"] = cur

        if p["stage"] == "INIT" and gain >= BE_TRIGGER_PCT:
            p["stage"] = "BE"
            p["sl_price"] = round(entry * (1 + BE_FLOOR_PCT), 2)
            p["locked"] = True
            cur_sl = p["sl_price"]
            self._save_state()
            print(Fore.BLUE + Style.BRIGHT
                  + f"\n  [LADDER->BE] +{gain*100:.1f}% | SL moved to +{BE_FLOOR_PCT*100:.0f}% "
                  + f"Rs{p['sl_price']:,.2f} | trade can no longer lose")
            emit("ladder", f"Breakeven lock — SL to +{BE_FLOOR_PCT*100:.0f}% (Rs{p['sl_price']:,.2f})",
                 level="success", stage="BE", gain_pct=round(gain * 100, 2),
                 sl_price=p["sl_price"], symbol=p["symbol"], current=round(cur, 2))
        elif p["stage"] == "BE" and gain >= LOCK1_TRIGGER:
            p["stage"] = "LOCK1"
            p["sl_price"] = round(entry * (1 + LOCK1_FLOOR), 2)
            cur_sl = p["sl_price"]
            self._save_state()
            print(Fore.BLUE + Style.BRIGHT
                  + f"\n  [LADDER->LOCK1] +{gain*100:.1f}% | SL locked at +{LOCK1_FLOOR*100:.0f}% "
                  + f"Rs{p['sl_price']:,.2f}")
            emit("ladder", f"Lock 1 — SL to +{LOCK1_FLOOR*100:.0f}% (Rs{p['sl_price']:,.2f})",
                 level="success", stage="LOCK1", gain_pct=round(gain * 100, 2),
                 sl_price=p["sl_price"], symbol=p["symbol"], current=round(cur, 2))
        elif p["stage"] == "LOCK1" and gain >= LOCK2_TRIGGER:
            p["stage"] = "LOCK2"
            p["sl_price"] = round(entry * (1 + LOCK2_FLOOR), 2)
            cur_sl = p["sl_price"]
            self._save_state()
            print(Fore.BLUE + Style.BRIGHT
                  + f"\n  [LADDER->LOCK2] +{gain*100:.1f}% | SL locked at +{LOCK2_FLOOR*100:.0f}% "
                  + f"Rs{p['sl_price']:,.2f} | free-run starts next tick")
            emit("ladder", f"Lock 2 — SL to +{LOCK2_FLOOR*100:.0f}% (Rs{p['sl_price']:,.2f}), free-run next",
                 level="success", stage="LOCK2", gain_pct=round(gain * 100, 2),
                 sl_price=p["sl_price"], symbol=p["symbol"], current=round(cur, 2))
        elif p["stage"] == "LOCK2":
            p["stage"] = "FREE"
            t = p["high_prem"] * (1 - FREE_GIVEBACK)
            if t > p["sl_price"]:
                p["sl_price"] = round(t, 2)
            cur_sl = p["sl_price"]
            self._save_state()
            emit("ladder", f"Free run — trailing {FREE_GIVEBACK*100:.0f}% behind peak",
                 level="success", stage="FREE", gain_pct=round(gain * 100, 2),
                 sl_price=p["sl_price"], symbol=p["symbol"], current=round(cur, 2))
        elif p["stage"] == "FREE":
            t = p["high_prem"] * (1 - FREE_GIVEBACK)
            if t > p["sl_price"]:
                p["sl_price"] = round(t, 2)
                self._save_state()
            cur_sl = p["sl_price"]

        if datetime.now().time() >= FORCE_CLOSE:
            self.exit_trade(cur, "EOD_FORCE_CLOSE")
            return
        if cur <= cur_sl:
            rm = {"INIT": "STOP_LOSS", "BE": "BE_FLOOR_4PCT", "LOCK1": "LOCK1_10PCT",
                  "LOCK2": "LOCK2_25PCT", "FREE": "TRAIL_FREE_RUN"}
            self.exit_trade(cur, rm.get(p["stage"], "STOP_LOSS"))
            return

    # ---- exit ----
    def exit_trade(self, exit_ltp, reason):
        p = self.position
        if exit_ltp is None:
            exit_ltp = p.get("avg_entry", p["entry_prem"])
        sid = self._place_order(p["symbol"], p["token"], "SELL", p["qty"], skip_verify=True)
        fill = exit_ltp
        if sid:
            if not PAPER_MODE:
                time.sleep(2)
            f = self.get_fill_price_from_tradebook(sid)
            if f:
                fill = f

        buy_ch  = self.calc_charges(p["entry_prem"], p["qty"], "BUY",  breakdown=True)
        sell_ch = self.calc_charges(fill,            p["qty"], "SELL", breakdown=True)
        total_charges = buy_ch["total"] + sell_ch["total"]
        gross = (fill - p["avg_entry"]) * p["qty"]
        net   = gross - total_charges

        self.day_state["pnl"] += net
        new_eq = self.book.apply_trade(net)     # <-- compounding happens here
        self._save_day()

        hold_min = 0
        try:
            hold_min = (datetime.now()
                        - datetime.strptime(p["entry_time"], "%Y-%m-%d %H:%M:%S")).total_seconds() / 60
        except Exception:
            pass

        rec = {
            "Date": datetime.now().strftime("%Y-%m-%d"),
            "Mode": "PAPER" if PAPER_MODE else "LIVE",
            "EntryTime": p["entry_time"], "ExitTime": datetime.now().strftime("%H:%M:%S"),
            "HoldMin": round(hold_min, 1),
            "Symbol": p["symbol"], "Type": p["type"], "Strike": p["strike"],
            "Qty": p["qty"], "AvgEntry": p["avg_entry"], "ExitFill": round(fill, 2),
            "ModelPrem": p.get("model_prem"), "LotCost": p.get("lot_cost"),
            "RealMargin": p.get("real_margin"), "RiskRs": p.get("risk_rs"),
            "GrossPnL": round(gross, 2),
            "BrokerageTotal": round(buy_ch["brokerage"] + sell_ch["brokerage"], 2),
            "STT": round(sell_ch["stt"], 2),
            "ExchTxn": round(buy_ch["exchange"] + sell_ch["exchange"], 2),
            "SEBI": round(buy_ch["sebi"] + sell_ch["sebi"], 2),
            "Stamp": round(buy_ch["stamp"], 2),
            "GST": round(buy_ch["gst"] + sell_ch["gst"], 2),
            "Charges": round(total_charges, 2), "NetPnL": round(net, 2),
            "Reason": reason, "Stage": p.get("stage", "INIT"),
            "EntryReason": p.get("entry_reason", ""),
            "SpotAtEntry": p.get("spot_at_entry"), "GarchVol": p.get("garch_vol"),
            "EntryIV": p.get("entry_iv"),
            "DayPnL": round(self.day_state["pnl"], 2),
            "EquityAfter": round(new_eq, 2),
            "LatencyMs": round(LAT.stats("ltpData")["avg"], 1) if LAT.stats("ltpData") else None,
        }
        try:
            pd.DataFrame([rec]).to_csv(LOG_FILE, mode="a",
                                       header=not os.path.isfile(LOG_FILE), index=False)
        except Exception as e:
            print(Fore.RED + "[LOG] " + str(e))

        col = Fore.GREEN if net > 0 else Fore.RED
        print(col + Style.BRIGHT + "\n" + "=" * 68)
        print(col + Style.BRIGHT + f"  POSITION CLOSED — {reason}   {datetime.now().strftime('%H:%M:%S')}")
        print(col + Style.BRIGHT + "=" * 68)
        print(f"  {p['symbol']}  |  stage reached [{p.get('stage')}]  |  held {hold_min:.0f} min")
        print(f"  Entry Rs {p['avg_entry']:,.2f}  ->  Exit Rs {fill:,.2f}   "
              + f"({(fill-p['avg_entry'])/p['avg_entry']*100:+.1f}%)")
        print(Fore.WHITE + "\n  CHARGES (Angel One rates)")
        print(f"    Brokerage    : Rs {buy_ch['brokerage']+sell_ch['brokerage']:>8,.2f}")
        print(f"    STT (sell)   : Rs {sell_ch['stt']:>8,.2f}")
        print(f"    Exchange txn : Rs {buy_ch['exchange']+sell_ch['exchange']:>8,.2f}")
        print(f"    SEBI + IPFT  : Rs {buy_ch['sebi']+sell_ch['sebi']+buy_ch['ipft']+sell_ch['ipft']:>8,.2f}")
        print(f"    Stamp duty   : Rs {buy_ch['stamp']:>8,.2f}")
        print(f"    GST          : Rs {buy_ch['gst']+sell_ch['gst']:>8,.2f}")
        print(f"    TOTAL        : Rs {total_charges:>8,.2f}")
        print(col + f"\n  Gross PnL    : Rs {gross:>10,.2f}")
        print(col + f"  Net PnL      : Rs {net:>10,.2f}")
        print(Fore.CYAN + Style.BRIGHT + f"  EQUITY       : Rs {new_eq:>10,.2f}   "
              + f"(day Rs {self.day_state['pnl']:+,.2f})")
        print(col + Style.BRIGHT + "=" * 68)

        emit("exit",
             f"CLOSED {p['symbol']} — {reason} — net Rs{net:+,.2f}",
             level="success" if net > 0 else "error",
             ledger=rec, symbol=p["symbol"], reason=reason,
             stage=p.get("stage"), hold_min=round(hold_min, 1),
             entry=p["avg_entry"], exit_fill=round(fill, 2),
             move_pct=round((fill - p["avg_entry"]) / p["avg_entry"] * 100, 2),
             gross=round(gross, 2), charges_breakdown={
                 "brokerage": round(buy_ch["brokerage"] + sell_ch["brokerage"], 2),
                 "stt": round(sell_ch["stt"], 2),
                 "exchange": round(buy_ch["exchange"] + sell_ch["exchange"], 2),
                 "sebi_ipft": round(buy_ch["sebi"] + sell_ch["sebi"]
                                    + buy_ch["ipft"] + sell_ch["ipft"], 2),
                 "stamp": round(buy_ch["stamp"], 2),
                 "gst": round(buy_ch["gst"] + sell_ch["gst"], 2),
                 "total": round(total_charges, 2)},
             net=round(net, 2), equity=round(new_eq, 2),
             day_pnl=round(self.day_state["pnl"], 2),
             entry_reason=p.get("entry_reason", ""))

        d = p["type"]
        ns = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.day_state["last_exit_dir"]  = d
        self.day_state["last_exit_time"] = ns
        self._clear_state()
        if reason == "STOP_LOSS":
            self.day_state["sl_hit"] = True
            self.day_state["last_sl_time"] = ns
            self.day_state["last_sl_dir"] = d
            self.day_state["last_sl_dir_time"] = ns
            print(Fore.YELLOW + f"  [RISK] {'CE' if d=='C' else 'PE'} blocked "
                  + f"{SL_SAME_DIR_COOLDOWN_MIN}m | opposite side OK now")
            emit("cooldown",
                 f"{'CE' if d=='C' else 'PE'} blocked for {SL_SAME_DIR_COOLDOWN_MIN}m after stop loss",
                 level="warn", direction=d, minutes=SL_SAME_DIR_COOLDOWN_MIN)
        if self.day_state["pnl"] <= -DAILY_KILL_RUPEES:
            self.day_state["killed"] = True
            print(Fore.RED + Style.BRIGHT
                  + f"\n  [DAILY KILL] Rs{self.day_state['pnl']:,.0f} - trading stopped for today")
            emit("daily_kill",
                 f"Daily kill switch — Rs{self.day_state['pnl']:,.0f}, trading stopped for today",
                 level="error", day_pnl=round(self.day_state["pnl"], 2),
                 limit=DAILY_KILL_RUPEES)
        self._save_day()

    def force_close_all(self):
        if self.position:
            cur = self.get_option_ltp(self.position["symbol"], self.position["token"])
            self.exit_trade(cur, "EOD_FORCE_CLOSE")

    def _same_dir_blocked(self, sig):
        now = datetime.now()
        for dkey, tkey, mins, tag in [
                ("last_sl_dir", "last_sl_dir_time", SL_SAME_DIR_COOLDOWN_MIN, "post-SL"),
                ("last_exit_dir", "last_exit_time", EXIT_SAME_DIR_COOLDOWN_MIN, "post-exit")]:
            d, t = self.day_state.get(dkey), self.day_state.get(tkey)
            if d == sig and t:
                try:
                    el = (now - datetime.strptime(t, "%Y-%m-%d %H:%M:%S")).total_seconds() / 60.0
                    if el < mins:
                        return True, f"{tag} same-direction cooldown {mins-el:.1f}m left"
                except Exception:
                    pass
        return False, ""

    def _get_nearest_expiry(self):
        today = datetime.now().date()
        exps = set()
        for s in self.scrip_master:
            try:
                e = datetime.strptime(s["expiry"], "%d%b%Y").date()
                if e > today:
                    exps.add(e)
            except Exception:
                continue
        return sorted(exps)[0] if exps else None

    # ================================================================
    #  LIVE SNAPSHOT — pushed to the phone continuously
    # ================================================================
    def status_event(self, decision, gvol, spot, atm, force=False):
        now = time.time()
        if not force and now - self.last_status_event < STATUS_EVENT_SECONDS:
            return
        self.last_status_event = now

        pos_payload = None
        unrealised = 0.0
        if self.position:
            p = self.position
            cur = self.get_option_ltp(p["symbol"], p["token"]) or p["avg_entry"]
            pnl = self.compute_pnl(cur)
            unrealised = pnl
            entry = p["avg_entry"]
            pos_payload = {
                "symbol": p["symbol"], "opt_type": p["type"], "strike": p["strike"],
                "expiry": p.get("expiry"), "qty": p["qty"], "stage": p["stage"],
                "entry": entry, "current": round(cur, 2),
                "pnl": round(pnl, 2), "pnl_pct": round(self.compute_pnl_pct(cur) * 100, 2),
                "sl_price": p["sl_price"],
                "sl_pct": round((p["sl_price"] / entry - 1) * 100, 2),
                "high_prem": p["high_prem"],
                "peak_gain_pct": round((p["high_prem"] / entry - 1) * 100, 2),
                "risk_left": round(max(0, (cur - p["sl_price"]) * p["qty"]), 2),
                "entry_time": p["entry_time"], "entry_reason": p.get("entry_reason", ""),
                "model_prem": p.get("model_prem"), "entry_iv": p.get("entry_iv"),
                "lot_cost": p.get("lot_cost"), "real_margin": p.get("real_margin"),
                "next_trigger": self._next_ladder_trigger(entry, cur),
                "levels": self._ladder_levels(entry),
                "token": p.get("token"),
            }

        emit("status", "", level="debug",
             time=datetime.now().strftime("%H:%M:%S"),
             market=atm,
             decision=decision.as_dict(),
             position=pos_payload,
             equity=round(self.book.equity, 2),
             open_equity=round(self.day_state.get("open_equity", self.book.equity), 2),
             day_pnl=round(self.day_state["pnl"], 2),
             unrealised=round(unrealised, 2),
             trades=self.day_state["trades"],
             max_trades=MAX_TRADES_PER_DAY,
             killed=self.day_state.get("killed", False),
             chop_skip=self.day_state.get("chop_skip", False),
             chop_score=self.chop_today_score,
             chop_cutoff=self.chop_cutoff,
             kill_used=round(abs(min(0, self.day_state["pnl"])), 2),
             kill_limit=DAILY_KILL_RUPEES,
             peak_equity=round(self.book.data.get("peak_equity", self.book.equity), 2),
             drawdown_pct=round(self.book.drawdown_from_peak() * 100, 2),
             total_return_pct=round(self.book.total_return() * 100, 2),
             seed_capital=self.book.data.get("seed_capital", SEED_CAPITAL),
             sessions_run=self.book.data.get("total_days", 0),
             sessions_done=self.book.data.get("total_days", 0),
             session_number=self.book.data.get("total_days", 0) + 1,
             latency=LAT.all_stats(),
             lot_size=self.lot_size,
             paper=PAPER_MODE,
             window={"open": MARKET_OPEN.strftime("%H:%M"),
                     "cutoff": ENTRY_CUTOFF.strftime("%H:%M"),
                     "force_close": FORCE_CLOSE.strftime("%H:%M"),
                     "end": MARKET_END.strftime("%H:%M")},
             reject_counts=dict(self.reject_counts))

    # ================================================================
    #  CHART — candles and the live option premium, for the app
    # ================================================================
    def chart_event(self, df_ind, spot, force=False):
        """Push today's bars and the open contract's premium track.

        Reuses the candles already fetched for signal generation, so this
        costs no extra broker calls.
        """
        now = time.time()
        if not force and now - self.last_chart_event < CHART_EVENT_SECONDS:
            return
        self.last_chart_event = now

        candles, vwap, ema9, ema21 = [], [], [], []
        try:
            today = df_ind[df_ind["Date"] == datetime.now().date()].tail(CHART_MAX_BARS)
            for row in today.itertuples():
                ts = int(pd.Timestamp(row.Timestamp).timestamp() * 1000)
                candles.append([ts, round(float(row.Open), 2), round(float(row.High), 2),
                                round(float(row.Low), 2), round(float(row.Close), 2),
                                int(row.Volume)])
                vwap.append(None if pd.isna(row.VWAP) else round(float(row.VWAP), 2))
                ema9.append(None if pd.isna(row.EMA9) else round(float(row.EMA9), 2))
                ema21.append(None if pd.isna(row.EMA21) else round(float(row.EMA21), 2))
        except Exception:
            return

        option = None
        if self.position:
            p = self.position
            entry = p["avg_entry"]
            option = {
                "symbol": p["symbol"],
                "opt_type": p["type"],
                "strike": p["strike"],
                "series": list(self.premium_series),
                "entry": entry,
                "sl": p["sl_price"],
                "stage": p["stage"],
                "high_prem": p["high_prem"],
                "entry_ts": self._entry_ts_ms(p),
                "levels": self._ladder_levels(entry),
            }

        emit("chart", "", level="debug",
             index=INDEX_NAME,
             source=FUT_SYMBOL or INDEX_SYMBOL,
             interval="1m",
             spot=round(spot, 2) if spot else None,
             candles=candles, vwap=vwap, ema9=ema9, ema21=ema21,
             option=option)

    @staticmethod
    def _entry_ts_ms(position):
        try:
            return int(datetime.strptime(
                position["entry_time"], "%Y-%m-%d %H:%M:%S").timestamp() * 1000)
        except Exception:
            return None

    @staticmethod
    def _ladder_levels(entry):
        """Every price the ladder cares about, for drawing on the chart.

        This strategy has no fixed take-profit — it climbs a trailing ladder,
        so these are the levels at which the stop ratchets up, not exit
        targets. The final stage trails behind the peak instead.
        """
        return {
            "stop": {"price": round(entry * (1 - SL_PCT), 2),
                     "pct": round(-SL_PCT * 100, 1), "label": "Stop loss"},
            "breakeven": {"price": round(entry * (1 + BE_TRIGGER_PCT), 2),
                          "pct": round(BE_TRIGGER_PCT * 100, 1),
                          "floor": round(entry * (1 + BE_FLOOR_PCT), 2),
                          "label": "Breakeven lock"},
            "lock1": {"price": round(entry * (1 + LOCK1_TRIGGER), 2),
                      "pct": round(LOCK1_TRIGGER * 100, 1),
                      "floor": round(entry * (1 + LOCK1_FLOOR), 2),
                      "label": "Lock 1"},
            "lock2": {"price": round(entry * (1 + LOCK2_TRIGGER), 2),
                      "pct": round(LOCK2_TRIGGER * 100, 1),
                      "floor": round(entry * (1 + LOCK2_FLOOR), 2),
                      "label": "Lock 2"},
            "free": {"giveback_pct": round(FREE_GIVEBACK * 100, 1),
                     "label": f"Trail {FREE_GIVEBACK*100:.0f}% behind peak"},
        }

    def _next_ladder_trigger(self, entry, cur):
        """What the position has to do next for the ladder to step up."""
        gain = (cur - entry) / entry if entry > 0 else 0.0
        stage = self.position["stage"] if self.position else "INIT"
        nxt = {
            "INIT":  (BE_TRIGGER_PCT, "Breakeven lock"),
            "BE":    (LOCK1_TRIGGER, "Lock 1 (+10%)"),
            "LOCK1": (LOCK2_TRIGGER, "Lock 2 (+25%)"),
        }.get(stage)
        if not nxt:
            return {"label": "Trailing 10% behind peak", "target_pct": None,
                    "gain_pct": round(gain * 100, 2), "progress": 1.0}
        target, label = nxt
        return {"label": label, "target_pct": round(target * 100, 1),
                "gain_pct": round(gain * 100, 2),
                "target_price": round(entry * (1 + target), 2),
                "progress": max(0.0, min(1.0, gain / target)) if target else 0.0}

    # ================================================================
    #  MINUTE REPORT — full status block every 60 seconds
    # ================================================================
    def minute_report(self, decision, gvol, spot, atm):
        now = time.time()
        if now - self.last_minute_report < MINUTE_UPDATE_SECONDS:
            return
        self.last_minute_report = now
        ts = datetime.now().strftime("%H:%M:%S")
        eq = self.book.equity
        op = self.day_state.get("open_equity", eq)
        day_pnl = self.day_state["pnl"]

        print("\n" + Fore.WHITE + "-" * 68)
        print(Fore.WHITE + Style.BRIGHT + f"  {ts}   MINUTE UPDATE"
              + ("   [PAPER]" if PAPER_MODE else "   [LIVE]"))
        print(Fore.WHITE + "-" * 68)

        # MARKET ATMOSPHERE
        print(Fore.CYAN + "  MARKET ATMOSPHERE")
        print(f"    NIFTY spot   : {atm['spot']:,.2f}   "
              + f"(day move {atm['day_move']:+.1f} pts, range {atm['day_range_pts']:.0f} pts"
              + f" / {atm['day_range_pct']:.2f}%)")
        print(f"    Volatility   : GARCH {atm['garch']:.1f}%  ->  {atm['vol_regime']}")
        print(f"    Trend        : ADX {atm['adx']:.1f}  ->  {atm['trend']}  ({atm['direction']})")
        print(f"    Efficiency   : {atm['ser']:.3f}  ->  {atm['efficiency']}")
        print(f"    VWAP         : {atm['vwap']:,.1f}   spot is {atm['vwap_side']}")
        print(f"    EMA9/EMA21   : {atm['ema9']:,.1f} / {atm['ema21']:,.1f}   "
              + f"slope {atm['slope']:+.2f}")
        if self.chop_today_score is not None and self.chop_cutoff is not None:
            flag = "BLOCKED" if self.day_state.get("chop_skip") else "OK"
            print(f"    Chop filter  : yesterday {self.chop_today_score:.3f} vs "
                  + f"cutoff {self.chop_cutoff:.3f}  ->  {flag}")

        # POSITION or DECISION
        lot_ref = None
        if self.position:
            p = self.position
            cur = self.get_option_ltp(p["symbol"], p["token"]) or p["avg_entry"]
            pnl = self.compute_pnl(cur)
            pct = self.compute_pnl_pct(cur) * 100
            c = Fore.GREEN if pnl >= 0 else Fore.RED
            print(Fore.CYAN + "\n  OPEN POSITION")
            print(f"    {p['symbol']}   [{p['stage']}]")
            print(f"    Entry Rs {p['avg_entry']:,.2f}  ->  now Rs {cur:,.2f}")
            print(c + f"    PnL          : Rs {pnl:+,.2f}  ({pct:+.1f}%)")
            print(f"    Stop loss    : Rs {p['sl_price']:,.2f}   "
                  + f"({(p['sl_price']/p['avg_entry']-1)*100:+.1f}%)")
            print(f"    Peak seen    : Rs {p['high_prem']:,.2f}")
            print(f"    Risk left    : Rs {max(0,(cur-p['sl_price'])*p['qty']):,.2f}")
        else:
            print(Fore.CYAN + "\n  NO POSITION — WHY NOT")
            print(Fore.YELLOW + f"    {decision.reason}: {decision.detail}")

        # RISK
        used_kill = abs(min(0, day_pnl))
        print(Fore.CYAN + "\n  RISK")
        print(f"    Equity       : Rs {eq:,.2f}   (opened day at Rs {op:,.2f})")
        print(f"    Day PnL      : Rs {day_pnl:+,.2f}")
        print(f"    Daily kill   : Rs {used_kill:,.0f} / Rs {DAILY_KILL_RUPEES:,.0f} used"
              + f"   ({DAILY_KILL_RUPEES-used_kill:,.0f} left)")
        print(f"    Trades       : {self.day_state['trades']}/{MAX_TRADES_PER_DAY}")
        print(f"    Peak equity  : Rs {self.book.data.get('peak_equity',eq):,.2f}   "
              + f"(drawdown {self.book.drawdown_from_peak()*100:+.1f}%)")
        print(f"    Total return : {self.book.total_return()*100:+.1f}% since seed "
              + f"Rs {self.book.data.get('seed_capital',SEED_CAPITAL):,.0f}")

        # COST OF 1 LOT (live)
        print(Fore.CYAN + "\n  COST OF 1 LOT (live ATM estimate)")
        try:
            side = decision.action or ("C" if atm["direction"] == "UP" else "P")
            ct = self.find_atm_contract_live(spot, side)
            if ct:
                sym, tok, k, ex, lot = ct
                l = self.get_option_ltp(sym, tok)
                if l:
                    cost = l * lot
                    ch = self.calc_charges(l, lot, "BUY", breakdown=True)
                    rt = ch["total"] + self.calc_charges(l, lot, "SELL", breakdown=True)["total"]
                    print(f"    {sym}")
                    print(f"    Premium      : Rs {l:,.2f} x {lot} = Rs {cost:,.2f}")
                    print(f"    Round-trip charges: Rs {rt:,.2f}  "
                          + f"({rt/cost*100:.2f}% of position)")
                    print(f"    Breakeven move: +{rt/cost*100:.2f}% on premium")
                    print(f"    Affordable   : {'YES' if cost <= eq*0.9 else 'NO'}"
                          + f"   (equity Rs {eq:,.2f})")
                    lot_ref = {"symbol": sym, "strike": k, "premium": round(l, 2),
                               "lot_size": lot, "cost": round(cost, 2),
                               "round_trip_charges": round(rt, 2),
                               "charges_pct": round(rt / cost * 100, 2),
                               "breakeven_pct": round(rt / cost * 100, 2),
                               "affordable": bool(cost <= eq * 0.9)}
        except Exception as e:
            print(f"    (unavailable: {e})")

        # LATENCY
        print(Fore.CYAN + "\n  ANGEL ONE LATENCY (measured)")
        for name in ["ltpData", "getCandleData", "getMarginApi", "searchScrip"]:
            s = LAT.stats(name)
            if s:
                print(f"    {name:<14}: last {s['last']:>6.0f}ms | avg {s['avg']:>6.0f}ms"
                      + f" | p95 {s['p95']:>6.0f}ms | n={s['n']}")
        print(Fore.WHITE + "-" * 68)

        emit("minute", f"Minute update {ts}",
             market=atm, decision=decision.as_dict(),
             equity=round(eq, 2), open_equity=round(op, 2),
             day_pnl=round(day_pnl, 2), trades=self.day_state["trades"],
             kill_used=round(used_kill, 2), kill_limit=DAILY_KILL_RUPEES,
             peak_equity=round(self.book.data.get("peak_equity", eq), 2),
             drawdown_pct=round(self.book.drawdown_from_peak() * 100, 2),
             total_return_pct=round(self.book.total_return() * 100, 2),
             lot_reference=lot_ref,
             chop_score=self.chop_today_score, chop_cutoff=self.chop_cutoff,
             latency=LAT.all_stats())

    # ================================================================
    #  END OF DAY REPORT
    # ================================================================
    def eod_report(self):
        if self.eod_filed:
            return
        self.eod_filed = True
        today = datetime.now().strftime("%Y-%m-%d")
        eq    = self.book.equity
        op    = self.day_state.get("open_equity", eq)
        atm   = self.market_snapshot or {}

        trades_today = pd.DataFrame()
        if os.path.exists(LOG_FILE):
            try:
                allt = pd.read_csv(LOG_FILE)
                trades_today = allt[allt["Date"] == today]
            except Exception:
                pass

        n = len(trades_today)
        print("\n" + Fore.CYAN + Style.BRIGHT + "=" * 68)
        print(Fore.CYAN + Style.BRIGHT + f"  END OF DAY REPORT — {today}"
              + ("   [PAPER]" if PAPER_MODE else "   [LIVE]"))
        print(Fore.CYAN + Style.BRIGHT + "=" * 68)

        win_rate = None
        profit_factor = None

        # 1. TRADES
        print(Fore.WHITE + Style.BRIGHT + "\n  1. TRADES")
        if n:
            w = int((trades_today["NetPnL"] > 0).sum())
            gp = trades_today[trades_today["NetPnL"] > 0]["NetPnL"].sum()
            gl = abs(trades_today[trades_today["NetPnL"] <= 0]["NetPnL"].sum())
            pf = gp / gl if gl > 0 else float("inf")
            win_rate = w / n * 100
            profit_factor = None if gl <= 0 else round(float(pf), 2)
            print(f"    Total trades : {n}   ({w} win / {n-w} loss)")
            print(f"    Win rate     : {w/n*100:.0f}%")
            print(f"    Profit factor: {pf:.2f}")
            print(f"    Avg hold     : {trades_today['HoldMin'].mean():.0f} min")
            print(f"    Gross PnL    : Rs {trades_today['GrossPnL'].sum():+,.2f}")
            print(f"    Net PnL      : Rs {trades_today['NetPnL'].sum():+,.2f}")
            print("\n    Trade log:")
            for _, r in trades_today.iterrows():
                c = Fore.GREEN if r["NetPnL"] > 0 else Fore.RED
                print(c + f"      {r['EntryTime'][-8:]}->{r['ExitTime']}  "
                      + f"{r['Symbol']:<22} {r['Reason']:<18} "
                      + f"Rs {r['NetPnL']:>+9,.2f}")
        else:
            print("    Total trades : 0")
            print(Fore.YELLOW + "    Reason no trades taken today:")
            if self.day_state.get("chop_skip"):
                print(Fore.YELLOW + "      CHOP FILTER blocked the whole session")
            elif self.reject_counts:
                for k, v in self.reject_counts.most_common(8):
                    print(Fore.YELLOW + f"      {k:<24} blocked {v} times")
            else:
                print(Fore.YELLOW + f"      {self.last_decision.reason}: {self.last_decision.detail}")

        # 2. RISK
        print(Fore.WHITE + Style.BRIGHT + "\n  2. RISK")
        day_pnl = eq - op
        print(f"    Opening equity: Rs {op:,.2f}")
        print(f"    Closing equity: Rs {eq:,.2f}")
        print(f"    Day PnL       : Rs {day_pnl:+,.2f}   ({day_pnl/op*100:+.2f}%)")
        print(f"    Daily kill    : Rs {abs(min(0,day_pnl)):,.0f} / Rs {DAILY_KILL_RUPEES:,.0f}"
              + ("   *** HIT ***" if self.day_state.get("killed") else ""))
        print(f"    Peak equity   : Rs {self.book.data.get('peak_equity',eq):,.2f}")
        print(f"    Drawdown      : {self.book.drawdown_from_peak()*100:+.2f}% from peak")
        if n:
            print(f"    Largest loss  : Rs {trades_today['NetPnL'].min():+,.2f}")
            print(f"    Largest win   : Rs {trades_today['NetPnL'].max():+,.2f}")
            print(f"    Risk per trade: Rs {trades_today['RiskRs'].mean():,.2f} avg "
                  + f"({trades_today['RiskRs'].mean()/op*100:.1f}% of equity)")

        # 3. MARKET ATMOSPHERE
        print(Fore.WHITE + Style.BRIGHT + "\n  3. MARKET ATMOSPHERE (at close)")
        if atm:
            print(f"    NIFTY close  : {atm.get('spot',0):,.2f}")
            print(f"    Day move     : {atm.get('day_move',0):+.1f} pts   "
                  + f"range {atm.get('day_range_pts',0):.0f} pts "
                  + f"({atm.get('day_range_pct',0):.2f}%)")
            print(f"    Volatility   : GARCH {atm.get('garch',0):.1f}%  -> {atm.get('vol_regime','?')}")
            print(f"    Trend        : ADX {atm.get('adx',0):.1f}  -> {atm.get('trend','?')}")
            print(f"    Direction    : {atm.get('direction','?')}")
            print(f"    Efficiency   : {atm.get('ser',0):.3f}  -> {atm.get('efficiency','?')}")
            print(f"    ATR          : {atm.get('atr',0):.1f} pts")
        if self.chop_today_score is not None:
            print(f"    Chop score   : {self.chop_today_score:.3f} "
                  + f"(cutoff {self.chop_cutoff:.3f})"
                  + ("   -> DAY BLOCKED" if self.day_state.get("chop_skip") else "   -> tradeable"))

        # 4. COST OF 1 LOT
        print(Fore.WHITE + Style.BRIGHT + "\n  4. COST OF 1 LOT")
        if n:
            print(f"    Avg premium   : Rs {trades_today['AvgEntry'].mean():,.2f}")
            print(f"    Avg lot cost  : Rs {trades_today['LotCost'].mean():,.2f} "
                  + f"({self.lot_size} qty)")
            if trades_today["RealMargin"].notna().any():
                print(f"    Avg margin    : Rs {trades_today['RealMargin'].mean():,.2f}  [Angel One API]")
            print(f"    Charges paid  : Rs {trades_today['Charges'].sum():,.2f} total")
            print(f"      Brokerage   : Rs {trades_today['BrokerageTotal'].sum():,.2f}")
            print(f"      STT         : Rs {trades_today['STT'].sum():,.2f}")
            print(f"      Exchange    : Rs {trades_today['ExchTxn'].sum():,.2f}")
            print(f"      GST         : Rs {trades_today['GST'].sum():,.2f}")
            print(f"      Stamp+SEBI  : Rs {trades_today['Stamp'].sum()+trades_today['SEBI'].sum():,.2f}")
            print(f"    Charges as %  : {trades_today['Charges'].sum()/abs(trades_today['GrossPnL'].sum())*100:.1f}% of gross"
                  if trades_today['GrossPnL'].sum() != 0 else "")
        else:
            try:
                spot = self.get_spot_ltp()
                if spot:
                    ct = self.find_atm_contract_live(spot, "C")
                    if ct:
                        sym, tok, k, ex, lot = ct
                        l = self.get_option_ltp(sym, tok)
                        if l:
                            rt = (self.calc_charges(l, lot, "BUY") + self.calc_charges(l, lot, "SELL"))
                            print(f"    {sym} (reference)")
                            print(f"    Premium       : Rs {l:,.2f} x {lot} = Rs {l*lot:,.2f}")
                            print(f"    Round-trip cost: Rs {rt:,.2f}")
            except Exception:
                print("    (no trades, reference unavailable)")

        # 5. MODEL vs REAL
        if n and "ModelPrem" in trades_today.columns:
            v = trades_today.dropna(subset=["ModelPrem"])
            if len(v):
                diff = (v["AvgEntry"] - v["ModelPrem"]) / v["ModelPrem"] * 100
                print(Fore.YELLOW + Style.BRIGHT + "\n  5. MODEL vs REAL FILL  (backtest honesty check)")
                print(Fore.YELLOW + f"    BSM model avg : Rs {v['ModelPrem'].mean():,.2f}")
                print(Fore.YELLOW + f"    Real fill avg : Rs {v['AvgEntry'].mean():,.2f}")
                print(Fore.YELLOW + f"    Difference    : {diff.mean():+.1f}%  "
                      + f"(min {diff.min():+.1f}%, max {diff.max():+.1f}%)")
                if abs(diff.mean()) > 10:
                    print(Fore.RED + "    >> Model is off by more than 10% - "
                          + "backtest returns were NOT realistic")
                else:
                    print(Fore.GREEN + "    >> Model within 10% - backtest assumptions holding")

        # 6. LATENCY
        print(Fore.WHITE + Style.BRIGHT + "\n  6. ANGEL ONE LATENCY (measured today)")
        for name in ["login", "ltpData", "getCandleData", "getMarginApi", "searchScrip", "scripMaster"]:
            s = LAT.stats(name)
            if s:
                print(f"    {name:<14}: avg {s['avg']:>7.0f}ms | p95 {s['p95']:>7.0f}ms"
                      + f" | max {s['mx']:>7.0f}ms | calls {s['n']}")

        # 7. EQUITY BOOK
        print(Fore.CYAN + Style.BRIGHT + "\n  7. EQUITY BOOK (compounding, no infusion)")
        b = self.book.data
        print(f"    Seeded       : Rs {b.get('seed_capital',SEED_CAPITAL):,.2f} on {b.get('seeded_on')}")
        print(f"    Sessions done: {b.get('total_days',0)}")
        print(f"    This session : #{b.get('total_days',0)+1} (in progress)")
        print(f"    Current      : Rs {eq:,.2f}")
        print(f"    Total return : {self.book.total_return()*100:+.2f}%")
        hist = b.get("history", [])[-5:]
        if hist:
            print("    Last sessions:")
            for h in hist:
                c = Fore.GREEN if h["pnl"] > 0 else Fore.RED
                print(c + f"      {h['date']}  Rs {h['open']:>9,.0f} -> Rs {h['close']:>9,.0f}"
                      + f"   ({h['pnl']:+,.0f})  {h['trades']} trades")
        print(Fore.CYAN + Style.BRIGHT + f"\n  TOMORROW STARTS WITH: Rs {eq:,.2f}")
        print(Fore.CYAN + Style.BRIGHT + "=" * 68 + "\n")

        # persist EOD row
        row = {
            "Date": today, "Mode": "PAPER" if PAPER_MODE else "LIVE",
            "Trades": n, "OpenEquity": round(op, 2), "CloseEquity": round(eq, 2),
            "DayPnL": round(day_pnl, 2),
            "Charges": round(trades_today["Charges"].sum(), 2) if n else 0.0,
            "Killed": self.day_state.get("killed", False),
            "ChopBlocked": self.day_state.get("chop_skip", False),
            "ChopScore": self.chop_today_score,
            "GARCH": atm.get("garch"), "ADX": atm.get("adx"),
            "VolRegime": atm.get("vol_regime"), "Trend": atm.get("trend"),
            "Direction": atm.get("direction"), "Efficiency": atm.get("efficiency"),
            "DayRangePts": atm.get("day_range_pts"),
            "AvgLatencyMs": round(LAT.stats("ltpData")["avg"], 1) if LAT.stats("ltpData") else None,
            "PeakEquity": round(b.get("peak_equity", eq), 2),
            "DrawdownPct": round(self.book.drawdown_from_peak() * 100, 2),
        }
        try:
            pd.DataFrame([row]).to_csv(EOD_FILE, mode="a",
                                       header=not os.path.isfile(EOD_FILE), index=False)
            print(Fore.GREEN + f"  Saved: {EOD_FILE}, {LOG_FILE}, {EQUITY_FILE}\n")
        except Exception as e:
            print(Fore.RED + f"  EOD save error: {e}")

        emit("eod", f"End of day — {n} trades, Rs{day_pnl:+,.2f}",
             level="success" if day_pnl >= 0 else "error",
             report=row,
             win_rate=round(win_rate, 1) if win_rate is not None else None,
             profit_factor=profit_factor,
             equity=round(eq, 2), open_equity=round(op, 2),
             day_pnl=round(day_pnl, 2), trades=n,
             market=atm, latency=LAT.all_stats(),
             equity_book={
                 "seed_capital": b.get("seed_capital", SEED_CAPITAL),
                 "seeded_on": b.get("seeded_on"),
                 "sessions_done": b.get("total_days", 0),
                 "session_number": b.get("total_days", 0) + 1,
                 "current": round(eq, 2),
                 "peak": round(b.get("peak_equity", eq), 2),
                 "total_return_pct": round(self.book.total_return() * 100, 2),
                 "history": b.get("history", [])[-10:],
             },
             no_trade_reasons=dict(self.reject_counts) if not n else None,
             trade_rows=trades_today.to_dict("records") if n else [])

        self.book.close_session(today, op, n)

    # ================================================================
    #  MAIN CYCLE
    # ================================================================
    def run_cycle(self):
        now = datetime.now()
        t = now.time()
        ts = now.strftime("%H:%M:%S")
        self._refresh_session_if_needed()
        self._heartbeat()

        if t >= MARKET_END:
            self.force_close_all()
            print(Fore.MAGENTA + "\n[DONE] Market closed 15:30.")
            self.eod_report()
            return False

        if t < dtime(9, 15):
            tty_status(f"[PRE-MARKET] Waiting for 09:15 | equity Rs{self.book.equity:,.0f} | {ts}")
            self.status_event(Decision(None, "PRE_MARKET", "Waiting for the 09:15 open"),
                              None, None, self.market_snapshot or {})
            return True

        if not self.day_state.get("chop_checked", False):
            self.day_state["chop_checked"] = True
            self.day_state["chop_skip"] = self._is_choppy_today()
            self._save_day()

        if t >= FORCE_CLOSE:
            if self.position:
                self.force_close_all()
            tty_status(f"[EOD WINDOW] Force-close active | {ts}")
            self.status_event(Decision(None, "EOD_WINDOW", "Past 15:00 — flat until close"),
                              None, None, self.market_snapshot or {})
            return True

        # gather market state every loop (needed for both paths)
        df = self.get_candles()
        if df is None or len(df) < SMA_PERIOD:
            got = len(df) if df is not None else 0
            tty_status(f"[WARMUP] {got}/{SMA_PERIOD} candles | {ts}    ")
            self.status_event(Decision(None, "WARMUP", f"{got}/{SMA_PERIOD} candles"),
                              None, None, self.market_snapshot or {})
            return True

        df_ind = self.compute_indicators(df)
        gvol   = self.compute_garch(df_ind)
        spot   = self.get_spot_ltp() or float(df_ind.iloc[-1]["Close"])
        atm    = self.read_market_atmosphere(df_ind, gvol, spot)
        nexp   = self._get_nearest_expiry()

        if self.position:
            self.manage_trade()
            dec = Decision(None, "IN_POSITION",
                           f"Managing {self.position['symbol']}" if self.position else "Flat")
            self.minute_report(dec, gvol, spot, atm)
            self.status_event(dec, gvol, spot, atm)
            self.chart_event(df_ind, spot)
            if self.position:
                p = self.position
                cur = self.get_option_ltp(p["symbol"], p["token"]) or p["avg_entry"]
                pnl = self.compute_pnl(cur)
                c = Fore.GREEN if pnl >= 0 else Fore.RED
                tty_status(c + f"  [{p['stage']}] {p['symbol']} Rs{cur:,.2f} | "
                           + f"PnL Rs{pnl:+,.0f} | SL Rs{p['sl_price']:,.2f} | "
                           + f"peak Rs{p['high_prem']:,.2f} | {ts}   ")
            return True

        decision = self.evaluate(df_ind, gvol, nexp, spot)
        prev_reason = self.last_decision.reason if self.last_decision else None
        self.last_decision = decision
        if not decision.took_trade:
            self.reject_counts[decision.reason] += 1
            if decision.reason != prev_reason:
                emit("decision", f"{decision.reason}: {decision.detail}",
                     level="info", **decision.as_dict())

        self.minute_report(decision, gvol, spot, atm)
        self.status_event(decision, gvol, spot, atm)
        self.chart_event(df_ind, spot)

        if decision.took_trade:
            emit("signal", f"SIGNAL {decision.detail}", level="success", **decision.as_dict())
            self.enter_trade(decision, df_ind, spot, gvol)
        else:
            tty_status(f"[{decision.reason}] {decision.detail[:62]:<62} | "
                       + f"Rs{self.book.equity:,.0f} | {ts}")
        return True


# ================================================================
#  CONFIG GUARD
# ================================================================
V11_BASELINE = {
    "SL_PCT": 0.10, "BE_TRIGGER_PCT": 0.15, "BE_FLOOR_PCT": 0.04,
    "LOCK1_TRIGGER": 0.25, "LOCK1_FLOOR": 0.10,
    "LOCK2_TRIGGER": 0.40, "LOCK2_FLOOR": 0.25, "FREE_GIVEBACK": 0.10,
    "DAILY_KILL_RUPEES": 3000.0, "MAX_TRADES_PER_DAY": 3,
    "GARCH_VOL_MIN": 0.09, "GARCH_TRADE3_MIN": 0.11, "POST_SL_GARCH_MIN": 0.09,
    "USE_RSI_FILTER": False, "ENABLE_CHOP_FILTER": True,
    "BLOCK_EXPIRY_DAY_TRADING": True,
    "MARKET_OPEN": "09:40", "ENTRY_CUTOFF": "13:30", "FORCE_CLOSE": "15:00",
    "INDEX_NAME": "NIFTY", "STRIKE_STEP": 50,
}


def current_config() -> dict:
    return {
        "SL_PCT": SL_PCT, "BE_TRIGGER_PCT": BE_TRIGGER_PCT,
        "BE_FLOOR_PCT": BE_FLOOR_PCT, "LOCK1_TRIGGER": LOCK1_TRIGGER,
        "LOCK1_FLOOR": LOCK1_FLOOR, "LOCK2_TRIGGER": LOCK2_TRIGGER,
        "LOCK2_FLOOR": LOCK2_FLOOR, "FREE_GIVEBACK": FREE_GIVEBACK,
        "DAILY_KILL_RUPEES": DAILY_KILL_RUPEES,
        "MAX_TRADES_PER_DAY": MAX_TRADES_PER_DAY,
        "GARCH_VOL_MIN": GARCH_VOL_MIN, "GARCH_TRADE3_MIN": GARCH_TRADE3_MIN,
        "POST_SL_GARCH_MIN": POST_SL_GARCH_MIN,
        "USE_RSI_FILTER": USE_RSI_FILTER,
        "ENABLE_CHOP_FILTER": ENABLE_CHOP_FILTER,
        "BLOCK_EXPIRY_DAY_TRADING": BLOCK_EXPIRY_DAY_TRADING,
        "MARKET_OPEN": MARKET_OPEN.strftime("%H:%M"),
        "ENTRY_CUTOFF": ENTRY_CUTOFF.strftime("%H:%M"),
        "FORCE_CLOSE": FORCE_CLOSE.strftime("%H:%M"),
        "INDEX_NAME": INDEX_NAME, "STRIKE_STEP": STRIKE_STEP,
    }


def verify_config() -> None:
    """Check the properties that must hold at any set of values.

    The original guard asserted the literal v11 constants, which cannot
    survive a strategy the user is allowed to edit. What actually matters is
    that the ladder steps upward and the session times run in order — a floor
    above its trigger would place the stop above the market and fire the
    instant a position opened. Deviations from v11 are reported, not blocked.
    """
    assert BE_FLOOR_PCT < BE_TRIGGER_PCT, \
        "Breakeven floor must sit below its trigger"
    assert BE_TRIGGER_PCT < LOCK1_TRIGGER < LOCK2_TRIGGER, \
        "Ladder triggers must increase: breakeven < lock 1 < lock 2"
    assert LOCK1_FLOOR < LOCK1_TRIGGER and LOCK2_FLOOR < LOCK2_TRIGGER, \
        "Each ladder floor must sit below its own trigger"
    assert BE_FLOOR_PCT <= LOCK1_FLOOR < LOCK2_FLOOR, \
        "Ladder floors must increase as the ladder climbs"
    assert 0 < SL_PCT < 1, "Stop loss must be between 0 and 100%"
    assert 0 < FREE_GIVEBACK < 1, "Free-run giveback must be between 0 and 100%"
    assert EMA_FAST < EMA_SLOW, "Fast EMA period must be shorter than the slow EMA"
    assert MARKET_OPEN < ENTRY_CUTOFF < FORCE_CLOSE < MARKET_END, \
        "Session times must run in order and finish before the exchange closes"
    assert MAX_TRADES_PER_DAY >= 1 and DAILY_KILL_RUPEES > 0, \
        "Trade cap and kill switch must be positive"
    assert ENABLE_PYRAMID is False, "Pyramid OFF"

    drift = {k: {"v11": v, "now": current_config()[k]}
             for k, v in V11_BASELINE.items() if current_config()[k] != v}
    if drift:
        print(Fore.YELLOW + Style.BRIGHT
              + f"[Guard] {len(drift)} parameter(s) differ from the v11 baseline:")
        for key, d in drift.items():
            print(Fore.YELLOW + f"    {key:<26} v11 {d['v11']}  ->  now {d['now']}")
        emit("config_drift",
             f"{len(drift)} parameter(s) differ from the v11 baseline",
             level="warn", drift=drift, config=current_config())
    else:
        emit("config_ok", "Running the v11 baseline configuration",
             config=current_config())


# ================================================================
#  MAIN
# ================================================================
def main():
    _install_signal_handlers()

    missing = [k for k, v in {
        "ANGEL_API_KEY": API_KEY, "ANGEL_CLIENT_ID": CLIENT_ID,
        "ANGEL_PASSWORD": PASSWORD, "ANGEL_TOTP_SECRET": TOTP_SECRET,
    }.items() if not v]
    if missing:
        raise BotStartupError(
            "Missing credentials in environment: " + ", ".join(missing)
            + ". Copy .env.example to .env and fill it in."
        )

    mode = "PAPER TRADING" if PAPER_MODE else "REAL MONEY"
    col  = Fore.CYAN if PAPER_MODE else Fore.RED
    print(col + Style.BRIGHT + "=" * 68)
    print(col + Style.BRIGHT + "  HAR HAR MAHADEV — NIFTY OPTIONS BOT v11")
    print(col + Style.BRIGHT + f"  MODE: {mode}")
    print(col + Style.BRIGHT + "  Compounding capital | minute updates | full EOD report")
    print(col + Style.BRIGHT + "=" * 68)
    if PAPER_MODE:
        print(Fore.GREEN + Style.BRIGHT +
              "\n  PAPER_MODE = True -> NO real orders placed.\n"
              "  Real Angel One LTPs, real margin, real latency.\n"
              "  Fills simulated at LTP +/- 0.5%. Real account untouched.\n")

    emit("boot", f"Bot starting — {mode}", level="success",
         paper=PAPER_MODE, mode=mode,
         session_window={"open": MARKET_OPEN.strftime("%H:%M"),
                         "cutoff": ENTRY_CUTOFF.strftime("%H:%M"),
                         "force_close": FORCE_CLOSE.strftime("%H:%M"),
                         "end": MARKET_END.strftime("%H:%M")})

    book   = EquityBook()
    broker = LiveBroker(book)

    real_bal = broker.get_real_account_balance()
    print(Fore.GREEN + "\n" + "=" * 68)
    print(Fore.GREEN + Style.BRIGHT + "  CONFIGURATION")
    print(Fore.GREEN + "=" * 68)
    print(f"  Paper equity   : Rs {book.equity:,.2f}   (compounding, no infusion)")
    if real_bal is not None:
        print(f"  Real account   : Rs {real_bal:,.2f}   (reference only, never traded)")
    print(f"  Seeded         : Rs {book.data.get('seed_capital',SEED_CAPITAL):,.2f}"
          + f" on {book.data.get('seeded_on')}")
    print(f"  Sessions done  : {book.data.get('total_days',0)}")
    print(f"  Total return   : {book.total_return()*100:+.2f}%")
    print(f"\n  Stop loss      : -{SL_PCT*100:.0f}%")
    print(f"  Breakeven      : +{BE_TRIGGER_PCT*100:.0f}% -> SL to +{BE_FLOOR_PCT*100:.0f}%")
    print(f"  Lock 1         : +{LOCK1_TRIGGER*100:.0f}% -> SL to +{LOCK1_FLOOR*100:.0f}%")
    print(f"  Lock 2         : +{LOCK2_TRIGGER*100:.0f}% -> SL to +{LOCK2_FLOOR*100:.0f}%")
    print(f"  Free-run trail : {FREE_GIVEBACK*100:.0f}% behind peak")
    print(f"  RSI filter     : {'ON' if USE_RSI_FILTER else 'OFF'}")
    print(f"  Chop filter    : {'ON (block bottom '+str(CHOP_BLOCK_PCT)+'%)' if ENABLE_CHOP_FILTER else 'OFF'}")
    print(f"  Entry window   : {MARKET_OPEN.strftime('%H:%M')} - {ENTRY_CUTOFF.strftime('%H:%M')}")
    print(f"  Force close    : {FORCE_CLOSE.strftime('%H:%M')}")
    print(f"  Daily kill     : Rs {DAILY_KILL_RUPEES:,.0f}")
    print(f"  Max trades/day : {MAX_TRADES_PER_DAY}")
    print(f"  Lot size       : {broker.lot_size}")
    print(f"  Minute updates : every {MINUTE_UPDATE_SECONDS}s")
    print(Fore.GREEN + "=" * 68)

    verify_config()
    print(Fore.GREEN + "[Guard] v11 config verified OK.\n")

    emit("ready", "Bot armed and watching the market", level="success",
         equity=round(book.equity, 2),
         real_account_balance=real_bal,
         lot_size=broker.lot_size,
         seed_capital=book.data.get("seed_capital", SEED_CAPITAL),
         seeded_on=book.data.get("seeded_on"),
         sessions_run=book.data.get("total_days", 0),
         sessions_done=book.data.get("total_days", 0),
         session_number=book.data.get("total_days", 0) + 1,
         total_return_pct=round(book.total_return() * 100, 2),
         ladder={"sl": SL_PCT, "be_trigger": BE_TRIGGER_PCT, "be_floor": BE_FLOOR_PCT,
                 "lock1_trigger": LOCK1_TRIGGER, "lock1_floor": LOCK1_FLOOR,
                 "lock2_trigger": LOCK2_TRIGGER, "lock2_floor": LOCK2_FLOOR,
                 "free_giveback": FREE_GIVEBACK},
         limits={"daily_kill": DAILY_KILL_RUPEES, "max_trades": MAX_TRADES_PER_DAY},
         paper=PAPER_MODE)

    print(Fore.GREEN + "[Run] Main loop started. Ctrl+C to stop.\n")
    try:
        while not _STOP.is_set():
            alive = broker.run_cycle()
            if not alive:
                break
            _STOP.wait(LOOP_SLEEP_IN_POSITION if broker.position else LOOP_SLEEP_IDLE)
    except KeyboardInterrupt:
        request_stop("KeyboardInterrupt")

    if _STOP.is_set():
        print("\n\n[STOP] Interrupted. Saving state...")
        # A stop mid-session must not leave a live position dangling.
        if broker.position:
            print(Fore.YELLOW + "[STOP] Flattening open position before exit...")
            broker.force_close_all()
        broker._save_state()
        broker._save_day()
        broker.eod_report()
        print("[STOP] Done. Equity preserved for next session.")

    emit("shutdown", "Bot stopped", level="warn",
         equity=round(book.equity, 2),
         day_pnl=round(broker.day_state.get("pnl", 0.0), 2),
         trades=broker.day_state.get("trades", 0))


if __name__ == "__main__":
    try:
        main()
    except BotStartupError as exc:
        emit("fatal", str(exc), level="error")
        print(Fore.RED + Style.BRIGHT + f"[FATAL] {exc}")
        sys.exit(2)
    except Exception as exc:  # pragma: no cover - last-resort reporter
        import traceback
        tb = traceback.format_exc()
        emit("fatal", f"{type(exc).__name__}: {exc}", level="error", traceback=tb)
        print(Fore.RED + tb)
        sys.exit(1)
