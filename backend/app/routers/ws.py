from __future__ import annotations

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect, status

from app.deps import ctx
from app.security import authenticate_ws_ticket

router = APIRouter(tags=["stream"])


@router.websocket("/ws/live")
async def live(ws: WebSocket, ticket: str = Query(...)) -> None:
    try:
        authenticate_ws_ticket(ticket)
    except ValueError:
        await ws.close(code=status.WS_1008_POLICY_VIOLATION, reason="invalid ticket")
        return

    await ws.accept()
    hub = ctx().hub
    await hub.connect(ws)
    try:
        while True:
            # The client sends nothing meaningful; this read is how we notice a close.
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        await hub.disconnect(ws)
