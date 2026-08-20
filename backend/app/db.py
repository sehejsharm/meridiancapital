"""SQLite persistence.

Everything the algorithm emits during a session lands here: the raw terminal
feed, structured decisions, trades, equity marks and the end-of-day report.
That makes any past day / week / month / quarter / year reconstructable long
after the process that produced it is gone.
"""
from __future__ import annotations

import json
import math
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
    slot           INTEGER NOT NULL DEFAULT 0,
    -- slot belongs in the key: five algorithms share one instrument, so two of
    -- them entering the same contract in the same second is ordinary, not a
    -- duplicate. Without it the second fill would replace the first.
    UNIQUE(session_date, entry_time, symbol, slot)
);
CREATE INDEX IF NOT EXISTS idx_trades_date ON trades(session_date);

-- One row per trading session (the EOD report).
CREATE TABLE IF NOT EXISTS sessions (
    session_date   TEXT NOT NULL,
    slot           INTEGER NOT NULL DEFAULT 0,
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
    created_at     TEXT,
    -- One EOD row per slot per day, not one per day.
    PRIMARY KEY (session_date, slot)
);

-- Intraday equity / P&L marks so the app can draw a curve.
CREATE TABLE IF NOT EXISTS equity_marks (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           TEXT NOT NULL,
    session_date TEXT NOT NULL,
    equity       REAL NOT NULL,
    day_pnl      REAL NOT NULL DEFAULT 0,
    open_position INTEGER NOT NULL DEFAULT 0,
    unrealised   REAL NOT NULL DEFAULT 0,
    slot         INTEGER NOT NULL DEFAULT 0
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
    exit_code    INTEGER,
    slot         INTEGER NOT NULL DEFAULT 0
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

-- Sessions that were signed out before their own expiry. Tokens are signed
-- rather than stored, so this is the only way one can be retired early; rows
-- are swept once the token they name would have expired anyway.
CREATE TABLE IF NOT EXISTS revoked_sessions (
    jti        TEXT PRIMARY KEY,
    expires_at INTEGER NOT NULL,
    revoked_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_revoked_exp ON revoked_sessions(expires_at);
"""


# Every row the bot writes is tagged with the slot that produced it, so five
# algorithms can share one database and still be reported on separately. Slot 0
# is the original single-bot lane: the column defaults to 0, which silently
# adopts every row written before slots existed rather than orphaning a live
# trading history.
SLOT_TABLES = ("events", "equity_marks", "runs")

# trades and sessions cannot take a plain ADD COLUMN: their uniqueness keys have
# to widen to include the slot, and SQLite will not alter a constraint in place.
# Those two are rebuilt instead — renamed aside, recreated from the schema above,
# and copied back with slot defaulting to 0.
REBUILD_TABLES = ("trades", "sessions")


def _columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]


def _migrate(conn: sqlite3.Connection) -> None:
    """Bring an older database up to the current schema.

    Runs on every boot and must stay safe against a populated, live database —
    this is somebody's trading history, so every path here either preserves the
    rows or does nothing at all.
    """
    for table in SLOT_TABLES:
        if "slot" not in _columns(conn, table):
            conn.execute(f"ALTER TABLE {table} ADD COLUMN slot INTEGER NOT NULL DEFAULT 0")

    for table in REBUILD_TABLES:
        cols = _columns(conn, table)
        if not cols or "slot" in cols:
            continue
        carried = ",".join(cols)
        conn.execute(f"ALTER TABLE {table} RENAME TO {table}_pre_slot")
        conn.executescript(SCHEMA)          # recreates just the renamed table
        conn.execute(
            f"INSERT INTO {table} ({carried}) SELECT {carried} FROM {table}_pre_slot"
        )
        conn.execute(f"DROP TABLE {table}_pre_slot")

    conn.execute("CREATE INDEX IF NOT EXISTS idx_events_slot ON events(slot)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_trades_slot ON trades(slot)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_slot ON sessions(slot)")


def init(db_path: Path) -> None:
    global _DB_PATH
    # Connections are cached per thread and never consulted `_DB_PATH` again
    # after the first open, so pointing init() at a second file used to change
    # nothing — every later query still went to the first one. The server calls
    # init() once and never noticed; tests that switch databases did.
    stale = getattr(_local, "conn", None)
    if stale is not None and _DB_PATH is not None and Path(db_path) != _DB_PATH:
        try:
            stale.close()
        except Exception:
            pass
        _local.conn = None
    _DB_PATH = Path(db_path)
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        conn.executescript(SCHEMA)
        _migrate(conn)
        # A rebuild drops the renamed table's indexes with it; every statement in
        # SCHEMA is IF NOT EXISTS, so replaying it restores them and does nothing
        # on the common path where no rebuild happened.
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
    slot: int = 0,
) -> int:
    return execute(
        """INSERT INTO events (ts, session_date, seq, run_id, kind, level, message,
                               payload, slot)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (ts, session_date, seq, run_id, kind, level, message,
         json.dumps(payload, default=str) if payload else None, slot),
    )


def recent_events(
    limit: int = 300,
    since_id: int = 0,
    session_date: Optional[str] = None,
    kinds: Optional[list[str]] = None,
    slot: Optional[int] = None,
) -> list[dict]:
    """`slot=None` reads every slot — the combined feed the deck shows."""
    sql = "SELECT * FROM events WHERE id > ?"
    params: list[Any] = [since_id]
    if session_date:
        sql += " AND session_date = ?"
        params.append(session_date)
    if slot is not None:
        sql += " AND slot = ?"
        params.append(slot)
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
    "day_pnl", "equity_after", "latency_ms", "slot",
]


def upsert_trade(rec: dict) -> None:
    cols = [c for c in TRADE_COLUMNS if c in rec]
    placeholders = ",".join("?" * len(cols))
    sql = (
        f"INSERT OR REPLACE INTO trades ({','.join(cols)}) VALUES ({placeholders})"
    )
    execute(sql, [rec.get(c) for c in cols])


def trades_between(start: str, end: str, slot: Optional[int] = None) -> list[dict]:
    sql = "SELECT * FROM trades WHERE session_date >= ? AND session_date <= ?"
    params: list[Any] = [start, end]
    if slot is not None:
        sql += " AND slot = ?"
        params.append(slot)
    return query(sql + " ORDER BY session_date, entry_time", params)


# ---------------------------------------------------------------- sessions

SESSION_COLUMNS = [
    "session_date", "mode", "trades", "open_equity", "close_equity", "day_pnl",
    "charges", "killed", "chop_blocked", "chop_score", "garch", "adx",
    "vol_regime", "trend", "direction", "efficiency", "day_range_pts",
    "avg_latency_ms", "peak_equity", "drawdown_pct", "win_rate",
    "profit_factor", "created_at", "slot",
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


def sessions_between(start: str, end: str, slot: Optional[int] = None) -> list[dict]:
    sql = "SELECT * FROM sessions WHERE session_date >= ? AND session_date <= ?"
    params: list[Any] = [start, end]
    if slot is not None:
        sql += " AND slot = ?"
        params.append(slot)
    return query(sql + " ORDER BY session_date", params)


# ---------------------------------------------------------------- equity


def insert_equity_mark(
    ts: str, session_date: str, equity: float, day_pnl: float,
    open_position: bool = False, unrealised: float = 0.0, slot: int = 0,
) -> None:
    execute(
        """INSERT INTO equity_marks (ts, session_date, equity, day_pnl, open_position,
                                     unrealised, slot)
           VALUES (?,?,?,?,?,?,?)""",
        (ts, session_date, equity, day_pnl, 1 if open_position else 0, unrealised, slot),
    )


def equity_marks(session_date: str, slot: Optional[int] = None) -> list[dict]:
    sql = "SELECT * FROM equity_marks WHERE session_date = ?"
    params: list[Any] = [session_date]
    if slot is not None:
        sql += " AND slot = ?"
        params.append(slot)
    return query(sql + " ORDER BY id", params)


def equity_marks_between(start: str, end: str,
                         slot: Optional[int] = None) -> list[dict]:
    """Every minute mark across a range — the minute-by-minute report."""
    sql = ("SELECT ts, session_date, slot, equity, day_pnl, open_position, unrealised "
           "FROM equity_marks WHERE session_date >= ? AND session_date <= ?")
    params: list[Any] = [start, end]
    if slot is not None:
        sql += " AND slot = ?"
        params.append(slot)
    return query(sql + " ORDER BY session_date, ts, id", params)


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


def start_run(session_date: str, pid: int, trigger: str, slot: int = 0) -> int:
    return execute(
        "INSERT INTO runs (session_date, started_at, pid, trigger, slot) VALUES (?,?,?,?,?)",
        (session_date, datetime.now().isoformat(timespec="seconds"), pid, trigger, slot),
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


def _risk_metrics(sess: list[dict], trades: list[dict], max_dd: float,
                  start: str, end: str) -> dict:
    """Risk-adjusted return, computed from daily returns.

    Daily rather than per-trade: a ratio built from trade P&Ls flatters a
    strategy that takes few, large positions, because the days it sat out never
    enter the denominator. Sitting out is a decision with a cost, so flat days
    count.

    The risk-free rate is expressed annually and de-annualised here. NSE trades
    roughly 250 sessions a year, which is the periods-per-year figure used to
    scale both the mean and the deviation.
    """
    PERIODS = 250
    RF_ANNUAL = 0.065                     # ~6.5%, a reasonable Indian T-bill
    rf_daily = RF_ANNUAL / PERIODS

    rets: list[float] = []
    for s in sess:
        opened = s.get("open_equity")
        pnl = s.get("day_pnl")
        if opened and pnl is not None and opened > 0:
            rets.append(pnl / opened)

    out: dict[str, Any] = {
        "sharpe": None, "sortino": None, "calmar": None,
        "expectancy": None, "avg_win": None, "avg_loss": None,
        "win_loss_ratio": None, "daily_vol_pct": None,
        "best_day": None, "worst_day": None, "trading_period_days": None,
    }

    wins = [t["net_pnl"] for t in trades if (t.get("net_pnl") or 0) > 0]
    losses = [t["net_pnl"] for t in trades if (t.get("net_pnl") or 0) <= 0]
    if trades:
        aw = sum(wins) / len(wins) if wins else 0.0
        al = abs(sum(losses) / len(losses)) if losses else 0.0
        p = len(wins) / len(trades)
        out["avg_win"] = round(aw, 2)
        out["avg_loss"] = round(al, 2)
        out["win_loss_ratio"] = round(aw / al, 2) if al > 0 else None
        # What one more trade is worth on average, in rupees.
        out["expectancy"] = round(p * aw - (1 - p) * al, 2)

    if sess:
        days = [s.get("day_pnl") or 0.0 for s in sess]
        out["best_day"] = round(max(days), 2)
        out["worst_day"] = round(min(days), 2)

    # Two returns is the minimum for a standard deviation that means anything.
    if len(rets) >= 2:
        mean = sum(rets) / len(rets)
        var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
        sd = math.sqrt(var)
        out["daily_vol_pct"] = round(sd * 100, 3)
        if sd > 0:
            out["sharpe"] = round((mean - rf_daily) / sd * math.sqrt(PERIODS), 2)

        # Sortino punishes only downside deviation; upside volatility is not risk.
        downside = [min(0.0, r - rf_daily) for r in rets]
        dvar = sum(d ** 2 for d in downside) / (len(rets) - 1)
        dsd = math.sqrt(dvar)
        if dsd > 0:
            out["sortino"] = round((mean - rf_daily) / dsd * math.sqrt(PERIODS), 2)

    # Calmar is annualised return over the worst drawdown. Meaningless without
    # a drawdown to divide by, and misleading over a window too short to
    # annualise, so both are guarded.
    try:
        d0 = datetime.strptime(start, "%Y-%m-%d").date()
        d1 = datetime.strptime(end, "%Y-%m-%d").date()
        span = max(1, (d1 - d0).days + 1)
        out["trading_period_days"] = span
        opened = sess[0].get("open_equity") if sess else None
        closed = sess[-1].get("close_equity") if sess else None
        # Annualising a few weeks of drift produces numbers like "Calmar 46",
        # which is arithmetically true and completely meaningless. A quarter is
        # the shortest window worth extrapolating from; below that it stays
        # blank rather than flattering the strategy.
        if opened and closed and opened > 0 and max_dd < 0 and span >= 90:
            total = closed / opened
            annual = total ** (365.0 / span) - 1.0
            out["calmar"] = round(annual / abs(max_dd), 2)
    except (ValueError, TypeError, ZeroDivisionError, OverflowError):
        pass

    return out


def aggregate(start: str, end: str, slot: Optional[int] = None) -> dict:
    """Headline numbers for any date range — powers the Reports screen.

    `slot=None` combines every algorithm, which is the portfolio-level answer;
    pass a slot to report on one of them alone.
    """
    trades = trades_between(start, end, slot=slot)
    sess = sessions_between(start, end, slot=slot)

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

    # Max peak-to-trough decline across the window.
    #
    # The running peak starts at the equity the window opened with, not at the
    # first close. Seeding from the first close meant a window containing a
    # single losing session could only ever report 0.00% — capital fell from
    # 20,000 to 19,616 and the report claimed no drawdown at all. Each session's
    # intraday high also counts, so a day that ran up and gave it back is
    # measured from the high rather than from the close.
    max_dd = 0.0
    running_peak = open_equity or 0.0
    for s in sess:
        close = s.get("close_equity")
        if close is None:
            continue
        running_peak = max(running_peak, s.get("peak_equity") or 0.0, close)
        if running_peak > 0:
            max_dd = min(max_dd, (close - running_peak) / running_peak)

    risk = _risk_metrics(sess, trades, max_dd, start, end)

    return {
        "start": start,
        "end": end,
        "sessions": len(sess),
        "trading_days": len({t["session_date"] for t in trades}),
        "trades": n,
        **risk,
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
