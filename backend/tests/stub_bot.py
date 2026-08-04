"""A fake bot for exercising the supervisor without touching a broker.

Emits the same shapes the real algorithm does — plain terminal lines and
`@@EVT@@` records — so runner.py's parsing, persistence and shutdown paths get
tested for real. Behaviour is chosen with STUB_MODE:

    normal  emit a session's worth of events, then idle until signalled
    crash   die with a non-zero exit code
    fatal   fail at startup the way BotStartupError does (exit 2)
"""
from __future__ import annotations

import os
import signal
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.bot.emitter import emit  # noqa: E402

MODE = os.getenv("STUB_MODE", "normal")
_stop = threading.Event()


def _handler(signum, _frame):
    emit("stopping", f"Stop requested ({signal.Signals(signum).name})", level="warn")
    _stop.set()


for _sig in (signal.SIGTERM, signal.SIGINT):
    signal.signal(_sig, _handler)


def main() -> None:
    if MODE == "fatal":
        emit("fatal", "Missing credentials in environment: ANGEL_API_KEY", level="error")
        print("[FATAL] Missing credentials in environment: ANGEL_API_KEY")
        sys.exit(2)

    print("=" * 68)
    print("  HAR HAR MAHADEV — NIFTY OPTIONS BOT v11")
    print("  MODE: PAPER TRADING")
    print("=" * 68)
    emit("boot", "Bot starting — PAPER TRADING", level="success", paper=True)
    # Report the strategy environment we were launched with, so the tests can
    # prove overrides actually reach the child process.
    emit("config_seen", "Strategy environment received", level="debug",
         env={k: os.getenv(k) for k in
              ("SL_PCT", "BE_TRIGGER_PCT", "MAX_TRADES_PER_DAY",
               "ENABLE_CHOP_FILTER", "MARKET_OPEN", "INDEX_NAME")})
    emit("ready", "Bot armed and watching the market", level="success", equity=20000.0)

    if MODE == "crash":
        print("[FAIL] simulated crash")
        sys.exit(1)

    emit("status", "", level="debug", equity=20000.0, day_pnl=0.0,
         trades=0, max_trades=3, position=None,
         decision={"action": None, "reason": "LOW_VOL", "detail": "GARCH 7.1% <= 9% gate"},
         market={"spot": 24512.3, "garch": 7.1, "adx": 14.2, "vol_regime": "QUIET",
                 "trend": "NO TREND / CHOP", "direction": "SIDEWAYS", "ser": 0.05,
                 "efficiency": "CHOPPY", "vwap": 24500.0, "vwap_side": "ABOVE",
                 "ema9": 24510.0, "ema21": 24505.0, "slope": 1.2, "atr": 12.0,
                 "day_range_pts": 96.0, "day_range_pct": 0.39, "day_move": 18.0})

    emit("decision", "LOW_VOL: GARCH 7.1% <= 9% gate — market too quiet",
         action=None, reason="LOW_VOL", detail="GARCH 7.1% <= 9% gate")

    emit("entry", "BOUGHT 24500CE @ Rs142.50", level="success",
         symbol="NIFTY07AUG2624500CE", opt_type="C", strike=24500, qty=75,
         fill=142.50, sl_price=128.25, risk_rs=1068.75, equity=20000.0,
         why="CE — EMA9 above EMA21, spot above VWAP, GARCH 12.4%")

    emit("ladder", "Breakeven lock — SL to +4% (Rs148.20)", level="success",
         stage="BE", gain_pct=15.4, sl_price=148.20)

    emit("exit", "CLOSED NIFTY07AUG2624500CE — LOCK1_10PCT — net Rs+2,609.08",
         level="success", symbol="NIFTY07AUG2624500CE", reason="LOCK1_10PCT",
         net=2609.08, equity=22609.08,
         ledger={
             "Date": datetime.now().strftime("%Y-%m-%d"), "Mode": "PAPER",
             "EntryTime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
             "ExitTime": datetime.now().strftime("%H:%M:%S"), "HoldMin": 48.9,
             "Symbol": "NIFTY07AUG2624500CE", "Type": "C", "Strike": 24500,
             "Qty": 75, "AvgEntry": 142.50, "ExitFill": 178.20,
             "GrossPnL": 2677.50, "BrokerageTotal": 40.0, "STT": 6.68,
             "ExchTxn": 12.03, "SEBI": 0.02, "Stamp": 0.32, "GST": 9.37,
             "Charges": 68.42, "NetPnL": 2609.08, "Reason": "LOCK1_10PCT",
             "Stage": "LOCK1", "EntryReason": "CE — clean trend stack",
             "SpotAtEntry": 24512.3, "GarchVol": 0.124, "EntryIV": 0.131,
             "ModelPrem": 138.9, "LotCost": 10687.5, "RealMargin": 10687.5,
             "RiskRs": 1068.75, "DayPnL": 2609.08, "EquityAfter": 22609.08,
             "LatencyMs": 214.0,
         })

    emit("minute", "Minute update", equity=22609.08, day_pnl=2609.08, trades=1)

    emit("eod", "End of day — 1 trades, Rs+2,609.08", level="success",
         trades=1, equity=22609.08, day_pnl=2609.08, win_rate=100.0,
         profit_factor=None,
         report={
             "Date": datetime.now().strftime("%Y-%m-%d"), "Mode": "PAPER",
             "Trades": 1, "OpenEquity": 20000.0, "CloseEquity": 22609.08,
             "DayPnL": 2609.08, "Charges": 68.42, "Killed": False,
             "ChopBlocked": False, "ChopScore": 0.62, "GARCH": 12.4,
             "ADX": 23.1, "VolRegime": "TRADEABLE", "Trend": "TRENDING",
             "Direction": "UP", "Efficiency": "DIRECTIONAL",
             "DayRangePts": 186.0, "AvgLatencyMs": 206.0,
             "PeakEquity": 22609.08, "DrawdownPct": 0.0,
         })

    print("[Run] Main loop started.")
    while not _stop.is_set():
        _stop.wait(0.2)

    print("[STOP] Interrupted. Saving state...")
    emit("shutdown", "Bot stopped", level="warn", equity=22609.08)


if __name__ == "__main__":
    main()
