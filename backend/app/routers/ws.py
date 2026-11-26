from __future__ import annotations

import asyncio
import contextlib
import time

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect, status

from app import devices
from app.deps import ctx
from app.security import authenticate_ws_ticket

router = APIRouter(tags=["stream"])

# How often an open socket re-checks the session it was opened under: a revoked
# or signed-out device stops receiving the live feed within this long.
RECHECK_SEC = 15.0


async def _watch_session(ws: WebSocket, payload: dict, expires: int) -> None:
    """Close the socket when the session behind it ends: the device revoked or
    signed out, every session ended, or the session's own expiry reached."""
    while True:
        await asyncio.sleep(RECHECK_SEC)
        problem = devices.session_problem(ctx().db, payload)
        if problem is None and expires and time.time() >= expires:
            problem = "session expired"
        if problem:
            with contextlib.suppress(Exception):
                await ws.close(code=status.WS_1008_POLICY_VIOLATION, reason=problem[:120])
            return


@router.websocket("/ws/live")
async def live(ws: WebSocket, ticket: str = Query(..., max_length=4096)) -> None:
    try:
        principal = authenticate_ws_ticket(ticket)
    except ValueError:
        await ws.close(code=status.WS_1008_POLICY_VIOLATION, reason="invalid ticket")
        return

    await ws.accept()
    hub = ctx().hub
    try:
        await hub.connect(ws)
    except Exception:            # the hello could not be sent: a dead or stalled socket
        await hub.disconnect(ws)
        try:
            await ws.close()
        except Exception:
            pass
        return
    claims = {"dev": principal.device, "piat": principal.issued_ms}
    watcher = asyncio.create_task(_watch_session(ws, claims, principal.expires or 0))
    try:
        while True:
            # The client sends nothing meaningful; this read is how we notice a close.
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        watcher.cancel()
        await hub.disconnect(ws)
