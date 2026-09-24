from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.deps import ctx, system_status
from app.security import Principal, require_auth
from engine.clock import now_ist
from shared.db import DEFAULT_ALGO, K_SNAPSHOT

router = APIRouter(prefix="/api", tags=["data"], dependencies=[Depends(require_auth)])


@router.get("/status")
async def status() -> dict:
    c = ctx()
    return system_status(c.db, c.sup, c.sched, c.fleet)


@router.get("/snapshot")
async def snapshot() -> dict:
    c = ctx()
    return {
        "snapshot": c.db.kv_get(K_SNAPSHOT, None),
        "status": system_status(c.db, c.sup, c.sched, c.fleet),
    }


@router.get("/trades")
async def trades(
    limit: int = Query(200, ge=1, le=1000),
    session_date: str | None = None,
) -> dict:
    c = ctx()
    return {
        "trades": c.db.trades(limit=limit, session_date=session_date),
        "stats": c.db.trade_stats(),
    }


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
) -> dict:
    c = ctx()
    return {
        "intraday": c.db.equity_curve(limit=limit, session_date=session_date or now_ist().strftime("%Y-%m-%d")),
        "daily": c.db.daily_equity(),
    }


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
