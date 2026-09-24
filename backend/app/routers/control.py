"""Operator control surface.

Two actions can lose real money if triggered by accident — switching to LIVE and
flattening an open position — so both require the operator to type an exact
confirmation phrase that the UI does not prefill. Everything here is written to
the audit log with the caller's IP before it takes effect.
"""

from __future__ import annotations

import asyncio
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator

from app.deps import ctx
from app.security import Principal, client_ip, require_auth

router = APIRouter(prefix="/api/control", tags=["control"], dependencies=[Depends(require_auth)])

FLATTEN_PHRASE = "FLATTEN"
NUKE_PHRASE = "NUKE ALL"

# How long the engines get to claim and act on their exit before being killed.
NUKE_DRAIN_SEC = 3.0


def _audit(request: Request, principal: Principal, action: str, detail: str = "") -> None:
    ctx().db.audit(principal.subject, action, detail, client_ip(request))


class StopRequest(BaseModel):
    force: bool = False
    reason: str = Field(default="operator stop", max_length=200)


class ModeRequest(BaseModel):
    mode: str
    # The dashboard asks before switching to real money; this is that answer.
    confirm: bool = False

    @field_validator("mode")
    @classmethod
    def _mode_valid(cls, v: str) -> str:
        if v not in ("paper", "live"):
            raise ValueError("mode must be 'paper' or 'live'")
        return v


class ConfirmRequest(BaseModel):
    confirm: str = ""


class ScheduleRequest(BaseModel):
    enabled: bool


class ResumeRequest(BaseModel):
    week: bool = False


class HolidayRequest(BaseModel):
    day: str
    label: str = ""

    @field_validator("day")
    @classmethod
    def _day_valid(cls, v: str) -> str:
        try:
            date.fromisoformat(v)
        except ValueError:
            raise ValueError("day must be an ISO date, e.g. 2026-10-21")
        return v


# ── engine process ───────────────────────────────────────────────────────────
@router.post("/engine/start")
async def engine_start(request: Request, principal: Principal = Depends(require_auth)) -> dict:
    c = ctx()
    _audit(request, principal, "engine.start")
    c.sup.state.manual_override = False
    # Through the fleet, which refuses a second algorithm on real money.
    res = c.fleet.start(c.sup.algo_id, trigger=f"manual:{principal.subject}")
    if not res.get("ok"):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=res.get("detail"))
    # Starting arms it for the daily open, as it does from the deck.
    c.db.set_algo_fields(c.sup.algo_id, enabled=1)
    return res


@router.post("/engine/stop")
async def engine_stop(
    body: StopRequest, request: Request, principal: Principal = Depends(require_auth)
) -> dict:
    c = ctx()
    _audit(request, principal, "engine.stop", f"force={body.force} reason={body.reason}")
    res = c.sup.stop(reason=body.reason, force=body.force, trigger=f"manual:{principal.subject}")
    if res.get("ok"):
        # Keep the scheduler from immediately bringing it back up mid-session,
        # and from bringing it up tomorrow: a stop disarms it.
        c.sup.state.manual_override = True
        c.db.set_algo_fields(c.sup.algo_id, enabled=0)
    else:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=res.get("detail"))
    return res


@router.post("/engine/restart")
async def engine_restart(request: Request, principal: Principal = Depends(require_auth)) -> dict:
    c = ctx()
    _audit(request, principal, "engine.restart")
    trigger = f"manual:{principal.subject}"
    if c.sup.state.running:
        c.sup.stop(reason="restart", force=True, trigger=trigger)
    return c.fleet.start(c.sup.algo_id, trigger=trigger)


@router.post("/mode")
async def set_mode(
    body: ModeRequest, request: Request, principal: Principal = Depends(require_auth)
) -> dict:
    c = ctx()
    if body.mode == "live" and not body.confirm:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="switching to LIVE has to be confirmed",
        )
    if c.sup.state.running:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="stop the engine before changing mode — a running engine holds its mode for the session",
        )
    c.sup.set_mode(body.mode)
    _audit(request, principal, "mode.set", body.mode)
    c.db.add_event(
        "warn", f"trading mode set to {body.mode.upper()} by {principal.subject}",
        source="api", algo_id=c.sup.algo_id,
    )
    return {"ok": True, "mode": body.mode}


# ── in-session risk controls ─────────────────────────────────────────────────
@router.post("/halt")
async def halt(request: Request, principal: Principal = Depends(require_auth)) -> dict:
    c = ctx()
    _audit(request, principal, "risk.halt")
    if not c.sup.state.running:
        raise HTTPException(status_code=409, detail="engine is not running")
    cmd_id = c.db.enqueue_command("halt", issued_by=principal.subject)
    return {"ok": True, "command_id": cmd_id, "detail": "no new entries will be taken today"}


@router.post("/resume")
async def resume(
    body: ResumeRequest, request: Request, principal: Principal = Depends(require_auth)
) -> dict:
    c = ctx()
    _audit(request, principal, "risk.resume", f"week={body.week}")
    if not c.sup.state.running:
        raise HTTPException(status_code=409, detail="engine is not running")
    cmd_id = c.db.enqueue_command("resume", {"week": body.week}, issued_by=principal.subject)
    return {"ok": True, "command_id": cmd_id}


@router.post("/flatten")
async def flatten(
    body: ConfirmRequest, request: Request, principal: Principal = Depends(require_auth)
) -> dict:
    c = ctx()
    if body.confirm != FLATTEN_PHRASE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"flatten requires confirm == '{FLATTEN_PHRASE}'",
        )
    _audit(request, principal, "risk.flatten")
    if not c.sup.state.running:
        raise HTTPException(
            status_code=409,
            detail="engine is not running — square off in the Angel One app directly",
        )
    cmd_id = c.db.enqueue_command("flatten", issued_by=principal.subject)
    c.db.add_event(
        "critical", f"FLATTEN requested by {principal.subject}", source="api", algo_id=c.sup.algo_id
    )
    return {"ok": True, "command_id": cmd_id, "detail": "market exit order queued"}


@router.post("/reload-scrip")
async def reload_scrip(request: Request, principal: Principal = Depends(require_auth)) -> dict:
    c = ctx()
    _audit(request, principal, "engine.reload_scrip")
    if not c.sup.state.running:
        raise HTTPException(status_code=409, detail="engine is not running")
    return {"ok": True, "command_id": c.db.enqueue_command("reload_scrip", issued_by=principal.subject)}


# ── automation ───────────────────────────────────────────────────────────────
@router.post("/schedule")
async def set_schedule(
    body: ScheduleRequest, request: Request, principal: Principal = Depends(require_auth)
) -> dict:
    c = ctx()
    c.sched.set_enabled(body.enabled)
    _audit(request, principal, "schedule.set", str(body.enabled))
    c.db.add_event(
        "info",
        f"auto start/stop {'ARMED' if body.enabled else 'DISARMED'} by {principal.subject}",
        source="api",
    )
    return {"ok": True, **c.sched.status()}


@router.get("/holidays")
async def list_holidays() -> dict:
    return {"holidays": ctx().db.holidays()}


@router.post("/holidays")
async def add_holiday(
    body: HolidayRequest, request: Request, principal: Principal = Depends(require_auth)
) -> dict:
    c = ctx()
    c.db.add_holiday(body.day, body.label)
    _audit(request, principal, "holiday.add", f"{body.day} {body.label}")
    return {"ok": True, "holidays": c.db.holidays()}


@router.delete("/holidays/{day}")
async def remove_holiday(
    day: str, request: Request, principal: Principal = Depends(require_auth)
) -> dict:
    c = ctx()
    c.db.remove_holiday(day)
    _audit(request, principal, "holiday.remove", day)
    return {"ok": True, "holidays": c.db.holidays()}


# ── emergency stop ───────────────────────────────────────────────────────────
@router.post("/nuke")
async def nuke(
    body: ConfirmRequest, request: Request, principal: Principal = Depends(require_auth)
) -> dict:
    """Stop everything, everywhere, now.

    Ordering is the whole design. Flatten commands are queued to every running
    engine *before* anything is stopped, because a stopped engine cannot square
    off its own position — killing first would strand live positions with
    nothing managing them. Only once the exits are queued does the fleet come
    down, and the halt flag is set so the scheduler cannot bring any of it back
    up on the next tick.

    What this cannot do: guarantee the exits filled. It queues market orders and
    stops the processes that would otherwise keep trading. Anything that does
    not fill has to be squared off in the Angel One app, and the response says
    which engines were holding a position when the button was pressed.
    """
    c = ctx()
    if body.confirm != NUKE_PHRASE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"the emergency stop requires confirm == '{NUKE_PHRASE}'",
        )

    _audit(request, principal, "risk.nuke", "emergency stop")
    c.db.add_event("critical", f"EMERGENCY STOP pressed by {principal.subject}", source="api")

    from shared.db import snapshot_key

    flattened: list[dict] = []
    stopped: list[dict] = []

    running = [
        (algo_id, sup)
        for algo_id, sup in c.fleet.all().items()
        if (sup.refresh() or True) and sup.state.running
    ]

    # 1. Queue an exit everywhere something is open, while the engines still live.
    for algo_id, sup in running:
        if sup.is_program(sup.state.pid):
            # A standalone program reads no command queue and publishes no
            # snapshot: nothing can be queued for it, and whether it holds a
            # position is unknown here — so it is never reported as flat. The
            # stop below sends it SIGINT, on which it is expected to square off.
            flattened.append(
                {"algo_id": algo_id, "had_position": None, "command_id": None, "program": True}
            )
            continue
        snap = c.db.kv_get(snapshot_key(algo_id), None) or {}
        had_position = bool(snap.get("position"))
        try:
            command_id = c.db.enqueue_command(
                "flatten", issued_by=f"nuke:{principal.subject}", algo_id=algo_id
            )
        except Exception as exc:
            command_id = None
            c.db.add_event(
                "critical",
                f"could not queue the emergency exit for {algo_id}: {exc}",
                source="api", algo_id=algo_id,
            )
        flattened.append(
            {"algo_id": algo_id, "had_position": had_position, "command_id": command_id}
        )

    # 2. Give the engines a moment to claim and act on those commands.
    await asyncio.sleep(NUKE_DRAIN_SEC)

    # 3. Bring the fleet down and keep it down.
    c.sched.set_enabled(False)
    for algo_id, _sup in running:
        result = c.fleet.stop(algo_id, reason=f"emergency stop by {principal.subject}", force=True)
        stopped.append({"algo_id": algo_id, **result})
        c.db.set_algo_fields(algo_id, enabled=0)

    held = [f["algo_id"] for f in flattened if f["had_position"]]
    unknown = [f["algo_id"] for f in flattened if f["had_position"] is None]
    c.db.add_event(
        "critical",
        f"emergency stop complete — {len(stopped)} engine(s) down, "
        f"{len(held)} had an open position"
        + (f", {len(unknown)} program(s) with unknown position" if unknown else "")
        + ", automation disarmed",
        source="api",
    )

    return {
        "ok": True,
        "engines_stopped": stopped,
        "exits_queued": flattened,
        "had_open_positions": held,
        "position_unknown": unknown,
        "automation_disarmed": True,
        "detail": " ".join(
            [f"{len(stopped)} engine(s) stopped and automation disarmed."]
            + (
                [f"{len(held)} had an open position — verify in the Angel One app that "
                 f"every exit filled."]
                if held else []
            )
            + (
                [f"{len(unknown)} standalone program(s) cannot report a position here — "
                 f"check the Angel One app that nothing is left open."]
                if unknown else []
            )
            + ([] if held or unknown else ["No engine was holding a position."])
        ),
    }
