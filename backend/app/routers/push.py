"""Phone notifications: this server's public key, and the devices that asked for alerts (app/webpush.py)."""

from __future__ import annotations

import asyncio
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app import webpush
from app.deps import ctx
from app.security import Principal, client_ip, require_auth

router = APIRouter(prefix="/api/push", tags=["push"], dependencies=[Depends(require_auth)])


class Keys(BaseModel):
    p256dh: str = Field(min_length=80, max_length=120)
    auth: str = Field(min_length=20, max_length=40)


class SubscribeRequest(BaseModel):
    endpoint: str = Field(min_length=20, max_length=1000)
    keys: Keys
    label: str = Field(default="", max_length=60)


class UnsubscribeRequest(BaseModel):
    endpoint: str = Field(min_length=20, max_length=1000)


@router.get("")
async def push_status() -> dict:
    return webpush.status(ctx().db)


@router.post("/subscribe")
async def subscribe(body: SubscribeRequest, request: Request, principal: Principal = Depends(require_auth)) -> dict:
    c = ctx()
    try:
        webpush.subscribe(c.db, body.endpoint, body.keys.p256dh, body.keys.auth, body.label.strip())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    c.db.audit(principal.subject, "push.subscribe", body.label.strip() or "phone", client_ip(request))
    return webpush.status(c.db)


@router.post("/unsubscribe")
async def unsubscribe(body: UnsubscribeRequest, request: Request, principal: Principal = Depends(require_auth)) -> dict:
    c = ctx()
    webpush.unsubscribe(c.db, body.endpoint)
    c.db.audit(principal.subject, "push.unsubscribe", "", client_ip(request))
    return webpush.status(c.db)


@router.post("/test")
async def test(request: Request, principal: Principal = Depends(require_auth)) -> dict:
    c = ctx()
    c.db.audit(principal.subject, "push.test", "", client_ip(request))
    if not webpush.subscriptions(c.db):
        return {"ok": False, "detail": "no phone has asked for notifications yet"}
    r = await asyncio.to_thread(webpush.send_all, "Test alert",
                                f"Meridian Capital notifications are working ({time.strftime('%H:%M:%S')}).",
                                "test", "info", c.db)
    detail = None if r["sent"] else ("the push service refused it; turn notifications off and on again on the phone"
                                     if r["removed"] else "the push service could not be reached from the server")
    return {"ok": r["sent"] > 0, "detail": detail, "result": r}
