"""Reliability: crash loops, Angel API errors, feed divergence and uptime (app/reliability.py)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

from app import reliability
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
