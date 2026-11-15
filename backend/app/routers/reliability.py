"""Reliability: crash loops, Angel API errors, feed divergence and uptime (app/reliability.py)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

from app import close_check, health, notify, reliability
from app.deps import ctx
from app.security import Principal, client_ip, require_auth

router = APIRouter(prefix="/api/reliability", tags=["reliability"], dependencies=[Depends(require_auth)])


class AckRequest(BaseModel):
    key: str = Field(min_length=1, max_length=200)


@router.get("")
async def get_reliability(
    days: int = Query(30, ge=1, le=120),
    algo: str | None = Query(None, max_length=80),
) -> dict:
    return reliability.report(ctx().db, days=days, algo_id=algo)


@router.post("/ack")
async def acknowledge(body: AckRequest, request: Request, principal: Principal = Depends(require_auth)) -> dict:
    """Take a crash loop off the banner once the operator has seen it."""
    c = ctx()
    reliability.acknowledge(c.db, body.key)
    reliability.clear_cache()
    c.db.audit(principal.subject, "reliability.ack", body.key, client_ip(request))
    return {"ok": True, "alerts": reliability.banner_alerts(c.db)}


@router.get("/alerts")
async def alert_channels() -> dict:
    """Which alert channels are configured (never the secrets themselves)."""
    return {"channels": notify.channels(), "kinds": sorted(notify.TITLES)}


@router.post("/alerts/test")
async def test_alert(request: Request, principal: Principal = Depends(require_auth)) -> dict:
    c = ctx()
    c.db.audit(principal.subject, "alerts.test", "", client_ip(request))
    import asyncio

    return await asyncio.to_thread(notify.test_message, c.db)


@router.get("/host")
async def host() -> dict:
    """The machine (processor, memory, disk) and the desk's housekeeping: the outside
    heartbeat, the last after-close check, journal retention."""
    import asyncio

    c = ctx()
    machine = await asyncio.to_thread(health.host, c.db)
    upkeep = c.upkeep.status() if c.upkeep is not None else None
    return {"machine": machine, "upkeep": upkeep}


@router.get("/close-check")
async def get_close_check(day: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$")) -> dict:
    """The after-close check for one day (the latest when no day is named)."""
    c = ctx()
    found = c.db.kv_get(close_check.key(day) if day else close_check.K_LATEST, None)
    return {"check": found}


@router.post("/close-check/run")
async def run_close_check(request: Request, principal: Principal = Depends(require_auth)) -> dict:
    """Run the check now. Angel is asked only if nothing is running (a second login
    would log a running program out) and the credentials are in the server's settings."""
    import asyncio

    from engine.clock import now_ist

    c = ctx()
    c.db.audit(principal.subject, "close_check.run", "", client_ip(request))
    c.fleet.refresh()
    running = any(s.state.running for s in c.fleet.all().values())
    day = now_ist().date().isoformat()
    angel = getattr(c.upkeep, "angel", close_check.ask_angel)
    result = await asyncio.to_thread(lambda: close_check.run(c.db, day, running=running, angel=angel))
    c.db.add_event({"ok": "ok", "check": "warn", "problem": "critical"}[result["status"]],
                   close_check.headline(result) + f" (run by {principal.subject})", source="api")
    return {"check": result}
