"""GANESH KAVACH 50K — Config #5 v3 strategy parameters.

Every value below is carried over verbatim from the backtested single-file
build. Changing any of them invalidates the backtest, so they are constants
here rather than settings: the only runtime-configurable items are the paths
and the paper/live mode.
"""

from __future__ import annotations

import os
from pathlib import Path

BUILD_NAME = "GANESH KAVACH 50K"
BUILD_VERSION = "Config #5 v3"
BANNER = f"गणेश कवच  ·  {BUILD_NAME}  ·  {BUILD_VERSION}"

# ── instrument ────────────────────────────────────────────────────────────────
INDEX_TOKEN, INDEX_EXCH, INDEX_TSYM = "99926000", "NSE", "Nifty 50"
LOT_SIZE, STRIKE_STEP = 65, 50   # NIFTY's lot per the operator's latest build; Angel's scrip master is authoritative at run time
STRIKE_OFFSET = -1  # one strike (50 pts) in the money — the Rs50k-validated strike
DONCHIAN_LB = 90
ENTRY_START, ENTRY_CUTOFF, FORCE_CLOSE = (10, 15), (14, 0), (15, 10)
MIN_DTE, MAX_DTE = 2, 8

MARKET_OPEN, MARKET_CLOSE = (9, 15), (15, 30)

# Angel product code for NFO option buying. CARRYFORWARD (NRML) is what most Angel
# accounts accept for option buying without extra entitlement; switch to "INTRADAY"
# (MIS) only if orders come back rejected with a product/margin error.
PRODUCT_TYPE = "CARRYFORWARD"
ORDER_RETRIES = 2  # re-attempt a failed entry/exit this many times before giving up

# ── data-integrity guards ─────────────────────────────────────────────────────
MAX_FEED_DIVERGENCE_PTS = 25.0  # skip entry if |last candle close - spot LTP| exceeds this
MAX_BAR_AGE_SEC = 180.0  # candle feed considered stale beyond this

# ── clock integrity ───────────────────────────────────────────────────────────
NTP_SERVERS = ("time.google.com", "pool.ntp.org", "time.cloudflare.com")
MAX_CLOCK_DRIFT_SEC = 5.0
HALT_CLOCK_DRIFT_SEC = 30.0
CLOCK_RECHECK_SEC = 900.0

# ── Angel rate limits (requests per second, per endpoint) ─────────────────────
RATE_LIMITS = {
    "ltp": 8.0,
    "candle": 2.0,
    "rms": 1.5,
    "position": 0.8,
    "order": 10.0,
    "orderbook": 1.5,
    "tradebook": 1.5,
    # Option-chain quotes for the dashboard: one call covers the whole chain,
    # and the feed asks every 15s — this cap only stops a runaway loop.
    "quote": 0.5,
}
FUNDS_CACHE_SEC = 10.0
POSITION_CACHE_SEC = 10.0

# ── profit ladder (how a trade takes money off the table) ─────────────────────
TARGET_PTS = 90  # HARD take-profit: exit when the index moves +90 pts in favour
BE_TRIGGER = 0.25  # at +25% premium gain, the stop jumps to break-even
TRAIL_FRAC = 0.35  # past +35% gain, the stop trails 35% behind the peak

# ── stop ladder (how a trade caps a loss) ─────────────────────────────────────
STOP_FRAC = 0.45  # HARD stop-loss: initial exit at -45% of premium

# ── per-trade guards (sizing & single-position risk) ──────────────────────────
DEPLOY_FRACTION = 0.40  # commit 40% of equity per trade
PER_TRADE_EQUITY_CAP = 0.60  # never let one position exceed 60% of equity
PER_TRADE_RISK_RS = 9_000.0  # skip a trade whose worst-case loss exceeds this
MAX_LOTS = 10  # hard concentration cap
MAX_PREMIUM = 600.0  # skip a contract priced above this per unit

# ── day / week guards (kill switches in money terms) ──────────────────────────
MAX_TRADES_DAY = 1
DAILY_LOSS_LIMIT_RS = 12_000.0  # DAILY KILL
WEEKLY_LOSS_LIMIT_RS = 25_000.0  # WEEKLY KILL
CONSEC_LOSS_HALT = 4  # pause new entries for the day after this many losses in a row
DAILY_PROFIT_LOCK_RS = 20_000.0  # once up this much on the day, stop and bank it

# ── portfolio guard (last line) ───────────────────────────────────────────────
MIN_CAPITAL = 0.0  # per the operator's latest build. Sizing still skips any trade that
#                    breaches the equity or risk caps, so a small account waits rather than
#                    over-sizing; a floor that refused to start was a restart loop.
MAX_DRAWDOWN_STOP = 0.45  # halt ALL new entries at -45% from peak equity

# ── loop cadence ──────────────────────────────────────────────────────────────
POLL_SECONDS = 20
POLL_IN_TRADE = 5
POLL_SCANNING = 2
SNAPSHOT_SECONDS = 2.0  # how often engine state is published to the dashboard
EQUITY_SAMPLE_SECONDS = 60.0  # how often a point is added to the equity curve

SCRIP_URL = "https://margincalculator.angelone.in/OpenAPI_File/files/OpenAPIScripMaster.json"


def _data_dir() -> Path:
    d = Path(os.environ.get("MERIDIAN_DATA_DIR", "/var/lib/meridian")).expanduser()
    d.mkdir(parents=True, exist_ok=True)
    return d


DATA_DIR = _data_dir()
DB_PATH = Path(os.environ.get("MERIDIAN_DB_PATH", DATA_DIR / "meridian.db"))
STATE_FILE = DATA_DIR / "gk50k_state.json"
SCRIP_CACHE = DATA_DIR / "gk50k_scrip.json"
TRADE_LOG = DATA_DIR / "gk50k_trades.csv"
TELEMETRY_SPOOL = DATA_DIR / "telemetry_spool.ndjson"

# Angel session identity headers. Public IP is discovered at runtime when unset.
LOCAL_IP = os.environ.get("ANGEL_LOCAL_IP", "127.0.0.1")
PUBLIC_IP = os.environ.get("ANGEL_PUBLIC_IP", "")
MAC_ADDR = os.environ.get("ANGEL_MAC_ADDR", "00:00:00:00:00:00")
