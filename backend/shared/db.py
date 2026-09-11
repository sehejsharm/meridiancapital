"""SQLite persistence shared by the engine process and the API process.

SQLite in WAL mode is the message bus between the two processes: the engine is
the only writer of market/trade state, the API is the only writer of commands,
and both read freely. This keeps the engine alive and trading even when the API
is restarted, and survives a crash of either side without losing a trade record.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from engine.clock import now_ist

_SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;

CREATE TABLE IF NOT EXISTS kv (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    rev         INTEGER NOT NULL DEFAULT 1,
    updated_ts  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      TEXT NOT NULL,
    level   TEXT NOT NULL,
    source  TEXT NOT NULL DEFAULT 'engine',
    message TEXT NOT NULL,
    extra   TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);

CREATE TABLE IF NOT EXISTS trades (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_ts    TEXT NOT NULL,
    exit_ts     TEXT,
    session_date TEXT NOT NULL,
    mode        TEXT NOT NULL,
    view        TEXT,
    side        TEXT,
    strike      INTEGER,
    expiry      TEXT,
    tsym        TEXT,
    lots        INTEGER,
    qty         INTEGER,
    entry_prem  REAL,
    exit_prem   REAL,
    spot_entry  REAL,
    spot_exit   REAL,
    peak_pct    REAL,
    gross       REAL,
    charges     REAL,
    net         REAL,
    reason      TEXT,
    hold_min    REAL,
    equity      REAL,
    pnl_source  TEXT
);
CREATE INDEX IF NOT EXISTS idx_trades_session ON trades(session_date);

CREATE TABLE IF NOT EXISTS equity_samples (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              TEXT NOT NULL,
    session_date    TEXT NOT NULL,
    equity          REAL NOT NULL,
    realised_today  REAL,
    peak_equity     REAL,
    day_pl          REAL
);
CREATE INDEX IF NOT EXISTS idx_equity_ts ON equity_samples(ts);

CREATE TABLE IF NOT EXISTS commands (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    created_ts  TEXT NOT NULL,
    action      TEXT NOT NULL,
    payload     TEXT,
    issued_by   TEXT,
    status      TEXT NOT NULL DEFAULT 'pending',
    claimed_ts  TEXT,
    done_ts     TEXT,
    result      TEXT
);
CREATE INDEX IF NOT EXISTS idx_commands_status ON commands(status);

CREATE TABLE IF NOT EXISTS engine_runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    started_ts  TEXT NOT NULL,
    stopped_ts  TEXT,
    pid         INTEGER,
    mode        TEXT,
    trigger     TEXT,
    exit_code   INTEGER,
    reason      TEXT
);

CREATE TABLE IF NOT EXISTS holidays (
    day     TEXT PRIMARY KEY,
    label   TEXT
);

CREATE TABLE IF NOT EXISTS audit_log (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      TEXT NOT NULL,
    actor   TEXT NOT NULL,
    action  TEXT NOT NULL,
    detail  TEXT,
    ip      TEXT
);
"""

_init_lock = threading.Lock()
_initialised: set[str] = set()


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=30.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


class Database:
    """Per-call connections keep this safe across threads and processes."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.init()

    def init(self) -> None:
        key = str(self.path.resolve())
        with _init_lock:
            if key in _initialised:
                return
            with _connect(self.path) as conn:
                conn.executescript(_SCHEMA)
            _initialised.add(key)

    @contextmanager
    def conn(self) -> Iterator[sqlite3.Connection]:
        c = _connect(self.path)
        try:
            yield c
        finally:
            c.close()

    # ── key/value (engine snapshot + operator settings) ──────────────────────
    def kv_set(self, key: str, value: Any) -> int:
        payload = json.dumps(value, default=str)
        with self.conn() as c:
            c.execute(
                """INSERT INTO kv(key, value, rev, updated_ts) VALUES(?,?,1,?)
                   ON CONFLICT(key) DO UPDATE SET
                     value=excluded.value, rev=kv.rev+1, updated_ts=excluded.updated_ts""",
                (key, payload, now_ist().isoformat(timespec="seconds")),
            )
            row = c.execute("SELECT rev FROM kv WHERE key=?", (key,)).fetchone()
        return int(row["rev"]) if row else 1

    def kv_get(self, key: str, default: Any = None) -> Any:
        with self.conn() as c:
            row = c.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        if not row:
            return default
        try:
            return json.loads(row["value"])
        except json.JSONDecodeError:
            return default

    def kv_rev(self, key: str) -> int:
        with self.conn() as c:
            row = c.execute("SELECT rev FROM kv WHERE key=?", (key,)).fetchone()
        return int(row["rev"]) if row else 0

    # ── events ───────────────────────────────────────────────────────────────
    def add_event(self, level: str, message: str, extra: dict | None = None, source: str = "engine") -> int:
        with self.conn() as c:
            cur = c.execute(
                "INSERT INTO events(ts, level, source, message, extra) VALUES(?,?,?,?,?)",
                (
                    now_ist().isoformat(timespec="seconds"),
                    level,
                    source,
                    message,
                    json.dumps(extra, default=str) if extra else None,
                ),
            )
        return int(cur.lastrowid)

    def events(self, limit: int = 200, after_id: int | None = None, levels: list[str] | None = None) -> list[dict]:
        sql = "SELECT * FROM events"
        where, args = [], []
        if after_id is not None:
            where.append("id > ?")
            args.append(after_id)
        if levels:
            where.append(f"level IN ({','.join('?' * len(levels))})")
            args.extend(levels)
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        with self.conn() as c:
            rows = c.execute(sql, args).fetchall()
        return [_event_row(r) for r in rows]

    def prune_events(self, keep: int = 50_000) -> int:
        with self.conn() as c:
            cur = c.execute(
                "DELETE FROM events WHERE id <= (SELECT MAX(id) - ? FROM events)", (keep,)
            )
        return cur.rowcount or 0

    # ── trades ───────────────────────────────────────────────────────────────
    def add_trade(self, trade: dict) -> int:
        cols = [
            "entry_ts", "exit_ts", "session_date", "mode", "view", "side", "strike", "expiry",
            "tsym", "lots", "qty", "entry_prem", "exit_prem", "spot_entry", "spot_exit",
            "peak_pct", "gross", "charges", "net", "reason", "hold_min", "equity", "pnl_source",
        ]
        vals = [trade.get(k) for k in cols]
        with self.conn() as c:
            cur = c.execute(
                f"INSERT INTO trades({','.join(cols)}) VALUES({','.join('?' * len(cols))})", vals
            )
        return int(cur.lastrowid)

    def trades(self, limit: int = 500, session_date: str | None = None) -> list[dict]:
        sql = "SELECT * FROM trades"
        args: list = []
        if session_date:
            sql += " WHERE session_date = ?"
            args.append(session_date)
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        with self.conn() as c:
            rows = c.execute(sql, args).fetchall()
        return [dict(r) for r in rows]

    def trade_stats(self) -> dict:
        with self.conn() as c:
            row = c.execute(
                """SELECT COUNT(*) n,
                          SUM(CASE WHEN net > 0 THEN 1 ELSE 0 END) wins,
                          COALESCE(SUM(net), 0) net,
                          COALESCE(SUM(gross), 0) gross,
                          COALESCE(SUM(charges), 0) charges,
                          COALESCE(MAX(net), 0) best,
                          COALESCE(MIN(net), 0) worst,
                          COALESCE(AVG(hold_min), 0) avg_hold
                   FROM trades WHERE exit_ts IS NOT NULL"""
            ).fetchone()
        d = dict(row)
        d["wins"] = d["wins"] or 0
        d["losses"] = (d["n"] or 0) - d["wins"]
        d["win_rate"] = (d["wins"] / d["n"]) if d["n"] else 0.0
        return d

    # ── equity curve ─────────────────────────────────────────────────────────
    def add_equity_sample(self, session_date: str, equity: float, realised_today: float | None,
                          peak_equity: float | None, day_pl: float | None) -> None:
        with self.conn() as c:
            c.execute(
                """INSERT INTO equity_samples(ts, session_date, equity, realised_today, peak_equity, day_pl)
                   VALUES(?,?,?,?,?,?)""",
                (now_ist().isoformat(timespec="seconds"), session_date, equity,
                 realised_today, peak_equity, day_pl),
            )

    def equity_curve(self, limit: int = 1000, session_date: str | None = None) -> list[dict]:
        sql = "SELECT ts, equity, realised_today, peak_equity, day_pl FROM equity_samples"
        args: list = []
        if session_date:
            sql += " WHERE session_date = ?"
            args.append(session_date)
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        with self.conn() as c:
            rows = c.execute(sql, args).fetchall()
        return [dict(r) for r in reversed(rows)]

    def daily_equity(self, days: int = 180) -> list[dict]:
        """Last equity reading of each session — the daily curve."""
        with self.conn() as c:
            rows = c.execute(
                """SELECT session_date, equity, day_pl FROM equity_samples e
                   WHERE id = (SELECT MAX(id) FROM equity_samples WHERE session_date = e.session_date)
                   ORDER BY session_date DESC LIMIT ?""",
                (days,),
            ).fetchall()
        return [dict(r) for r in reversed(rows)]

    # ── commands (API writes, engine consumes) ───────────────────────────────
    def enqueue_command(self, action: str, payload: dict | None = None, issued_by: str = "api") -> int:
        with self.conn() as c:
            cur = c.execute(
                "INSERT INTO commands(created_ts, action, payload, issued_by) VALUES(?,?,?,?)",
                (now_ist().isoformat(timespec="seconds"), action,
                 json.dumps(payload or {}, default=str), issued_by),
            )
        return int(cur.lastrowid)

    def claim_commands(self) -> list[dict]:
        """Atomically take every pending command. Engine-side only."""
        with self.conn() as c:
            c.execute("BEGIN IMMEDIATE")
            rows = c.execute(
                "SELECT * FROM commands WHERE status='pending' ORDER BY id"
            ).fetchall()
            if rows:
                ids = [r["id"] for r in rows]
                c.execute(
                    f"UPDATE commands SET status='claimed', claimed_ts=? "
                    f"WHERE id IN ({','.join('?' * len(ids))})",
                    [now_ist().isoformat(timespec="seconds"), *ids],
                )
            c.execute("COMMIT")
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["payload"] = json.loads(d.get("payload") or "{}")
            except json.JSONDecodeError:
                d["payload"] = {}
            out.append(d)
        return out

    def finish_command(self, command_id: int, result: str, status: str = "done") -> None:
        with self.conn() as c:
            c.execute(
                "UPDATE commands SET status=?, done_ts=?, result=? WHERE id=?",
                (status, now_ist().isoformat(timespec="seconds"), result[:500], command_id),
            )

    def recent_commands(self, limit: int = 50) -> list[dict]:
        with self.conn() as c:
            rows = c.execute("SELECT * FROM commands ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def expire_stale_commands(self) -> int:
        """Commands left claimed by an engine that died never re-run."""
        with self.conn() as c:
            cur = c.execute(
                "UPDATE commands SET status='expired', done_ts=?, result='engine stopped before execution' "
                "WHERE status IN ('pending','claimed')",
                (now_ist().isoformat(timespec="seconds"),),
            )
        return cur.rowcount or 0

    # ── engine runs ──────────────────────────────────────────────────────────
    def start_run(self, pid: int, mode: str, trigger: str) -> int:
        with self.conn() as c:
            cur = c.execute(
                "INSERT INTO engine_runs(started_ts, pid, mode, trigger) VALUES(?,?,?,?)",
                (now_ist().isoformat(timespec="seconds"), pid, mode, trigger),
            )
        return int(cur.lastrowid)

    def end_run(self, run_id: int, exit_code: int | None, reason: str) -> None:
        with self.conn() as c:
            c.execute(
                "UPDATE engine_runs SET stopped_ts=?, exit_code=?, reason=? WHERE id=?",
                (now_ist().isoformat(timespec="seconds"), exit_code, reason[:300], run_id),
            )

    def recent_runs(self, limit: int = 25) -> list[dict]:
        with self.conn() as c:
            rows = c.execute("SELECT * FROM engine_runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    # ── holiday calendar ─────────────────────────────────────────────────────
    def holidays(self) -> list[dict]:
        with self.conn() as c:
            rows = c.execute("SELECT day, label FROM holidays ORDER BY day").fetchall()
        return [dict(r) for r in rows]

    def add_holiday(self, day: str, label: str = "") -> None:
        with self.conn() as c:
            c.execute(
                "INSERT INTO holidays(day, label) VALUES(?,?) "
                "ON CONFLICT(day) DO UPDATE SET label=excluded.label",
                (day, label),
            )

    def remove_holiday(self, day: str) -> None:
        with self.conn() as c:
            c.execute("DELETE FROM holidays WHERE day=?", (day,))

    # ── audit ────────────────────────────────────────────────────────────────
    def audit(self, actor: str, action: str, detail: str = "", ip: str = "") -> None:
        with self.conn() as c:
            c.execute(
                "INSERT INTO audit_log(ts, actor, action, detail, ip) VALUES(?,?,?,?,?)",
                (now_ist().isoformat(timespec="seconds"), actor, action, detail[:500], ip),
            )

    def audit_tail(self, limit: int = 100) -> list[dict]:
        with self.conn() as c:
            rows = c.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]


def _event_row(r: sqlite3.Row) -> dict:
    d = dict(r)
    if d.get("extra"):
        try:
            d["extra"] = json.loads(d["extra"])
        except json.JSONDecodeError:
            d["extra"] = None
    return d


# Well-known kv keys
K_SNAPSHOT = "engine:snapshot"
K_MODE = "settings:mode"  # "paper" | "live"
K_SCHEDULE = "settings:schedule_enabled"  # bool
K_SUPERVISOR = "supervisor:state"
