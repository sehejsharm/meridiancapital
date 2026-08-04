"""SQLite persistence.

Everything the algorithm emits during a session lands here: the raw terminal
feed, structured decisions, trades, equity marks and the end-of-day report.
That makes any past day / week / month / quarter / year reconstructable long
after the process that produced it is gone.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional

_local = threading.local()
_DB_PATH: Optional[Path] = None
_write_lock = threading.Lock()

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA foreign_keys=ON;

-- Raw + structured feed of everything the bot printed or emitted.
CREATE TABLE IF NOT EXISTS events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            TEXT    NOT NULL,           -- ISO8601 local (IST)
    session_date  TEXT    NOT NULL,           -- YYYY-MM-DD
    seq           INTEGER NOT NULL DEFAULT 0, -- monotonic within a run
    run_id        INTEGER,
    kind          TEXT    NOT NULL,           -- log | decision | entry | exit | ladder | ...
    level         TEXT    NOT NULL DEFAULT 'info',
    message       TEXT    NOT NULL DEFAULT '',
    payload       TEXT                        -- JSON blob
);
CREATE INDEX IF NOT EXISTS idx_events_date ON events(session_date);
CREATE INDEX IF NOT EXISTS idx_events_kind ON events(kind);
CREATE INDEX IF NOT EXISTS idx_events_ts   ON events(ts);

-- One row per completed round trip.
CREATE TABLE IF NOT EXISTS trades (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    session_date   TEXT    NOT NULL,
    mode           TEXT    NOT NULL DEFAULT 'PAPER',
    entry_time     TEXT,
    exit_time      TEXT,
    hold_min       REAL,
    symbol         TEXT,
    opt_type       TEXT,
    strike         INTEGER,
    qty            INTEGER,
    avg_entry      REAL,
    exit_fill      REAL,
    model_prem     REAL,
    lot_cost       REAL,
    real_margin    REAL,
    risk_rs        REAL,
    gross_pnl      REAL,
    brokerage      REAL,
    stt            REAL,
    exch_txn       REAL,
    sebi           REAL,
    stamp          REAL,
    gst            REAL,
    charges        REAL,
    net_pnl        REAL,
    reason         TEXT,
    stage          TEXT,
    entry_reason   TEXT,
    spot_at_entry  REAL,
    garch_vol      REAL,
    entry_iv       REAL,
    day_pnl        REAL,
    equity_after   REAL,
    latency_ms     REAL,
    UNIQUE(session_date, entry_time, symbol)
);
CREATE INDEX IF NOT EXISTS idx_trades_date ON trades(session_date);

-- One row per trading session (the EOD report).
CREATE TABLE IF NOT EXISTS sessions (
    session_date   TEXT PRIMARY KEY,
    mode           TEXT,
    trades         INTEGER DEFAULT 0,
    open_equity    REAL,
    close_equity   REAL,
    day_pnl        REAL,
    charges        REAL,
    killed         INTEGER DEFAULT 0,
    chop_blocked   INTEGER DEFAULT 0,
    chop_score     REAL,
    garch          REAL,
    adx            REAL,
    vol_regime     TEXT,
    trend          TEXT,
    direction      TEXT,
    efficiency     TEXT,
    day_range_pts  REAL,
    avg_latency_ms REAL,
    peak_equity    REAL,
    drawdown_pct   REAL,
    win_rate       REAL,
    profit_factor  REAL,
    created_at     TEXT
);

-- Intraday equity / P&L marks so the app can draw a curve.
CREATE TABLE IF NOT EXISTS equity_marks (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           TEXT NOT NULL,
    session_date TEXT NOT NULL,
    equity       REAL NOT NULL,
    day_pnl      REAL NOT NULL DEFAULT 0,
    open_position INTEGER NOT NULL DEFAULT 0,
    unrealised   REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_marks_date ON equity_marks(session_date);

-- Supervisor process lifecycle.
CREATE TABLE IF NOT EXISTS runs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    session_date TEXT NOT NULL,
    started_at   TEXT NOT NULL,
    stopped_at   TEXT,
    pid          INTEGER,
    trigger      TEXT,          -- schedule | manual | restart
    stop_reason  TEXT,
    exit_code    INTEGER
);
CREATE INDEX IF NOT EXISTS idx_runs_date ON runs(session_date);

-- Devices registered for push.
CREATE TABLE IF NOT EXISTS push_tokens (
    token      TEXT PRIMARY KEY,
    platform   TEXT,
    created_at TEXT
);

-- Small persistent key/value bag (latest snapshot, schedule overrides...).
CREATE TABLE IF NOT EXISTS kv (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


def init(db_path: Path) -> None:
    global _DB_PATH
    _DB_PATH = Path(db_path)
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        conn.executescript(SCHEMA)


def _conn() -> sqlite3.Connection:
    if _DB_PATH is None:
        raise RuntimeError("db.init() must be called before use")
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(str(_DB_PATH), timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        _local.conn = conn
    return conn


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    conn = _conn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def query(sql: str, params: Iterable[Any] = ()) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(sql, tuple(params)).fetchall()
    return [dict(r) for r in rows]


def query_one(sql: str, params: Iterable[Any] = ()) -> Optional[dict]:
    rows = query(sql, params)
    return rows[0] if rows else None


def execute(sql: str, params: Iterable[Any] = ()) -> int:
    with _write_lock, connect() as conn:
        cur = conn.execute(sql, tuple(params))
        return cur.lastrowid or 0


# ---------------------------------------------------------------- events


def insert_event(
    ts: str,
    session_date: str,
    kind: str,
    message: str,
    level: str = "info",
    payload: Optional[dict] = None,
    seq: int = 0,
    run_id: Optional[int] = None,
) -> int:
    return execute(
        """INSERT INTO events (ts, session_date, seq, run_id, kind, level, message, payload)
           VALUES (?,?,?,?,?,?,?,?)""",
        (ts, session_date, seq, run_id, kind, level, message,
         json.dumps(payload, default=str) if payload else None),
    )


def recent_events(
    limit: int = 300,
    since_id: int = 0,
    session_date: Optional[str] = None,
    kinds: Optional[list[str]] = None,
) -> list[dict]:
    sql = "SELECT * FROM events WHERE id > ?"
    params: list[Any] = [since_id]
    if session_date:
        sql += " AND session_date = ?"
        params.append(session_date)
    if kinds:
        sql += f" AND kind IN ({','.join('?' * len(kinds))})"
        params.extend(kinds)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    rows = query(sql, params)
    for r in rows:
        r["payload"] = json.loads(r["payload"]) if r["payload"] else None
    return list(reversed(rows))


# ---------------------------------------------------------------- trades

TRADE_COLUMNS = [
    "session_date", "mode", "entry_time", "exit_time", "hold_min", "symbol",
    "opt_type", "strike", "qty", "avg_entry", "exit_fill", "model_prem",
    "lot_cost", "real_margin", "risk_rs", "gross_pnl", "brokerage", "stt",
    "exch_txn", "sebi", "stamp", "gst", "charges", "net_pnl", "reason",
    "stage", "entry_reason", "spot_at_entry", "garch_vol", "entry_iv",
    "day_pnl", "equity_after", "latency_ms",
]


def upsert_trade(rec: dict) -> None:
    cols = [c for c in TRADE_COLUMNS if c in rec]
    placeholders = ",".join("?" * len(cols))
    sql = (
        f"INSERT OR REPLACE INTO trades ({','.join(cols)}) VALUES ({placeholders})"
    )
    execute(sql, [rec.get(c) for c in cols])


def trades_between(start: str, end: str) -> list[dict]:
    return query(
        "SELECT * FROM trades WHERE session_date >= ? AND session_date <= ?"
        " ORDER BY session_date, entry_time",
        (start, end),
    )


# ---------------------------------------------------------------- sessions

SESSION_COLUMNS = [
    "session_date", "mode", "trades", "open_equity", "close_equity", "day_pnl",
    "charges", "killed", "chop_blocked", "chop_score", "garch", "adx",
    "vol_regime", "trend", "direction", "efficiency", "day_range_pts",
    "avg_latency_ms", "peak_equity", "drawdown_pct", "win_rate",
    "profit_factor", "created_at",
]


def upsert_session(rec: dict) -> None:
    rec = dict(rec)
    rec.setdefault("created_at", datetime.now().isoformat(timespec="seconds"))
    cols = [c for c in SESSION_COLUMNS if c in rec]
    placeholders = ",".join("?" * len(cols))
    execute(
        f"INSERT OR REPLACE INTO sessions ({','.join(cols)}) VALUES ({placeholders})",
        [rec.get(c) for c in cols],
    )


def sessions_between(start: str, end: str) -> list[dict]:
    return query(
        "SELECT * FROM sessions WHERE session_date >= ? AND session_date <= ?"
        " ORDER BY session_date",
        (start, end),
    )


# ---------------------------------------------------------------- equity


def insert_equity_mark(
    ts: str, session_date: str, equity: float, day_pnl: float,
    open_position: bool = False, unrealised: float = 0.0,
) -> None:
    execute(
        """INSERT INTO equity_marks (ts, session_date, equity, day_pnl, open_position, unrealised)
           VALUES (?,?,?,?,?,?)""",
        (ts, session_date, equity, day_pnl, 1 if open_position else 0, unrealised),
    )


def equity_marks(session_date: str) -> list[dict]:
    return query(
        "SELECT * FROM equity_marks WHERE session_date = ? ORDER BY id", (session_date,)
    )


def equity_curve(start: str, end: str) -> list[dict]:
    """Daily closing equity across a range, for the long-horizon chart."""
    return query(
        """SELECT session_date, close_equity AS equity, day_pnl
             FROM sessions
            WHERE session_date >= ? AND session_date <= ?
            ORDER BY session_date""",
        (start, end),
    )


# ---------------------------------------------------------------- runs


def start_run(session_date: str, pid: int, trigger: str) -> int:
    return execute(
        "INSERT INTO runs (session_date, started_at, pid, trigger) VALUES (?,?,?,?)",
        (session_date, datetime.now().isoformat(timespec="seconds"), pid, trigger),
    )


def end_run(run_id: int, stop_reason: str, exit_code: Optional[int]) -> None:
    execute(
        "UPDATE runs SET stopped_at = ?, stop_reason = ?, exit_code = ? WHERE id = ?",
        (datetime.now().isoformat(timespec="seconds"), stop_reason, exit_code, run_id),
    )


def recent_runs(limit: int = 30) -> list[dict]:
    return query("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,))


# ---------------------------------------------------------------- kv


def kv_set(key: str, value: Any) -> None:
    execute(
        "INSERT OR REPLACE INTO kv (key, value) VALUES (?,?)",
        (key, json.dumps(value, default=str)),
    )


def kv_get(key: str, default: Any = None) -> Any:
    row = query_one("SELECT value FROM kv WHERE key = ?", (key,))
    if not row:
        return default
    try:
        return json.loads(row["value"])
    except (json.JSONDecodeError, TypeError):
        return default


# ---------------------------------------------------------------- push


def add_push_token(token: str, platform: str = "") -> None:
    execute(
        "INSERT OR REPLACE INTO push_tokens (token, platform, created_at) VALUES (?,?,?)",
        (token, platform, datetime.now().isoformat(timespec="seconds")),
    )


def list_push_tokens() -> list[str]:
    return [r["token"] for r in query("SELECT token FROM push_tokens")]


def remove_push_token(token: str) -> None:
    execute("DELETE FROM push_tokens WHERE token = ?", (token,))


# ---------------------------------------------------------------- stats


def aggregate(start: str, end: str) -> dict:
    """Headline numbers for any date range — powers the Reports screen."""
    trades = trades_between(start, end)
    sess = sessions_between(start, end)

    n = len(trades)
    wins = [t for t in trades if (t.get("net_pnl") or 0) > 0]
    losses = [t for t in trades if (t.get("net_pnl") or 0) <= 0]
    gross_win = sum(t["net_pnl"] for t in wins) if wins else 0.0
    gross_loss = abs(sum(t["net_pnl"] for t in losses)) if losses else 0.0
    net = sum((t.get("net_pnl") or 0) for t in trades)
    charges = sum((t.get("charges") or 0) for t in trades)

    open_equity = sess[0]["open_equity"] if sess else None
    close_equity = sess[-1]["close_equity"] if sess else None
    peak = max((s.get("peak_equity") or 0) for s in sess) if sess else None

    # Max drawdown across the daily closes in range.
    curve = [s["close_equity"] for s in sess if s.get("close_equity") is not None]
    max_dd = 0.0
    running_peak = curve[0] if curve else 0.0
    for v in curve:
        running_peak = max(running_peak, v)
        if running_peak > 0:
            max_dd = min(max_dd, (v - running_peak) / running_peak)

    return {
        "start": start,
        "end": end,
        "sessions": len(sess),
        "trading_days": len({t["session_date"] for t in trades}),
        "trades": n,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": (len(wins) / n * 100) if n else 0.0,
        "gross_profit": round(gross_win, 2),
        "gross_loss": round(gross_loss, 2),
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else None,
        "net_pnl": round(net, 2),
        "charges": round(charges, 2),
        "avg_trade": round(net / n, 2) if n else 0.0,
        "best_trade": round(max((t["net_pnl"] for t in trades), default=0.0), 2),
        "worst_trade": round(min((t["net_pnl"] for t in trades), default=0.0), 2),
        "avg_hold_min": round(
            sum((t.get("hold_min") or 0) for t in trades) / n, 1) if n else 0.0,
        "open_equity": open_equity,
        "close_equity": close_equity,
        "peak_equity": peak,
        "max_drawdown_pct": round(max_dd * 100, 2),
        "return_pct": round((close_equity - open_equity) / open_equity * 100, 2)
        if open_equity and close_equity else 0.0,
        "days_blocked_chop": sum(1 for s in sess if s.get("chop_blocked")),
        "days_killed": sum(1 for s in sess if s.get("killed")),
    }


def today_str() -> str:
    return date.today().strftime("%Y-%m-%d")
