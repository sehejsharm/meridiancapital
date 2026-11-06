"""A past session, put back together for replay: the index, the P&L, the trades, the journal.

Post-mortems meant opening the blotter, the journal and a chart in three tabs
and lining up the times by hand. Everything needed is already recorded — the
algorithm's minute-by-minute P&L samples (with the index, from 7 Nov 2026 on),
its trades and its journal — except the NIFTY candles, which were only ever
held for the current day. The archiver below keeps each day's one-minute
candles, so sessions from now on replay against the market itself.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from engine.clock import now_ist

BARS_KEY = "market:bars:{day}"
ARCHIVE_EVERY_SEC = 300.0
SESSION_START, SESSION_END = (9, 15), (15, 40)


def bars_day(bar: dict) -> str | None:
    """The IST date of a chart bar (``t`` is epoch seconds already shifted to IST)."""
    try:
        return datetime.fromtimestamp(int(bar["t"]), tz=timezone.utc).date().isoformat()
    except (KeyError, TypeError, ValueError, OSError):
        return None


def archive_today(db, chart=None) -> int:
    """Keep today's one-minute NIFTY candles. Returns how many were stored."""
    from app import market

    today = now_ist().date().isoformat()
    data = chart if chart is not None else market.nifty_chart(db)
    bars = [b for b in (data.get("bars") or []) if bars_day(b) == today]
    if not bars:
        return 0
    stored = db.kv_get(BARS_KEY.format(day=today), None) or {}
    # Angel's candles (the engine trades on them) are never replaced by Yahoo's.
    if stored.get("source") == "angel" and data.get("source") != "angel" and len(stored.get("bars") or []) >= len(bars):
        return 0
    db.kv_set(BARS_KEY.format(day=today), {"day": today, "source": data.get("source"), "bars": bars})
    return len(bars)


def session(db, day: str | None, algo_id: str | None) -> dict:
    """Everything a replay of one session needs, oldest first."""
    from app import ledger, market

    algo_id = algo_id or ledger.live_algo(db)
    if not algo_id:
        return {"algo_id": None, "day": None, "sessions": [], "samples": [], "trades": [], "events": [], "bars": []}
    with db.conn() as c:
        traded = [r["session_date"] for r in c.execute(
            "SELECT DISTINCT session_date FROM trades WHERE algo_id = ? ORDER BY session_date DESC LIMIT 90",
            (algo_id,))]
    sessions = sorted(set(db.sample_sessions(algo_id)) | set(traded), reverse=True)
    day = day or (sessions[0] if sessions else now_ist().date().isoformat())
    trades = ledger.reconcile(db, db.trades(limit=50, session_date=day, algo_id=algo_id))
    trades.sort(key=lambda t: str(t.get("entry_ts") or ""))
    events = list(reversed(db.events(limit=3000, algo_id=algo_id, day=day)))
    archived = db.kv_get(BARS_KEY.format(day=day), None) or {}
    bars, bars_source = archived.get("bars") or [], archived.get("source")
    if not bars and day == now_ist().date().isoformat():
        live = market.nifty_chart(db)
        bars, bars_source = [b for b in live.get("bars") or [] if bars_day(b) == day], live.get("source")
    algo = db.algo(algo_id) or {}
    return {
        "algo_id": algo_id,
        "name": algo.get("name") or algo_id,
        "day": day,
        "sessions": sessions,
        "samples": db.session_samples(day, algo_id),
        "trades": trades,
        "events": [{k: e.get(k) for k in ("id", "ts", "level", "source", "message")} for e in events],
        "bars": bars,
        "bars_source": bars_source,
        "report": db.kv_get(f"report:eod:{day}:{algo_id}", None),
    }


class Archiver:
    """Keeps each day's candles while the market is open, every few minutes."""

    def __init__(self, db):
        self.db = db
        self._task: asyncio.Task | None = None

    def due(self) -> bool:
        t = now_ist()
        hm = (t.hour, t.minute)
        return t.weekday() < 5 and SESSION_START <= hm <= SESSION_END

    async def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="meridian-archiver")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _loop(self) -> None:
        while True:
            try:
                if self.due():
                    await asyncio.to_thread(archive_today, self.db)
            except Exception:  # an archive gap must never take the API down
                pass
            await asyncio.sleep(ARCHIVE_EVERY_SEC)
