from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.config import settings
from app.deps import ctx
from app.security import (
    Principal,
    client_ip,
    issue_token,
    login_throttle,
    require_auth,
    verify_password,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    password: str = Field(min_length=1, max_length=512)


class TokenResponse(BaseModel):
    token: str
    expires_in: int
    operator: str


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, request: Request) -> TokenResponse:
    ip = client_ip(request)
    login_throttle.check(ip)
    if not verify_password(body.password, settings.password_hash):
        login_throttle.fail(ip)
        ctx().db.audit(settings.operator, "login.failed", "bad password", ip)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid password")
    login_throttle.succeed(ip)
    ttl = settings.token_ttl_min * 60
    ctx().db.audit(settings.operator, "login.success", "", ip)
    return TokenResponse(
        token=issue_token(settings.operator, ttl), expires_in=ttl, operator=settings.operator
    )


@router.get("/me")
async def me(principal: Principal = Depends(require_auth)) -> dict:
    return {"operator": principal.subject, "scope": principal.scope}


@router.post("/ws-ticket")
async def ws_ticket(principal: Principal = Depends(require_auth)) -> dict:
    """Short-lived, single-purpose credential for the WebSocket handshake."""
    return {
        "ticket": issue_token(principal.subject, settings.ws_ticket_ttl_sec, scope="ws"),
        "expires_in": settings.ws_ticket_ttl_sec,
    }
