"""Starter algorithm for Meridian Capital.

Upload this as-is and it will run, report, and show a full deck — it just
never takes a trade, because the one decision that matters is left to you.
Search for TODO.

Two things make a file work with the rest of the system:

  1. It runs standalone — no relative imports, and a __main__ block.
  2. It prints @@EVT@@ lines, which is how the dashboard, Trades and Reports
     learn what happened. Anything else it prints shows up in the Live feed.

Without the @@EVT@@ lines the algorithm still runs and you still see its
output, but the position card, the trade history and every export stay empty.
The deck will say "running but not reporting" when that happens.

The full contract — every event kind, every field name — is in the brief you
can copy from Admin, or at backend/app/bot/CONTRACT.md in the repository.
"""
import json
import os
import signal
import sys
import threading
from datetime import datetime

# ---------------------------------------------------------------- protocol
# Inlined so this file stands completely alone.

_seq = 0


def emit(kind, message="", level="info", **payload):
    """One structured event. The supervisor reads these off stdout.

    The flush matters: stdout is a pipe here, not a terminal, so Python
    block-buffers it. Without the flush nothing arrives until the process
    exits — and nothing at all if it is killed.
    """
    global _seq
    _seq += 1
    sys.stdout.write("@@EVT@@" + json.dumps({
        "kind": kind, "level": level, "message": message,
        "ts": datetime.now().isoformat(timespec="milliseconds"),
        "seq": _seq, "payload": payload or None,
    }, default=str) + "\n")
    sys.stdout.flush()


# ---------------------------------------------------------------- config

API_KEY = os.getenv("ANGEL_API_KEY", "")
CLIENT_ID = os.getenv("ANGEL_CLIENT_ID", "")
PASSWORD = os.getenv("ANGEL_PASSWORD", "")
TOTP_SECRET = os.getenv("ANGEL_TOTP_SECRET", "")
PAPER_MODE = (os.getenv("PAPER_MODE", "true").lower() in ("1", "true", "yes"))
DATA_DIR = os.getenv("DATA_DIR", "./data")
SLOT = int(os.getenv("SLOT", "0") or 0)

STARTING_EQUITY = 20000.0
MAX_TRADES_PER_DAY = 3
DAILY_LOSS_LIMIT = 3000.0        # the kill switch
CYCLE_SECONDS = 2.0


class Book:
    """Everything this session needs to remember.

    Kept in one place because almost every field here ends up in either a
    `status` event or the end-of-day report, and scattering them makes it easy
    to report a number that disagrees with the one the algorithm acted on.
    """

    def __init__(self):
        self.equity = STARTING_EQUITY
        self.open_equity = STARTING_EQUITY
        self.peak_equity = STARTING_EQUITY
        self.day_pnl = 0.0
        self.charges = 0.0
        self.trades = 0
        self.wins = 0
        self.gross_win = 0.0
        self.gross_loss = 0.0
        self.position = None          # dict while in a trade, else None
        self.killed = False

    @property
    def drawdown_pct(self):
        if self.peak_equity <= 0:
            return 0.0
        return (self.equity - self.peak_equity) / self.peak_equity * 100

    def mark(self, equity):
        self.equity = equity
        self.peak_equity = max(self.peak_equity, equity)

    @property
    def win_rate(self):
        return (self.wins / self.trades * 100) if self.trades else 0.0

    @property
    def profit_factor(self):
        return round(self.gross_win / self.gross_loss, 2) if self.gross_loss > 0 else None


# ---------------------------------------------------------------- shutdown

_stop = threading.Event()


def _install_signal_handlers():
    def _on_signal(signum, _frame):
        emit("stopping", "Stop requested — flattening", level="warn")
        _stop.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, _on_signal)


# ---------------------------------------------------------------- your logic


def read_market():
    """TODO: return whatever your entry rule needs.

    Everything in `market` shows up on the deck's market card, so it is worth
    returning the real figures rather than placeholders — this is what tells
    you at a glance why the algorithm is or is not acting.
    """
    return {
        "spot": None,           # NIFTY spot
        "garch": 0.0,           # volatility %
        "adx": 0.0,
        "trend": "UNKNOWN",     # TRENDING | CHOPPY | UNKNOWN
        "direction": "FLAT",    # UP | DOWN | FLAT
        "ema9": 0.0, "ema21": 0.0,
        "ser": 0.0, "efficiency": "UNKNOWN",
        "vwap": 0.0, "vwap_side": "—",
        "day_range_pts": 0.0, "day_move": 0.0,
        "vol_regime": "UNKNOWN",
    }


def want_entry(market, book):
    """TODO: your entry rule.

    Return a dict describing the contract to buy, or None to stand aside.
    Whatever reason you return for standing aside is what the deck shows, so
    make it specific — "ADX 14.2, below the 20 threshold" beats "no signal".
    """
    return None, {"action": None, "reason": "WATCHING",
                  "detail": "No entry rule has been written yet."}


def should_exit(position, market, book):
    """TODO: your exit rule. Return an exit reason string, or None to hold."""
    return None


# ---------------------------------------------------------------- reporting


def snapshot(book, market, decision, watching=None):
    """The `status` event — the deck reads this and nothing else."""
    emit("status", "", level="debug",
         paper=PAPER_MODE,
         equity=round(book.equity, 2),
         open_equity=round(book.open_equity, 2),
         day_pnl=round(book.day_pnl, 2),
         total_return_pct=round(
             (book.equity - STARTING_EQUITY) / STARTING_EQUITY * 100, 3),
         peak_equity=round(book.peak_equity, 2),
         drawdown_pct=round(book.drawdown_pct, 3),
         sessions_run=1,
         trades=book.trades, max_trades=MAX_TRADES_PER_DAY,
         kill_used=round(max(0.0, -book.day_pnl), 2),
         kill_limit=DAILY_LOSS_LIMIT,
         killed=book.killed,
         unrealised=round(book.position["pnl"], 2) if book.position else 0.0,
         chop_skip=False, chop_score=None,
         position=book.position,
         watching=watching or {},
         decision=decision,
         market=market,
         latency={})


def file_trade(book, position, exit_price, reason):
    """The `exit` event — this, and only this, puts a row in Trades.

    Key names are capitalised exactly as the platform expects. A misspelt key
    is dropped without complaint, which is how a trade ends up in the table
    with half its columns blank.
    """
    qty = position["qty"]
    gross = (exit_price - position["entry"]) * qty
    charges = estimate_charges(position["entry"], exit_price, qty)
    net = gross - charges

    book.trades += 1
    book.day_pnl += net
    book.charges += charges
    book.mark(book.equity + net)
    if net > 0:
        book.wins += 1
        book.gross_win += net
    else:
        book.gross_loss += abs(net)

    now = datetime.now()
    entered = position["entry_time"]
    hold = (now - datetime.fromisoformat(entered)).total_seconds() / 60

    emit("exit",
         f"SOLD {position['symbol']} @ {exit_price:.2f}  {net:+.0f}",
         level="success" if net >= 0 else "warn",
         ledger={
             "Date": now.date().isoformat(),
             "Mode": "PAPER" if PAPER_MODE else "LIVE",
             "EntryTime": entered,
             "ExitTime": now.isoformat(timespec="seconds"),
             "HoldMin": round(hold, 1),
             "Symbol": position["symbol"],
             "Type": position.get("opt_type", "CE"),
             "Strike": position.get("strike"),
             "Qty": qty,
             "AvgEntry": round(position["entry"], 2),
             "ExitFill": round(exit_price, 2),
             "GrossPnL": round(gross, 2),
             "Charges": round(charges, 2),
             "NetPnL": round(net, 2),
             "Reason": reason,
             "Stage": position.get("stage", "INIT"),
             "EntryReason": position.get("entry_reason", ""),
             "SpotAtEntry": position.get("spot_at_entry"),
             "LotCost": round(position["entry"] * qty, 2),
             "RealMargin": round(position["entry"] * qty, 2),
             "RiskRs": round((position["entry"] - position.get("sl_price", 0)) * qty, 2),
             "DayPnL": round(book.day_pnl, 2),
             "EquityAfter": round(book.equity, 2),
         })
    book.position = None


def estimate_charges(entry, exit_price, qty):
    """TODO: replace with your broker's actual schedule.

    A placeholder that is roughly right beats zero: reporting gross P&L as if
    it were net makes every strategy look better than it is.
    """
    turnover = (entry + exit_price) * qty
    brokerage = min(40.0, turnover * 0.0003)
    stt = exit_price * qty * 0.000625
    txn = turnover * 0.00003503
    gst = (brokerage + txn) * 0.18
    return brokerage + stt + txn + gst


def file_end_of_day(book):
    """The `eod` event — this is what Reports, the calendar and every risk
    metric are built from. A day with no trades still needs one."""
    emit("eod", "Session complete", level="success",
         win_rate=round(book.win_rate, 1),
         profit_factor=book.profit_factor,
         report={
             "Date": datetime.now().date().isoformat(),
             "Mode": "PAPER" if PAPER_MODE else "LIVE",
             "Trades": book.trades,
             "OpenEquity": round(book.open_equity, 2),
             "CloseEquity": round(book.equity, 2),
             "DayPnL": round(book.day_pnl, 2),
             "Charges": round(book.charges, 2),
             "PeakEquity": round(book.peak_equity, 2),
             "DrawdownPct": round(book.drawdown_pct, 3),
             "Killed": book.killed,
             "ChopBlocked": False,
         })


# ---------------------------------------------------------------- main


def main():
    _install_signal_handlers()

    if not PAPER_MODE and not all([API_KEY, CLIENT_ID, PASSWORD, TOTP_SECRET]):
        emit("fatal", "Missing broker credentials in the environment", level="error")
        sys.exit(2)

    print("=" * 60)
    print(f"  MY ALGORITHM  |  SLOT {SLOT}  |  "
          f"MODE: {'PAPER' if PAPER_MODE else 'LIVE MONEY'}")
    print("=" * 60)

    emit("boot", "Algorithm starting", level="success", paper=PAPER_MODE, slot=SLOT)

    # TODO: connect to your broker here. On failure, emit a fatal and exit 2 —
    # the slot then shows the reason instead of restarting into the same wall.

    book = Book()
    emit("ready", "Armed and watching the market", level="success",
         equity=book.equity)

    last_minute = None

    while not _stop.is_set():
        market = read_market()

        if book.position:
            reason = should_exit(book.position, market, book)
            if reason:
                file_trade(book, book.position, book.position["current"], reason)
            decision = {"action": "HOLD", "reason": "IN_POSITION",
                        "detail": "Managing an open position."}
            watching = None
        elif book.killed or book.trades >= MAX_TRADES_PER_DAY:
            decision = {"action": None,
                        "reason": "KILLED" if book.killed else "TRADE_LIMIT",
                        "detail": ("The daily loss limit has been reached."
                                   if book.killed else
                                   f"{book.trades} of {MAX_TRADES_PER_DAY} trades taken.")}
            watching = None
        else:
            candidate, decision = want_entry(market, book)
            watching = candidate
            if candidate:
                # TODO: place the order (or simulate it when PAPER_MODE), then
                # set book.position and emit an "entry" event.
                pass

        # The daily loss limit. Checked every cycle, not only after a trade —
        # an open position can breach it on its own.
        if not book.killed and book.day_pnl <= -DAILY_LOSS_LIMIT:
            book.killed = True
            emit("risk", f"Daily loss limit hit at {book.day_pnl:.0f} — "
                         f"no further entries today", level="error",
                 day_pnl=book.day_pnl, limit=DAILY_LOSS_LIMIT)

        snapshot(book, market, decision, watching)

        # One equity mark a minute. These are the equity curve.
        minute = datetime.now().replace(second=0, microsecond=0)
        if minute != last_minute:
            last_minute = minute
            emit("minute", "", level="debug",
                 equity=round(book.equity, 2), day_pnl=round(book.day_pnl, 2))

        # wait(), not sleep() — this wakes the moment a stop is requested.
        _stop.wait(CYCLE_SECONDS)

    # On the way out: flatten first, then file the day.
    if book.position:
        emit("stopping", "Flattening the open position", level="warn")
        file_trade(book, book.position, book.position["current"], "SESSION_END")

    file_end_of_day(book)
    emit("shutdown", "Algorithm stopped", level="warn", equity=round(book.equity, 2))


if __name__ == "__main__":
    main()
