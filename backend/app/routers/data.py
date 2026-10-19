from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app import ledger
from app.deps import ctx, system_status
from app.program_status import live_snapshot_key
from app.security import Principal, require_auth
from engine.clock import now_ist
from shared.db import DEFAULT_ALGO, snapshot_key

router = APIRouter(prefix="/api", tags=["data"], dependencies=[Depends(require_auth)])


@router.get("/status")
async def status() -> dict:
    c = ctx()
    return system_status(c.db, c.sup, c.sched, c.fleet)


@router.get("/snapshot")
async def snapshot() -> dict:
    c = ctx()
    return {
        "snapshot": c.db.kv_get(live_snapshot_key(c.db, c.fleet), None),
        "status": system_status(c.db, c.sup, c.sched, c.fleet),
    }


@router.get("/trades")
async def trades(
    limit: int = Query(200, ge=1, le=1000),
    session_date: str | None = None,
) -> dict:
    """Every trade, with Angel's own charges and net beside the recorded ones;
    the headline figures are over real-money trades, from Angel's numbers."""
    c = ctx()
    rows = ledger.reconcile(c.db, c.db.trades(limit=limit, session_date=session_date))
    every = rows if session_date is None and len(rows) < limit else ledger.reconcile(c.db, c.db.trades(limit=5000))
    # Newest first by when the trade was placed: a trade imported late (a
    # program's log read after a restart) is not a newer trade.
    rows.sort(key=lambda t: (t.get("entry_ts") or t.get("exit_ts") or "").replace(" ", "T"), reverse=True)
    return {"trades": rows, "stats": ledger.stats(every)}


@router.get("/events")
async def events(
    limit: int = Query(200, ge=1, le=1000),
    after_id: int | None = None,
    level: list[str] | None = Query(None),
    algo: str | None = Query(None, max_length=80),
) -> dict:
    """The journal, optionally for one algorithm (or ``system`` for desk-wide events)."""
    return {"events": ctx().db.events(limit=limit, after_id=after_id, levels=level, algo_id=algo)}


@router.get("/equity")
async def equity(
    session_date: str | None = None,
    limit: int = Query(1000, ge=1, le=5000),
    daily: bool = True,
) -> dict:
    c = ctx()
    today = now_ist().strftime("%Y-%m-%d")
    day = session_date or today
    curve = c.db.equity_curve(limit=limit, session_date=day)
    if not curve and not session_date:
        # Before today's first mark (or on a day off) the chart shows the last
        # session it has, labelled as such, rather than an empty box.
        day = c.db.latest_equity_session() or today
        curve = c.db.equity_curve(limit=limit, session_date=day) if day != today else []
    return {
        "intraday": curve,
        "session_date": day,
        "recorded": day != today,
        # The deck shows only the day's curve and asks for this off.
        "daily": _daily_balances(c, today) if daily else [],
    }


def _daily_balances(c, today: str) -> list[dict]:
    """Closing balance at Angel for each session of the real-money algorithm:
    the next morning's opening balance, and today's live trading balance."""
    algo = ledger.live_algo(c.db)
    if algo is None:
        return []
    snap = c.db.kv_get(snapshot_key(algo), None) or {}
    money = ((snap.get("account") or {}).get("money") or {})
    live_balance = money.get("balance") if (snap.get("position") is None and str(snap.get("ts") or "")[:10] == today) else None
    return ledger.closing_balances(c.db, algo, live_balance=live_balance, today=today)


@router.get("/reports/eod")
async def eod_report(date: str | None = None, algo: str | None = Query(None, max_length=80)) -> dict:
    db = ctx().db
    if not algo:
        key = f"report:eod:{date}" if date else "report:eod:latest"
        return {"report": db.kv_get(key, None)}
    key = f"report:eod:{date}:{algo}" if date else f"report:eod:latest:{algo}"
    report = db.kv_get(key, None)
    if report is None and algo == DEFAULT_ALGO:
        # The built-in's reports from before they were kept per algorithm.
        report = db.kv_get(f"report:eod:{date}" if date else "report:eod:latest", None)
    return {"report": report}


@router.get("/runs")
async def runs(
    limit: int = Query(25, ge=1, le=200), algo: str | None = Query(None, max_length=80)
) -> dict:
    return {"runs": ctx().db.recent_runs(limit, algo_id=algo)}


@router.get("/commands")
async def commands(
    limit: int = Query(50, ge=1, le=200), algo: str | None = Query(None, max_length=80)
) -> dict:
    return {"commands": ctx().db.recent_commands(limit, algo_id=algo)}


@router.get("/audit")
async def audit(
    limit: int = Query(100, ge=1, le=500),
    _p: Principal = Depends(require_auth),
) -> dict:
    return {"entries": ctx().db.audit_tail(limit)}
