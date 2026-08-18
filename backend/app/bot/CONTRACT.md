# Meridian Capital — algorithm brief

Paste this whole document into a Claude chat, then say what you want the
algorithm to do. Claude has everything it needs here to write a file that runs
in a Meridian slot **and** shows up correctly on the dashboard.

---

## What you are writing

A single self-contained Python file. Meridian runs it as its own process, one
per slot, and reads its standard output. Up to five of them run side by side
against the same instrument, each with its own equity, its own trades and its
own line in every report.

Two rules make a file work here:

1. **It runs standalone.** No relative imports, no package context. One
   `if __name__ == "__main__":` block. It is started as `python your_file.py`.
2. **It prints `@@EVT@@` lines.** That is the entire integration. Everything
   the dashboard, the Trades screen and the reports know comes from those
   lines.

Anything else the file prints — ordinary `print()` — is picked up as the
terminal feed and shown in Live. That is useful, but on its own it is *all*
you get: no position card, no trade history, no P&L, no report rows. An
algorithm that only prints is the single most common thing that goes wrong.

---

## The event line

```python
import json, sys
from datetime import datetime

_seq = 0

def emit(kind, message="", level="info", **payload):
    global _seq
    _seq += 1
    sys.stdout.write("@@EVT@@" + json.dumps({
        "kind": kind, "level": level, "message": message,
        "ts": datetime.now().isoformat(timespec="milliseconds"),
        "seq": _seq, "payload": payload or None,
    }, default=str) + "\n")
    sys.stdout.flush()
```

`flush()` is not optional. Python block-buffers stdout when it is a pipe, and
Meridian reads a pipe — without the flush your events arrive in a lump when
the process exits, or not at all if it is killed.

`level` is one of `debug`, `info`, `success`, `warn`, `error`. It only colours
the Live feed and decides what counts as a fault in diagnostics.

---

## The six event kinds that matter

Everything else you emit is stored and shown in the feed, which is worth doing
for anything a human would want to read back later. But these six are the ones
wired into screens.

### 1. `status` — the deck

Sent as often as you like; never stored. This is what the dashboard's live
cards read. Emit one every cycle.

```python
emit("status", "", level="debug",
     paper=True,                    # bool  — drives the red LIVE MONEY banner
     equity=20000.0,                # float — the big number
     open_equity=20000.0,           # float — equity at the open, for today's %
     day_pnl=0.0,                   # float — realised + unrealised, today
     total_return_pct=0.0,          # float — since inception
     peak_equity=20000.0,           # float
     drawdown_pct=0.0,              # float — negative or zero
     sessions_run=1,                # int
     trades=0, max_trades=3,        # int   — trade counter on the risk card
     kill_used=0.0, kill_limit=3000.0,   # float — daily loss budget
     killed=False,                  # bool  — kill switch tripped today
     unrealised=0.0,                # float
     chop_skip=False, chop_score=0.0,
     position=None,                 # dict or None — see below
     watching={"symbol": "NIFTY24500CE", "premium": 142.5},   # when flat
     decision={"action": None, "reason": "WATCHING",
               "detail": "ADX 14.2 — below the 20 threshold"},
     market={"spot": 24512.4, "garch": 11.2, "adx": 22.4, "trend": "TRENDING",
             "direction": "UP", "ema9": 24510.0, "ema21": 24488.0,
             "ser": 0.18, "efficiency": "EFFICIENT", "vwap": 24501.2,
             "vwap_side": "above", "day_range_pts": 96.0, "day_move": 42.0,
             "vol_regime": "NORMAL"},
     latency={"ltpData": {"avg": 88.0, "p95": 140.0, "n": 412}})
```

`decision` is what the deck shows when you are flat. Fill in `reason` with a
short code and `detail` with the actual numbers — "why is it not trading" is
the question this platform gets asked most, and a good `detail` answers it
without anyone reading a log.

When you are in a position, `position` is a dict:

```python
position={
    "symbol": "NIFTY24500CE",
    "qty": 75,
    "entry": 142.5,            # what you paid
    "current": 151.3,          # what it is worth right now  ← the headline
    "entry_time": "2026-08-18T10:14:02",
    "stage": "BE",             # INIT | BE | LOCK1 | LOCK2 | FREE
    "pnl": 660.0, "pnl_pct": 6.2,
    "sl_price": 128.0,
    "high_prem": 154.0,        # best premium seen since entry
    "risk_left": 1087.5,
    "lot_cost": 10687.5,
    "real_margin": 10687.5,
    "entry_reason": "ADX 24.1, EMA9>EMA21, SER 0.21",
    "next_trigger": {"label": "Lock 1", "gain_pct": 6.2,
                     "target_pct": 20, "progress": 0.31},
}
```

If your strategy has no trailing ladder, use `stage: "INIT"` and a
`next_trigger` with just `label` and `gain_pct`. The rungs render as unreached
and nothing breaks.

### 2. `minute` — the equity curve

One per minute while running. Each one becomes a point on the equity curve and
feeds the drawdown calculation.

```python
emit("minute", "", level="debug", equity=20140.0, day_pnl=140.0)
```

### 3. `exit` with a `ledger` — a row in Trades

This is what puts a trade into the Trades screen and every report. Emit it when
a position closes, with the complete record:

```python
emit("exit", "SOLD NIFTY24500CE @ 178.20  +2678", level="success", ledger={
    "Date": "2026-08-18",          # YYYY-MM-DD
    "Mode": "PAPER",               # PAPER | LIVE
    "EntryTime": "2026-08-18T10:14:02",
    "ExitTime":  "2026-08-18T10:41:55",
    "HoldMin": 27.9,
    "Symbol": "NIFTY24500CE",
    "Type": "CE",                  # CE | PE
    "Strike": 24500,
    "Qty": 75,
    "AvgEntry": 142.50,            # price paid
    "ExitFill": 178.20,            # price received
    "GrossPnL": 2677.50,
    "BrokerageTotal": 40.0, "STT": 6.7, "ExchTxn": 1.3,
    "SEBI": 0.02, "Stamp": 0.3, "GST": 7.4,
    "Charges": 55.72,
    "NetPnL": 2621.78,             # gross minus charges — the reported number
    "Reason": "LOCK1_HIT",         # why it closed
    "Stage": "LOCK1",
    "EntryReason": "ADX 24.1, EMA9>EMA21, SER 0.21",
    "SpotAtEntry": 24512.4,
    "DayPnL": 2621.78,             # running total for the day, after this
    "EquityAfter": 22621.78,
    # optional, stored if present:
    "ModelPrem": 139.8, "LotCost": 10687.5, "RealMargin": 10687.5,
    "RiskRs": 1087.5, "GarchVol": 11.2, "EntryIV": 14.6, "LatencyMs": 88.0,
})
```

The keys are capitalised exactly as shown. A missing key stores as null; a
misspelt key is silently dropped, which is why a trade sometimes appears with
blank columns.

Emit a matching `entry` event when you open — it is not required for the
tables, but the Live feed and the session replay read much better with it.

### 4. `eod` with a `report` — a row in Daily Sessions

Emit once, as the session ends, before you exit. This drives the daily P&L
calendar, the equity curve's daily points and every risk metric.

```python
emit("eod", "Session complete", level="success",
     win_rate=66.7, profit_factor=2.41,
     report={
        "Date": "2026-08-18", "Mode": "PAPER", "Trades": 3,
        "OpenEquity": 20000.0, "CloseEquity": 22621.78,
        "DayPnL": 2621.78, "Charges": 167.16,
        "PeakEquity": 22800.0, "DrawdownPct": -0.78,
        "Killed": False, "ChopBlocked": False, "ChopScore": 0.42,
        "GARCH": 11.2, "ADX": 22.4, "VolRegime": "NORMAL",
        "Trend": "TRENDING", "Direction": "UP", "Efficiency": "EFFICIENT",
        "DayRangePts": 96.0, "AvgLatencyMs": 88.0,
     })
```

Without this, Reports has no sessions, so return %, max drawdown, Sharpe,
Sortino and Calmar are all blank however many trades you took. Sharpe and its
relatives are computed from *daily* returns — `OpenEquity` and `DayPnL` are
what they are built from, so a day you sat out still needs an `eod` with
`Trades: 0` and `DayPnL: 0`.

### 5. `chart` — the chart overlay

Optional. Ephemeral, like `status`.

```python
emit("chart", "", level="debug",
     candles=[[epoch_ms, open, high, low, close], ...],
     overlay={"ema9": [...], "ema21": [...], "vwap": [...]})
```

### 6. `fatal` — give up loudly

```python
emit("fatal", "Broker rejected the login: invalid TOTP", level="error")
sys.exit(2)
```

Exit code 2 tells the supervisor this was a startup failure, and the slot goes
to a fault state with your message on the deck instead of restarting into the
same wall.

---

## Free-form events worth emitting

These are stored and appear in Live, in the session replay and in the report
event log. None of them are required; all of them make a bad day explicable.

| kind | when |
|---|---|
| `boot` | process started |
| `ready` | connected to the broker, armed |
| `entry` | a position was opened |
| `ladder` | the trailing stop moved a rung |
| `decision` | you evaluated and chose not to act, with the reason |
| `risk` | a limit was approached or hit |
| `stopping` | SIGTERM received, flattening |
| `shutdown` | clean exit |

---

## Environment

Credentials arrive as environment variables. Never hardcode them.

| variable | meaning |
|---|---|
| `ANGEL_API_KEY`, `ANGEL_CLIENT_ID`, `ANGEL_PASSWORD`, `ANGEL_TOTP_SECRET` | broker credentials |
| `PAPER_MODE` | `"true"` / `"false"` — **must** be respected |
| `DATA_DIR` | writable directory for anything you persist |
| `SLOT` | which slot this process is (0–4) |

`PAPER_MODE` is the one that matters. When it is true, simulate fills at the
live price and place no orders. The platform will not stop you ignoring it —
respecting it is the algorithm's job.

---

## Shutdown

The supervisor sends `SIGTERM` at the scheduled stop and when someone presses
Stop. You get one chance to flatten:

```python
_stop = threading.Event()

def _on_signal(signum, frame):
    emit("stopping", "Stop requested", level="warn")
    _stop.set()

for s in (signal.SIGTERM, signal.SIGINT):
    signal.signal(s, _on_signal)

while not _stop.is_set():
    ...
    _stop.wait(2.0)          # not time.sleep — this wakes on the signal
```

Use `_stop.wait(n)` rather than `time.sleep(n)`, or a stop request waits out
your whole sleep before anything happens.

---

## Two things that will bite

**Carriage-return status lines.** `print("\rWorking...", end="")` is fine in a
terminal and stalls the reader here, because Meridian reads line by line and a
line with no newline never arrives. Check `sys.stdout.isatty()` first, or emit
a `status` event instead.

**Printing on every tick.** A line per second is 23,000 lines a session; it
pushes everything meaningful out of the feed. Meridian collapses obvious
repeats, but the right fix is to print when something changes and use `status`
for the rest.

---

## What "good" looks like

When the algorithm is written correctly, within a minute of starting the slot
you should see: the deck showing equity and a decision reason, the Live feed
carrying your boot and ready lines, and — once it trades — a card with the
contract's current price, then a row in Trades. If the deck says *"running but
not reporting"*, the file is printing and not emitting; go back to `emit`.
