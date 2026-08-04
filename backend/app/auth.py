"""Bearer-token auth.

One shared token, held by the phone. Small surface on purpose: this service
can move real money, so the only thing it accepts is a token that matches
exactly, compared in constant time.
"""
from __future__ import annotations

import hmac
from typing import Optional

from fastapi import Header, HTTPException, Query, WebSocket, status

from .config import settings


def _valid(token: Optional[str]) -> bool:
    if not settings.api_token:
        # No token configured: refuse rather than run wide open.
        return False
    if not token:
        return False
    return hmac.compare_digest(token, settings.api_token)


def _extract(authorization: Optional[str], x_api_token: Optional[str]) -> Optional[str]:
    if x_api_token:
        return x_api_token.strip()
    if authorization:
        parts = authorization.split(None, 1)
        if len(parts) == 2 and parts[0].lower() == "bearer":
            return parts[1].strip()
        return authorization.strip()
    return None


async def require_token(
    authorization: Optional[str] = Header(default=None),
    x_api_token: Optional[str] = Header(default=None, alias="X-API-Token"),
) -> str:
    token = _extract(authorization, x_api_token)
    if not _valid(token):
        if not settings.api_token:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="API_TOKEN is not configured on the server",
            )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return token


async def require_token_query(
    token: Optional[str] = Query(default=None),
    authorization: Optional[str] = Header(default=None),
    x_api_token: Optional[str] = Header(default=None, alias="X-API-Token"),
) -> str:
    """Downloads open in the phone's browser, which cannot set headers."""
    candidate = token or _extract(authorization, x_api_token)
    if not _valid(candidate):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Invalid or missing API token")
    return candidate


async def authorise_websocket(ws: WebSocket) -> bool:
    """WebSocket handshakes carry the token as a query param or subprotocol."""
    token = ws.query_params.get("token")
    if not token:
        header = ws.headers.get("authorization") or ws.headers.get("x-api-token")
        token = _extract(header, None)
    if _valid(token):
        return True
    await ws.close(code=4401, reason="Unauthorized")
    return False
