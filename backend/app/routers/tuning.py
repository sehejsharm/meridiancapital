"""Strategy parameter surface.

The dashboard renders its editor from `GET /api/tuning` and writes back to
`POST /api/tuning`, so the form and the validation can never drift apart —
both come from `engine.tuning.PARAMS`.

Three deliberate rules, all of which exist because this moves real money:

*   **Nothing takes effect until the engine restarts.** The engine reads its
    parameters once at startup. Saving while it runs stages the change and the
    response says so; it never mutates the ladder under an open position.
*   **Raising risk needs the phrase.** Any change that moves a parameter in the
    riskier direction requires the operator to type it, exactly as going live
    and flattening already do.
*   **Every write is audited**, with the before and after value and the
    caller's IP.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.deps import ctx
from app.security import Principal, client_ip, require_auth
from engine import tuning
from shared.db import K_TUNING

router = APIRouter(prefix="/api/tuning", tags=["tuning"], dependencies=[Depends(require_auth)])

RETUNE_PHRASE = "RETUNE"


class SaveRequest(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict)
    confirm: str = ""


def _stored() -> dict[str, Any]:
    return ctx().db.kv_get(K_TUNING, None) or {}


def _payload() -> dict:
    """Schema, backtested defaults, saved overrides and the resulting values."""
    saved = _stored()
    defaults = tuning.defaults()
    effective = {**defaults, **saved}
    running = ctx().sup.state.running
    return {
        "params": tuning.describe(),
        "groups": list(tuning.GROUPS),
        "defaults": defaults,
        "overrides": saved,
        "effective": effective,
        "changed": sorted(k for k in effective if effective[k] != defaults[k]),
        "riskier": tuning.riskier_keys(saved),
        "engine_running": running,
        # The engine binds parameters at startup, so anything saved while it is
        # up is staged rather than live.
        "pending_restart": bool(saved) and running,
        "confirm_phrase": RETUNE_PHRASE,
    }


@router.get("")
async def get_tuning() -> dict:
    return _payload()


@router.post("")
async def save_tuning(
    body: SaveRequest, request: Request, principal: Principal = Depends(require_auth)
) -> dict:
    c = ctx()
    defaults = tuning.defaults()

    try:
        clean = tuning.validate(body.values)
    except tuning.TuningError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    # Store only genuine deviations, so resetting a field really does restore
    # the backtested value rather than pinning it at today's default.
    overrides = {k: v for k, v in clean.items() if v != defaults[k]}
    hot = tuning.riskier_keys(overrides)
    if hot and body.confirm != RETUNE_PHRASE:
        labels = ", ".join(tuning.BY_KEY[k].label for k in hot)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"{labels} move risk up — this requires confirm == '{RETUNE_PHRASE}'"
            ),
        )

    before = _stored()
    c.db.kv_set(K_TUNING, overrides)

    diff = [
        f"{k}: {before.get(k, defaults[k])} -> {overrides.get(k, defaults[k])}"
        for k in sorted(set(before) | set(overrides))
        if before.get(k, defaults[k]) != overrides.get(k, defaults[k])
    ]
    c.db.audit(principal.subject, "tuning.save", "; ".join(diff) or "no change", client_ip(request))
    if diff:
        c.db.add_event(
            "warn",
            f"strategy parameters changed by {principal.subject}: {'; '.join(diff)}",
            source="api",
        )
    return {"ok": True, "applied_on_restart": True, **_payload()}


@router.post("/reset")
async def reset_tuning(request: Request, principal: Principal = Depends(require_auth)) -> dict:
    """Drop every override and go back to the backtested configuration."""
    c = ctx()
    had = _stored()
    c.db.kv_set(K_TUNING, {})
    c.db.audit(principal.subject, "tuning.reset", f"cleared {len(had)} override(s)", client_ip(request))
    if had:
        c.db.add_event(
            "warn",
            f"strategy parameters reset to backtested defaults by {principal.subject}",
            source="api",
        )
    return {"ok": True, **_payload()}
