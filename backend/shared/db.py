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
    extra   TEXT,
    algo_id TEXT NOT NULL DEFAULT 'gk50k'
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
    pnl_source  TEXT,
    algo_id TEXT NOT NULL DEFAULT 'gk50k'
);
CREATE INDEX IF NOT EXISTS idx_trades_session ON trades(session_date);

CREATE TABLE IF NOT EXISTS equity_samples (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              TEXT NOT NULL,
    session_date    TEXT NOT NULL,
    equity          REAL NOT NULL,
    realised_today  REAL,
    peak_equity     REAL,
    day_pl          REAL,
    algo_id TEXT NOT NULL DEFAULT 'gk50k'
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
    result      TEXT,
    algo_id TEXT NOT NULL DEFAULT 'gk50k'
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
    reason      TEXT,
    algo_id TEXT NOT NULL DEFAULT 'gk50k'
);

CREATE TABLE IF NOT EXISTS holidays (
    day     TEXT PRIMARY KEY,
    label   TEXT
);

CREATE TABLE IF NOT EXISTS algos (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    kind            TEXT NOT NULL DEFAULT 'uploaded',
    mode            TEXT NOT NULL DEFAULT 'paper',
    active_version  INTEGER,
    enabled         INTEGER NOT NULL DEFAULT 0,
    created_ts      TEXT NOT NULL,
    notes           TEXT,
    shadow_of       TEXT
);

CREATE TABLE IF NOT EXISTS algo_versions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    algo_id         TEXT NOT NULL,
    version         INTEGER NOT NULL,
    created_ts      TEXT NOT NULL,
    uploaded_by     TEXT,
    source          TEXT NOT NULL,
    sha256          TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending',
    gate_report     TEXT,
    paper_sessions  INTEGER NOT NULL DEFAULT 0,
    UNIQUE(algo_id, version)
);
CREATE INDEX IF NOT EXISTS idx_versions_algo ON algo_versions(algo_id);

CREATE TABLE IF NOT EXISTS passkeys (
    credential_id   TEXT PRIMARY KEY,
    public_key      TEXT NOT NULL,
    sign_count      INTEGER NOT NULL DEFAULT 0,
    label           TEXT NOT NULL DEFAULT '',
    transports      TEXT,
    created_ts      TEXT NOT NULL,
    last_used_ts    TEXT
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

# Everything written before multi-algo belongs to the built-in build.
DEFAULT_ALGO = "gk50k"
# Events that belong to the desk rather than to one algorithm: the API starting,
# the scheduler, an emergency stop. They used to default to the built-in's id,
# which filed every one of them in its journal.
SYSTEM_ALGO = "system"

_init_lock = threading.Lock()
_initialised: set[str] = set()


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=30.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


# Columns added after the first release. CREATE TABLE IF NOT EXISTS will not add
# a column to a table that already exists, so a database created before
# multi-algo needs them applied by hand. Existing rows take the DEFAULT, which
# attaches all prior history to the built-in build.
_ADDED_COLUMNS = (
    ("events", "algo_id", "TEXT NOT NULL DEFAULT 'gk50k'"),
    ("trades", "algo_id", "TEXT NOT NULL DEFAULT 'gk50k'"),
    ("equity_samples", "algo_id", "TEXT NOT NULL DEFAULT 'gk50k'"),
    ("commands", "algo_id", "TEXT NOT NULL DEFAULT 'gk50k'"),
    ("engine_runs", "algo_id", "TEXT NOT NULL DEFAULT 'gk50k'"),
    ("algos", "shadow_of", "TEXT"),
)


def _migrate(conn: sqlite3.Connection) -> None:
    for table, column, decl in _ADDED_COLUMNS:
        cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        if not cols:  # table absent entirely; the schema script owns it
            continue
        if column not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
    for table in ("events", "trades", "equity_samples", "commands"):
        conn.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{table}_algo ON {table}(algo_id)"
        )


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
                _migrate(conn)
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
    def add_event(
        self,
        level: str,
        message: str,
        extra: dict | None = None,
        source: str = "engine",
        algo_id: str = SYSTEM_ALGO,
    ) -> int:
        with self.conn() as c:
            cur = c.execute(
                "INSERT INTO events(ts, level, source, message, extra, algo_id) VALUES(?,?,?,?,?,?)",
                (
                    now_ist().isoformat(timespec="seconds"),
                    level,
                    source,
                    message,
                    json.dumps(extra, default=str) if extra else None,
                    algo_id,
                ),
            )
        return int(cur.lastrowid)

    def events(
        self,
        limit: int = 200,
        after_id: int | None = None,
        levels: list[str] | None = None,
        algo_id: str | None = None,
    ) -> list[dict]:
        sql = "SELECT * FROM events"
        where, args = [], []
        if after_id is not None:
            where.append("id > ?")
            args.append(after_id)
        if levels:
            where.append(f"level IN ({','.join('?' * len(levels))})")
            args.extend(levels)
        if algo_id:
            where.append("algo_id = ?")
            args.append(algo_id)
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
            "algo_id",
        ]
        vals = [trade.get(k) if k != "algo_id" else trade.get(k, DEFAULT_ALGO) for k in cols]
        with self.conn() as c:
            cur = c.execute(
                f"INSERT INTO trades({','.join(cols)}) VALUES({','.join('?' * len(cols))})", vals
            )
        return int(cur.lastrowid)

    def trades(
        self,
        limit: int = 500,
        session_date: str | None = None,
        algo_id: str | None = None,
    ) -> list[dict]:
        sql = "SELECT * FROM trades"
        where, args = [], []
        if session_date:
            where.append("session_date = ?")
            args.append(session_date)
        if algo_id:
            where.append("algo_id = ?")
            args.append(algo_id)
        if where:
            sql += " WHERE " + " AND ".join(where)
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
                          peak_equity: float | None, day_pl: float | None,
                          algo_id: str = DEFAULT_ALGO) -> None:
        with self.conn() as c:
            c.execute(
                """INSERT INTO equity_samples(ts, session_date, equity, realised_today,
                                              peak_equity, day_pl, algo_id)
                   VALUES(?,?,?,?,?,?,?)""",
                (now_ist().isoformat(timespec="seconds"), session_date, equity,
                 realised_today, peak_equity, day_pl, algo_id),
            )

    def equity_curve(
        self,
        limit: int = 1000,
        session_date: str | None = None,
        algo_id: str | None = None,
    ) -> list[dict]:
        sql = "SELECT ts, equity, realised_today, peak_equity, day_pl FROM equity_samples"
        where, args = [], []
        if session_date:
            where.append("session_date = ?")
            args.append(session_date)
        if algo_id:
            where.append("algo_id = ?")
            args.append(algo_id)
        if where:
            sql += " WHERE " + " AND ".join(where)
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
    def enqueue_command(self, action: str, payload: dict | None = None, issued_by: str = "api",
                        algo_id: str = DEFAULT_ALGO) -> int:
        with self.conn() as c:
            cur = c.execute(
                "INSERT INTO commands(created_ts, action, payload, issued_by, algo_id) "
                "VALUES(?,?,?,?,?)",
                (now_ist().isoformat(timespec="seconds"), action,
                 json.dumps(payload or {}, default=str), issued_by, algo_id),
            )
        return int(cur.lastrowid)

    def claim_commands(self, algo_id: str = DEFAULT_ALGO) -> list[dict]:
        """Atomically take this algorithm's pending commands. Engine-side only.

        Scoped by algo_id: with several engines sharing the bus, an unscoped
        claim would let one engine swallow another's halt or flatten and then
        act on it against the wrong position.
        """
        with self.conn() as c:
            c.execute("BEGIN IMMEDIATE")
            rows = c.execute(
                "SELECT * FROM commands WHERE status='pending' AND algo_id=? ORDER BY id",
                (algo_id,),
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

    def recent_commands(self, limit: int = 50, algo_id: str | None = None) -> list[dict]:
        sql, args = "SELECT * FROM commands", []
        if algo_id:
            sql += " WHERE algo_id = ?"
            args.append(algo_id)
        with self.conn() as c:
            rows = c.execute(sql + " ORDER BY id DESC LIMIT ?", (*args, limit)).fetchall()
        return [dict(r) for r in rows]

    def expire_stale_commands(self, algo_id: str | None = None) -> int:
        """Commands left claimed by an engine that died never re-run.

        Scoped to the engine that stopped. Unscoped, one algorithm stopping
        expired every other algorithm's queue — including a flatten the
        emergency stop had just queued for an engine still holding a position.
        """
        sql = (
            "UPDATE commands SET status='expired', done_ts=?, result='engine stopped before execution' "
            "WHERE status IN ('pending','claimed')"
        )
        args: list[Any] = [now_ist().isoformat(timespec="seconds")]
        if algo_id:
            sql += " AND algo_id = ?"
            args.append(algo_id)
        with self.conn() as c:
            cur = c.execute(sql, args)
        return cur.rowcount or 0

    # ── engine runs ──────────────────────────────────────────────────────────
    def start_run(self, pid: int, mode: str, trigger: str, algo_id: str = DEFAULT_ALGO) -> int:
        with self.conn() as c:
            cur = c.execute(
                """INSERT INTO engine_runs(started_ts, pid, mode, trigger, algo_id)
                   VALUES(?,?,?,?,?)""",
                (now_ist().isoformat(timespec="seconds"), pid, mode, trigger, algo_id),
            )
        return int(cur.lastrowid)

    def end_run(self, run_id: int, exit_code: int | None, reason: str) -> None:
        with self.conn() as c:
            c.execute(
                "UPDATE engine_runs SET stopped_ts=?, exit_code=?, reason=? WHERE id=?",
                (now_ist().isoformat(timespec="seconds"), exit_code, reason[:300], run_id),
            )

    def recent_runs(self, limit: int = 25, algo_id: str | None = None) -> list[dict]:
        sql, args = "SELECT * FROM engine_runs", []
        if algo_id:
            sql += " WHERE algo_id = ?"
            args.append(algo_id)
        with self.conn() as c:
            rows = c.execute(sql + " ORDER BY id DESC LIMIT ?", (*args, limit)).fetchall()
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
    # ── passkeys (Face ID / Touch ID / security keys) ────────────────────────
    def add_passkey(self, credential_id: str, public_key: str, sign_count: int,
                    label: str, transports: str = "") -> None:
        with self.conn() as c:
            c.execute(
                """INSERT INTO passkeys(credential_id, public_key, sign_count, label,
                                        transports, created_ts)
                   VALUES(?,?,?,?,?,?)
                   ON CONFLICT(credential_id) DO UPDATE SET
                     public_key=excluded.public_key, label=excluded.label,
                     transports=excluded.transports""",
                (credential_id, public_key, sign_count, label, transports,
                 now_ist().isoformat(timespec="seconds")),
            )

    def passkeys(self) -> list[dict]:
        with self.conn() as c:
            return [
                dict(r)
                for r in c.execute(
                    """SELECT credential_id, label, transports, created_ts, last_used_ts,
                              sign_count
                       FROM passkeys ORDER BY created_ts"""
                )
            ]

    def passkey(self, credential_id: str) -> dict | None:
        with self.conn() as c:
            r = c.execute(
                "SELECT * FROM passkeys WHERE credential_id=?", (credential_id,)
            ).fetchone()
            return dict(r) if r else None

    def touch_passkey(self, credential_id: str, sign_count: int) -> None:
        with self.conn() as c:
            c.execute(
                "UPDATE passkeys SET sign_count=?, last_used_ts=? WHERE credential_id=?",
                (sign_count, now_ist().isoformat(timespec="seconds"), credential_id),
            )

    def delete_passkey(self, credential_id: str) -> None:
        with self.conn() as c:
            c.execute("DELETE FROM passkeys WHERE credential_id=?", (credential_id,))

    # ── algo registry ────────────────────────────────────────────────────────
    def algos(self) -> list[dict]:
        with self.conn() as c:
            return [dict(r) for r in c.execute("SELECT * FROM algos ORDER BY created_ts")]

    def algo(self, algo_id: str) -> dict | None:
        with self.conn() as c:
            r = c.execute("SELECT * FROM algos WHERE id=?", (algo_id,)).fetchone()
            return dict(r) if r else None

    def upsert_algo(self, algo_id: str, name: str, kind: str = "uploaded", notes: str = "") -> None:
        with self.conn() as c:
            c.execute(
                """INSERT INTO algos(id, name, kind, created_ts, notes) VALUES(?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET name=excluded.name, notes=excluded.notes""",
                (algo_id, name, kind, now_ist().isoformat(timespec="seconds"), notes),
            )

    def set_algo_fields(self, algo_id: str, **fields: Any) -> None:
        allowed = {"name", "mode", "active_version", "enabled", "notes", "shadow_of"}
        cols = {k: v for k, v in fields.items() if k in allowed}
        if not cols:
            return
        sets = ", ".join(f"{k}=?" for k in cols)
        with self.conn() as c:
            c.execute(f"UPDATE algos SET {sets} WHERE id=?", (*cols.values(), algo_id))

    def delete_algo(self, algo_id: str) -> None:
        with self.conn() as c:
            c.execute("DELETE FROM algo_versions WHERE algo_id=?", (algo_id,))
            c.execute("DELETE FROM algos WHERE id=?", (algo_id,))

    # ── algo versions ────────────────────────────────────────────────────────
    def add_version(self, algo_id: str, source: str, sha256: str, uploaded_by: str) -> int:
        with self.conn() as c:
            nxt = c.execute(
                "SELECT COALESCE(MAX(version), 0) + 1 FROM algo_versions WHERE algo_id=?",
                (algo_id,),
            ).fetchone()[0]
            cur = c.execute(
                """INSERT INTO algo_versions(algo_id, version, created_ts, uploaded_by, source, sha256)
                   VALUES(?,?,?,?,?,?)""",
                (algo_id, nxt, now_ist().isoformat(timespec="seconds"), uploaded_by, source, sha256),
            )
            return int(cur.lastrowid)

    def versions(self, algo_id: str, limit: int = 50) -> list[dict]:
        """Version metadata without the source blob, which is large and rarely needed."""
        with self.conn() as c:
            return [
                dict(r)
                for r in c.execute(
                    """SELECT id, algo_id, version, created_ts, uploaded_by, sha256,
                              status, gate_report, paper_sessions
                       FROM algo_versions WHERE algo_id=? ORDER BY version DESC LIMIT ?""",
                    (algo_id, limit),
                )
            ]

    def version(self, version_id: int) -> dict | None:
        with self.conn() as c:
            r = c.execute("SELECT * FROM algo_versions WHERE id=?", (version_id,)).fetchone()
            return dict(r) if r else None

    def set_version_status(self, version_id: int, status: str, gate_report: Any = None) -> None:
        with self.conn() as c:
            if gate_report is None:
                c.execute("UPDATE algo_versions SET status=? WHERE id=?", (status, version_id))
            else:
                c.execute(
                    "UPDATE algo_versions SET status=?, gate_report=? WHERE id=?",
                    (status, json.dumps(gate_report, default=str), version_id),
                )

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


def snapshot_key(algo_id: str = DEFAULT_ALGO) -> str:
    """Where an engine publishes its live state for the dashboard."""
    return K_SNAPSHOT if algo_id == DEFAULT_ALGO else f"{K_SNAPSHOT}:{algo_id}"
K_MODE = "settings:mode"  # "paper" | "live"
K_SCHEDULE = "settings:schedule_enabled"  # bool
K_TUNING = "settings:tuning"  # dashboard overrides of the strategy parameters
K_SUPERVISOR = "supervisor:state"
