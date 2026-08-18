"""Meridian Capital API.

The phone talks to exactly this. REST for state and history, one WebSocket
for the live feed, and a set of export endpoints that hand back the trading
record for any window the user asks for.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Optional

# The whole service reasons in exchange-local time.
os.environ.setdefault("TZ", os.getenv("TZ", "Asia/Kolkata"))
try:
    import time as _time
    _time.tzset()
except AttributeError:  # pragma: no cover - Windows
    pass

from fastapi import Depends, FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, Field

from . import algorithms
from . import auth as auth_mod
from . import db, exports, news, strategy_config, users
from .auth import (authorise_websocket, require_operator, require_super_admin,
                   require_token, require_token_query)
from .config import settings
from .holidays import expiry_state
from .runner import MAX_SLOTS, fleet, fleet_status, get_slot, supervisor
from .scheduler import (next_runs, reschedule, set_schedule_enabled,
                        shutdown_scheduler, start_scheduler)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
)
log = logging.getLogger("meridian")

def _find_web_dir() -> Path:
    """The dashboard is also a standalone Vercel project, so it lives at the
    repository root. In the container it is copied next to the backend."""
    here = Path(__file__).resolve()
    for candidate in (here.parent.parent / "web", here.parent.parent.parent / "web"):
        if (candidate / "index.html").exists():
            return candidate
    return here.parent.parent / "web"


WEB_DIR = _find_web_dir()


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init(settings.db_path)
    for lane in fleet:
        lane.bind_loop(asyncio.get_running_loop())
    log.info("Data directory: %s", settings.data_dir)
    log.info("Mode: %s", "PAPER" if settings.paper_mode else "LIVE — REAL MONEY")
    try:
        boot_user = users.bootstrap()
        if boot_user:
            log.info("Super admin ready: %r (%d user(s) total)",
                     boot_user, users.count())
    except Exception:
        log.exception("Could not bootstrap the super admin")

    if not settings.api_token and not auth_mod.login_configured():
        log.error("No account exists and API_TOKEN is unset — every "
                  "authenticated route will refuse. Set ADMIN_USER and "
                  "ADMIN_PASSWORD in .env and restart.")
    log.info("Active algorithm: %s", algorithms.active_description())
    if settings.missing_credentials():
        log.warning("Angel One credentials incomplete: %s",
                    ", ".join(settings.missing_credentials()))
    start_scheduler()
    try:
        yield
    finally:
        shutdown_scheduler()
        # Every slot gets the same graceful stop, so an open position in slot 3
        # is flattened on shutdown just like one in slot 1.
        for lane in fleet:
            if lane.running:
                lane.stop(reason="server shutdown")


app = FastAPI(
    title="Meridian Capital",
    description="Control plane for the NIFTY options algorithm",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],          # the token is the gate, not the origin
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================ models


class StartRequest(BaseModel):
    force: bool = Field(default=False, description="Start even on a weekend or holiday")
    slot: int = Field(default=0, ge=0, le=4)


class StopRequest(BaseModel):
    reason: str = Field(default="manual stop from app")
    slot: int = Field(default=0, ge=0, le=4)


class ScheduleRequest(BaseModel):
    enabled: Optional[bool] = None
    start: Optional[str] = Field(default=None, pattern=r"^\d{1,2}:\d{2}$")
    stop: Optional[str] = Field(default=None, pattern=r"^\d{1,2}:\d{2}$")


class PushRegisterRequest(BaseModel):
    token: str
    platform: str = ""


class StrategyUpdateRequest(BaseModel):
    values: dict[str, object] = Field(default_factory=dict)


class ProfileRequest(BaseModel):
    name: str


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class CreateUserRequest(BaseModel):
    username: str = Field(min_length=2, max_length=32)
    password: str = Field(min_length=8, max_length=256)
    role: str = Field(default="viewer")


class UpdateUserRequest(BaseModel):
    role: Optional[str] = None
    disabled: Optional[bool] = None
    password: Optional[str] = Field(default=None, min_length=8, max_length=256)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=8, max_length=256)


class AlgorithmUploadRequest(BaseModel):
    name: str = Field(default="Uploaded algorithm", max_length=80)
    source: str = Field(min_length=1)
    activate: bool = False
    slot: int = Field(default=0, ge=0, le=4)


class AlgorithmValidateRequest(BaseModel):
    source: str = Field(min_length=1)
    filename: str = Field(default="algorithm.py", max_length=120)


class ActivateRequest(BaseModel):
    # None empties the slot; only slots 1-4 may be emptied.
    version_id: Optional[str] = None
    slot: int = Field(default=0, ge=0, le=4)


# ============================================================ health


@app.get("/api/health")
async def health():
    """Unauthenticated liveness probe — no state leaks from here."""
    return {
        "ok": True,
        "service": "meridian-capital",
        "server_time": datetime.now().isoformat(timespec="seconds"),
        "timezone": settings.tz,
        "auth_configured": bool(settings.api_token) or auth_mod.login_configured(),
        "login_available": auth_mod.login_configured(),
    }


# ============================================================ auth


@app.post("/api/auth/login")
async def login(body: LoginRequest, request: Request):
    ip = request.client.host if request.client else "unknown"

    locked = auth_mod.check_rate_limit(ip)
    if locked is not None:
        raise HTTPException(
            status_code=429,
            detail=f"Too many attempts. Try again in {max(1, locked)} seconds.",
        )

    if not auth_mod.login_configured():
        raise HTTPException(
            status_code=503,
            detail="No password is configured on the server. Set ADMIN_PASSWORD in .env.",
        )

    session = auth_mod.attempt_login(body.username, body.password)
    if session is None:
        auth_mod.record_failure(ip)
        log.warning("failed login for %r from %s", body.username[:32], ip)
        raise HTTPException(status_code=401,
                            detail="Incorrect operator name or passcode")

    auth_mod.clear_failures(ip)
    log.info("login succeeded for %s from %s", session["user"], ip)
    return session


@app.get("/api/auth/me")
async def whoami(token: str = Depends(require_token)):
    payload = auth_mod.verify_session(token)
    role = auth_mod.role_of(token)
    return {
        "user": payload["sub"] if payload else "api-token",
        "role": role,
        "role_label": users.ROLE_LABEL.get(role, role),
        "can_manage_users": users.has_at_least(role, "super_admin"),
        "can_change_algorithm": users.has_at_least(role, "super_admin"),
        "can_operate": users.has_at_least(role, "operator"),
        "expires_at": payload.get("exp") if payload else None,
        "kind": "session" if payload else "api-token",
    }


@app.post("/api/auth/change-password")
async def change_own_password(body: ChangePasswordRequest,
                              token: str = Depends(require_token)):
    """Change your own password — needs the current one, even for a super admin."""
    payload = auth_mod.verify_session(token)
    if not payload:
        raise HTTPException(status_code=400,
                            detail="Only a signed-in user can change a password.")
    username = payload["sub"]
    if auth_mod.attempt_login(username, body.current_password) is None:
        raise HTTPException(status_code=401, detail="Current password is incorrect.")
    try:
        users.set_password(username, body.new_password)
    except users.UserError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    log.info("password changed for %s", username)
    return {"ok": True}


# ============================================================ users


@app.get("/api/users")
async def list_users(_: str = Depends(require_super_admin)):
    return {
        "users": users.list_all(),
        "roles": [{"value": r, "label": users.ROLE_LABEL[r]} for r in users.ROLES],
    }


@app.post("/api/users")
async def create_user(body: CreateUserRequest, token: str = Depends(require_super_admin)):
    payload = auth_mod.verify_session(token)
    actor = payload["sub"] if payload else "api-token"
    try:
        created = users.create(body.username, body.password, body.role,  # type: ignore[arg-type]
                               created_by=actor, must_change=True)
    except users.UserError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    supervisor._emit_local(
        "users", f"{actor} created user {created['username']!r} as {created['role']}",
        level="warn")
    return created


@app.patch("/api/users/{user_id}")
async def update_user(user_id: int, body: UpdateUserRequest,
                      token: str = Depends(require_super_admin)):
    payload = auth_mod.verify_session(token)
    actor = payload["sub"] if payload else "api-token"
    target = users.get_by_id(user_id)
    if not target:
        raise HTTPException(status_code=404, detail="No such user.")

    try:
        if body.role is not None:
            users.set_role(user_id, body.role)          # type: ignore[arg-type]
        if body.disabled is not None:
            users.set_disabled(user_id, body.disabled)
        if body.password is not None:
            users.set_password(target["username"], body.password)
    except users.UserError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    supervisor._emit_local("users", f"{actor} updated user {target['username']!r}",
                           level="warn")
    return users.get_by_id(user_id) and users._row_to_public(users.get_by_id(user_id))


@app.delete("/api/users/{user_id}")
async def delete_user(user_id: int, token: str = Depends(require_super_admin)):
    payload = auth_mod.verify_session(token)
    actor = payload["sub"] if payload else "api-token"
    target = users.get_by_id(user_id)
    if not target:
        raise HTTPException(status_code=404, detail="No such user.")
    if payload and target["username"].lower() == payload["sub"].lower():
        raise HTTPException(status_code=400,
                            detail="You cannot delete the account you are signed in with.")
    try:
        users.delete(user_id)
    except users.UserError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    supervisor._emit_local("users", f"{actor} deleted user {target['username']!r}",
                           level="warn")
    return {"ok": True}


# ============================================================ algorithm


@app.get("/api/algorithm")
async def algorithm_list(_: str = Depends(require_token)):
    return {
        **algorithms.list_versions(MAX_SLOTS),
        "active_description": algorithms.active_description(),
        "bot_running": supervisor.running,
        "running_slots": [s.slot for s in fleet if s.running],
        "applies_at": "next start",
    }


@app.get("/api/algorithm/template")
async def algorithm_template(_: str = Depends(require_token)):
    """A working skeleton that already speaks the dashboard's event protocol."""
    return {"filename": "my_algorithm.py", "source": algorithms.template()}


@app.post("/api/algorithm/validate")
async def algorithm_validate(body: AlgorithmValidateRequest,
                             _: str = Depends(require_super_admin)):
    """Check an algorithm without storing it — a dry run of the upload."""
    return await asyncio.to_thread(algorithms.validate, body.source, body.filename)


@app.post("/api/algorithm/upload")
async def algorithm_upload(body: AlgorithmUploadRequest,
                           token: str = Depends(require_super_admin)):
    payload = auth_mod.verify_session(token)
    actor = payload["sub"] if payload else "api-token"

    report = await asyncio.to_thread(algorithms.validate, body.source, body.name)
    if not report["ok"]:
        return JSONResponse(status_code=422,
                            content={"stored": False, "report": report})

    entry = algorithms.save_version(body.source, body.name, actor, report)
    supervisor._emit_local(
        "algorithm", f"{actor} uploaded algorithm {entry['name']!r} ({entry['id']})",
        level="warn", version=entry["id"])

    activated = False
    if body.activate:
        algorithms.activate(entry["id"], slot=body.slot)
        activated = True
        supervisor._emit_local(
            "algorithm",
            f"{entry['name']!r} is now active"
            + (" — applies when the bot next starts" if supervisor.running else ""),
            level="warn")

    return {"stored": True, "activated": activated, "version": entry, "report": report,
            **algorithms.list_versions(MAX_SLOTS)}


@app.post("/api/algorithm/activate")
async def algorithm_activate(body: ActivateRequest,
                             token: str = Depends(require_super_admin)):
    payload = auth_mod.verify_session(token)
    actor = payload["sub"] if payload else "api-token"
    try:
        result = algorithms.activate(body.version_id, slot=body.slot)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    lane = get_slot(body.slot)
    lane._emit_local(
        "algorithm",
        f"{actor} assigned {body.version_id or 'nothing'} to {lane.name}"
        + (" — applies when it next starts" if lane.running else ""),
        level="warn")
    return {**result, "active_description": algorithms.active_description(body.slot)}


@app.delete("/api/algorithm/{version_id}")
async def algorithm_delete(version_id: str, _: str = Depends(require_super_admin)):
    try:
        return algorithms.delete_version(version_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/algorithm/{version_id}/source")
async def algorithm_source(version_id: str, _: str = Depends(require_super_admin)):
    source = algorithms.get_source(version_id)
    if source is None:
        raise HTTPException(status_code=404, detail="No such version.")
    return {"version_id": version_id, "source": source}


@app.post("/api/auth/logout-everywhere")
async def logout_everywhere(_: str = Depends(require_token)):
    """Invalidate every session token issued so far — for a lost device."""
    epoch = auth_mod.bump_epoch()
    log.warning("all sessions invalidated (epoch %s)", epoch)
    return {"ok": True, "epoch": epoch}


# ============================================================ bot control


@app.get("/api/status")
async def status(_: str = Depends(require_token)):
    return {
        **supervisor.status(),
        "schedule_next": next_runs(),
        "config": settings.public_dict(),
    }


def _diagnostics() -> dict:
    """Everything needed to answer "is the algorithm actually alive and well?".

    The supervisor knows whether a process exists, but a process that is up and
    silently failing every broker call looks identical to a healthy one from the
    outside. So this also reports how long it has been since the child last said
    anything, and what it has been complaining about — an algorithm that stopped
    emitting is the failure mode that would otherwise go unnoticed until the P&L
    came out wrong.
    """
    st = supervisor.status()
    now = datetime.now()
    today = db.today_str()

    def age(iso: Optional[str]) -> Optional[int]:
        if not iso:
            return None
        try:
            return max(0, int((now - datetime.fromisoformat(iso)).total_seconds()))
        except (ValueError, TypeError):
            return None

    snap = st.get("snapshot") or {}
    last_event = st.get("last_event") or {}
    event_age = age(last_event.get("ts"))
    tick_age = age(snap.get("_ts"))

    # Only things that actually went wrong. A warning is shown but does not
    # count as an error, and routine status never reaches here at all.
    raw_faults = db.query(
        """SELECT ts, slot, kind, level, message FROM events
           WHERE session_date = ?
             AND (level IN ('error','critical','warn') OR kind IN ('fatal','error'))
           ORDER BY ts DESC, id DESC LIMIT 200""",
        (today,),
    )

    # The same broker error repeating 90 times is one problem, not ninety, and
    # listing each copy pushes the other faults off the panel.
    faults: list[dict] = []
    seen: dict[str, dict] = {}
    for row in raw_faults:
        key = (row["message"] or row["kind"] or "")[:120]
        if key in seen:
            entry = seen[key]
            entry["count"] += 1
            entry["first_ts"] = row["ts"]         # rows arrive newest first
            continue
        entry = {**row, "count": 1, "first_ts": row["ts"]}
        seen[key] = entry
        faults.append(entry)
        if len(faults) >= 12:
            break

    counts = db.query(
        """SELECT level, COUNT(*) AS n FROM events
           WHERE session_date = ? GROUP BY level""",
        (today,),
    )
    by_level = {r["level"]: r["n"] for r in counts}
    errors_today = by_level.get("error", 0) + by_level.get("critical", 0)
    events_today = sum(by_level.values())

    # A running child that has gone quiet for longer than two status intervals is
    # not obviously healthy, and saying so is the whole point of this endpoint.
    if not st.get("running"):
        heartbeat = "stopped"
    elif event_age is None:
        heartbeat = "unknown"
    elif event_age > 120:
        heartbeat = "stale"
    else:
        heartbeat = "live"

    ok = (
        st.get("state") != "error"
        and not st.get("last_error")
        and heartbeat in ("live", "stopped")
        and not errors_today
    )

    return {
        "ok": ok,
        "heartbeat": heartbeat,
        "state": st.get("state"),
        "running": st.get("running"),
        "pid": st.get("pid"),
        "uptime_seconds": st.get("uptime_seconds"),
        "restarts": st.get("restarts"),
        "last_error": st.get("last_error"),
        "stop_reason": st.get("stop_reason"),
        "last_event_age": event_age,
        "last_event_kind": last_event.get("kind"),
        "last_tick_age": tick_age,
        "has_market_data": bool((snap.get("market") or {}).get("spot")),
        "paper": snap.get("paper", settings.paper_mode),
        "is_trading_day": st.get("is_trading_day"),
        "not_trading_reason": st.get("not_trading_reason"),
        # Computed here rather than read from the algorithm, so it is on screen
        # whether or not anything is running.
        "expiry": expiry_state(now.date(), settings.skip_holidays),
        "inside_window": st.get("inside_window"),
        "schedule": st.get("schedule"),
        "schedule_next": next_runs(),
        "errors_today": errors_today,
        "warnings_today": by_level.get("warn", 0),
        # Everything the bot said, so the deck can show activity without
        # implying that activity is failure.
        "events_today": events_today,
        "faults": faults,
        "server_time": now.isoformat(timespec="seconds"),
    }


@app.get("/api/diagnostics")
async def diagnostics(_: str = Depends(require_token)):
    return await asyncio.to_thread(_diagnostics)


@app.get("/api/news")
async def market_news(_: str = Depends(require_token)):
    """Cached market headlines. Never allowed to fail the request."""
    try:
        data = await asyncio.to_thread(news.headlines)
    except Exception as exc:                          # noqa: BLE001
        log.debug("news unavailable: %s", exc)
        return {"items": [], "sources": [], "error": "News is unavailable."}
    return {
        "items": data.get("items", []),
        "sources": data.get("sources", []),
        "age_seconds": news.age_seconds(),
        "stale": data.get("stale", False),
        "error": data.get("error"),
    }


@app.get("/api/fleet")
async def fleet_state(_: str = Depends(require_token)):
    """Every slot at a glance — what the deck lays itself out from."""
    state = await asyncio.to_thread(fleet_status)
    assignments = {s["slot"]: s for s in algorithms.list_versions(MAX_SLOTS)["slots"]}
    for lane in state["slots"]:
        a = assignments.get(lane["slot"], {})
        lane["algorithm"] = a.get("description", "Empty — no algorithm assigned")
        lane["empty"] = a.get("empty", lane["slot"] != 0)
    return state


@app.post("/api/bot/start")
async def bot_start(body: StartRequest, _: str = Depends(require_operator)):
    lane = get_slot(body.slot)
    result = await asyncio.to_thread(lane.start, "manual", body.force)
    if not result.get("ok"):
        return JSONResponse(status_code=409, content=result)
    return result


@app.post("/api/bot/stop")
async def bot_stop(body: StopRequest, _: str = Depends(require_operator)):
    lane = get_slot(body.slot)
    result = await asyncio.to_thread(lane.stop, body.reason)
    if not result.get("ok"):
        return JSONResponse(status_code=409, content=result)
    return result


@app.post("/api/bot/restart")
async def bot_restart(_: str = Depends(require_operator)):
    return await asyncio.to_thread(supervisor.restart, "manual restart from app")


@app.get("/api/schedule")
async def schedule_get(_: str = Depends(require_token)):
    return {
        "start": settings.session_start.strftime("%H:%M"),
        "stop": settings.session_stop.strftime("%H:%M"),
        "timezone": settings.tz,
        "auto": settings.auto_schedule,
        "skip_holidays": settings.skip_holidays,
        **next_runs(),
    }


@app.post("/api/schedule")
async def schedule_set(body: ScheduleRequest, _: str = Depends(require_operator)):
    if body.start and body.stop:
        try:
            reschedule(body.start, body.stop)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    elif body.start or body.stop:
        raise HTTPException(status_code=400, detail="send both start and stop, or neither")
    if body.enabled is not None:
        set_schedule_enabled(body.enabled)
    return await schedule_get(_)


# ============================================================ live feed


@app.get("/api/events")
async def events(
    since_id: int = Query(default=0, ge=0),
    limit: int = Query(default=200, ge=1, le=2000),
    session_date: Optional[str] = None,
    kinds: Optional[str] = Query(default=None, description="comma-separated event kinds"),
    _: str = Depends(require_token),
):
    kind_list = [k.strip() for k in kinds.split(",") if k.strip()] if kinds else None
    rows = db.recent_events(limit=limit, since_id=since_id,
                            session_date=session_date, kinds=kind_list)
    return {"events": rows, "last_id": rows[-1]["id"] if rows else since_id}


@app.get("/api/today")
async def today(_: str = Depends(require_token)):
    d = db.today_str()
    return {
        "date": d,
        "snapshot": supervisor.snapshot,
        "status": supervisor.status(),
        "trades": db.trades_between(d, d),
        "session": db.query_one("SELECT * FROM sessions WHERE session_date = ?", (d,)),
        "equity_marks": db.equity_marks(d),
        "tail": supervisor.tail[-120:],
    }


@app.websocket("/ws")
async def websocket_feed(ws: WebSocket):
    """Live push: every log line and structured event as it happens."""
    await ws.accept()
    if not await authorise_websocket(ws):
        return

    # One queue per slot: the feed is the whole fleet, and each event already
    # carries the slot that produced it so the dashboard can split them again.
    queues = [lane.subscribe() for lane in fleet]
    merged = sorted(
        (e for lane in fleet for e in lane.tail[-80:]),
        key=lambda e: e.get("ts") or "",
    )[-120:]
    try:
        await ws.send_text(json.dumps({
            "kind": "hello",
            "status": supervisor.status(),
            "fleet": fleet_status(),
            "tail": merged,
        }, default=str))

        async def pump_one(q):
            while True:
                event = await q.get()
                await ws.send_text(json.dumps(event, default=str))

        async def pump():
            await asyncio.gather(*(pump_one(q) for q in queues))

        async def heartbeat():
            # Keeps mobile networks and proxies from dropping an idle socket.
            while True:
                await asyncio.sleep(25)
                await ws.send_text(json.dumps({"kind": "ping"}))

        pump_task = asyncio.create_task(pump())
        beat_task = asyncio.create_task(heartbeat())
        done, pending = await asyncio.wait(
            {pump_task, beat_task}, return_when=asyncio.FIRST_EXCEPTION)
        for task in pending:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        for task in done:
            exc = task.exception()
            if exc and not isinstance(exc, WebSocketDisconnect):
                raise exc
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        log.debug("websocket closed: %s", exc)
    finally:
        for lane, q in zip(fleet, queues):
            lane.unsubscribe(q)


# ============================================================ strategy


@app.get("/api/strategy")
async def strategy_get(_: str = Depends(require_token)):
    return {
        **strategy_config.describe(),
        "applies_at": "next start",
        "bot_running": supervisor.running,
    }


@app.put("/api/strategy")
async def strategy_put(body: StrategyUpdateRequest, _: str = Depends(require_operator)):
    """Edit strategy parameters.

    Accepted while the bot is running, but deliberately not applied until it
    next starts — an open position must finish under the rules it was opened
    with.
    """
    try:
        strategy_config.apply(body.values)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    supervisor._emit_local(
        "strategy",
        f"Strategy updated — {len(body.values)} parameter(s) changed"
        + (" (applies when the bot next starts)" if supervisor.running else ""),
        level="warn",
    )
    return await strategy_get(_)


@app.post("/api/strategy/reset")
async def strategy_reset(_: str = Depends(require_operator)):
    strategy_config.reset()
    supervisor._emit_local("strategy", "Strategy reset to the v11 baseline", level="warn")
    return await strategy_get(_)


@app.post("/api/strategy/profiles")
async def profile_save(body: ProfileRequest, _: str = Depends(require_operator)):
    try:
        strategy_config.save_profile(body.name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return await strategy_get(_)


@app.post("/api/strategy/profiles/load")
async def profile_load(body: ProfileRequest, _: str = Depends(require_operator)):
    try:
        strategy_config.load_profile(body.name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    supervisor._emit_local("strategy", f"Loaded strategy profile {body.name!r}", level="warn")
    return await strategy_get(_)


@app.delete("/api/strategy/profiles")
async def profile_delete(name: str, _: str = Depends(require_operator)):
    strategy_config.delete_profile(name)
    return await strategy_get(_)


# ============================================================ chart


@app.get("/api/chart")
async def chart(_: str = Depends(require_token)):
    """Latest candles, indicator overlays, and the open contract's premium.

    Produced by the bot from the candles it already fetches, so it is only
    populated while the bot is running.
    """
    if not supervisor.chart:
        return {
            "available": False,
            "reason": "The bot is not running — candles come from its market feed."
            if not supervisor.running
            else "Waiting for the first candle push.",
            "candles": [], "option": None,
        }
    return {"available": True, **supervisor.chart}


# ============================================================ history


@app.get("/api/trades")
async def trades(
    start: Optional[str] = None,
    end: Optional[str] = None,
    period: str = "month",
    anchor: Optional[str] = None,
    slot: Optional[int] = Query(default=None, ge=0, le=4),
    _: str = Depends(require_token),
):
    s, e, label = _range(period, anchor, start, end)
    rows = db.trades_between(s, e, slot=slot)
    return {"range": {"start": s, "end": e, "label": label},
            "slot": slot, "count": len(rows), "trades": rows}


@app.get("/api/sessions")
async def sessions(
    start: Optional[str] = None,
    end: Optional[str] = None,
    period: str = "month",
    anchor: Optional[str] = None,
    slot: Optional[int] = Query(default=None, ge=0, le=4),
    _: str = Depends(require_token),
):
    s, e, label = _range(period, anchor, start, end)
    rows = db.sessions_between(s, e, slot=slot)
    return {"range": {"start": s, "end": e, "label": label},
            "count": len(rows), "sessions": rows}


@app.get("/api/summary")
async def summary(
    period: str = "month",
    anchor: Optional[str] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
    slot: Optional[int] = Query(default=None, ge=0, le=4),
    _: str = Depends(require_token),
):
    s, e, label = _range(period, anchor, start, end)
    return {
        "range": {"start": s, "end": e, "label": label},
        "slot": slot,
        "summary": db.aggregate(s, e, slot=slot),
        "curve": db.equity_curve(s, e),
        "sessions": db.sessions_between(s, e, slot=slot),
        # Per-slot breakdown so the report can attribute P&L to each algorithm
        # rather than only showing the combined number.
        "by_slot": [
            {"slot": i, **db.aggregate(s, e, slot=i)} for i in range(MAX_SLOTS)
        ],
    }


@app.get("/api/equity/intraday")
async def equity_intraday(
    session_date: Optional[str] = None,
    slot: Optional[int] = Query(default=None, ge=0, le=4),
    _: str = Depends(require_token),
):
    d = session_date or db.today_str()
    return {"date": d, "slot": slot, "marks": db.equity_marks(d, slot=slot)}


@app.get("/api/runs")
async def runs(limit: int = Query(default=30, ge=1, le=200), _: str = Depends(require_token)):
    return {"runs": db.recent_runs(limit)}


# ============================================================ exports


@app.get("/api/export")
async def export(
    period: str = Query(default="day",
                        description="day | week | month | quarter | year | all | custom"),
    anchor: Optional[str] = Query(default=None, description="any date inside the window"),
    start: Optional[str] = None,
    end: Optional[str] = None,
    format: str = Query(default="csv", pattern="^(csv|json|xlsx|pdf)$"),
    events: bool = Query(default=False, description="include the full event log"),
    slot: Optional[int] = Query(default=None, ge=0, le=4,
                                description="one algorithm, or every one if omitted"),
    _: str = Depends(require_token_query),
):
    s, e, label = _range(period, anchor, start, end)
    payload = exports.build_payload(s, e, label, include_events=events, slot=slot)

    if format == "json":
        body = exports.to_json(payload).encode()
        media = "application/json"
    elif format == "xlsx":
        body = exports.to_xlsx(payload)
        media = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    elif format == "pdf":
        body = await asyncio.to_thread(exports.to_pdf, payload)
        media = "application/pdf"
    else:
        body = exports.to_csv(payload).encode()
        media = "text/csv"

    name = exports.filename(period, s, e, format)
    return Response(
        content=body,
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@app.get("/api/export/preview")
async def export_preview(
    period: str = "day",
    anchor: Optional[str] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
    slot: Optional[int] = Query(default=None, ge=0, le=4),
    _: str = Depends(require_token),
):
    """What the download will contain, so the app can show it before saving."""
    s, e, label = _range(period, anchor, start, end)
    return {
        "range": {"start": s, "end": e, "label": label},
        "slot": slot,
        "summary": db.aggregate(s, e, slot=slot),
        "sessions": len(db.sessions_between(s, e, slot=slot)),
        "trades": len(db.trades_between(s, e, slot=slot)),
        "formats": ["csv", "pdf", "xlsx", "json"],
    }


# ============================================================ push


@app.post("/api/push/register")
async def push_register(body: PushRegisterRequest, _: str = Depends(require_token)):
    db.add_push_token(body.token, body.platform)
    return {"ok": True, "registered": body.token[:24] + "..."}


@app.delete("/api/push/register")
async def push_unregister(token: str, _: str = Depends(require_token)):
    db.remove_push_token(token)
    return {"ok": True}


@app.post("/api/push/test")
async def push_test(_: str = Depends(require_token)):
    from .push import send
    await asyncio.to_thread(
        send, "Meridian Capital", "Push notifications are wired up correctly.",
        {"screen": "dashboard"})
    return {"ok": True, "tokens": len(db.list_push_tokens())}


# ============================================================ web app


@app.get("/", include_in_schema=False)
async def index():
    page = WEB_DIR / "index.html"
    if page.exists():
        return FileResponse(page, media_type="text/html")
    return HTMLResponse("<h1>Meridian Capital</h1><p>API is up. See /api/docs</p>")


@app.get("/manifest.webmanifest", include_in_schema=False)
async def manifest():
    path = WEB_DIR / "manifest.webmanifest"
    if path.exists():
        return FileResponse(path, media_type="application/manifest+json")
    return JSONResponse({
        "name": "Meridian Capital",
        "short_name": "Meridian",
        "start_url": "/",
        "display": "standalone",
        "background_color": "#05070d",
        "theme_color": "#05070d",
        "icons": [{
            "src": "/icon.svg", "sizes": "any", "type": "image/svg+xml",
            "purpose": "any maskable",
        }],
    })


@app.get("/icon.svg", include_in_schema=False)
async def icon():
    path = WEB_DIR / "icon.svg"
    if path.exists():
        return FileResponse(path, media_type="image/svg+xml")
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">'
        '<rect width="512" height="512" rx="112" fill="#05070d"/>'
        '<path d="M112 352V160l72 96 72-96 72 96 72-96v192" fill="none" '
        'stroke="#35d6a0" stroke-width="30" stroke-linejoin="round" '
        'stroke-linecap="round"/></svg>'
    )
    return Response(content=svg, media_type="image/svg+xml")


# ============================================================ helpers


def _range(period: str, anchor: Optional[str],
           start: Optional[str], end: Optional[str]) -> tuple[str, str, str]:
    if start and end and period not in ("custom",):
        period = "custom"
    try:
        return exports.resolve_range(period, anchor, start, end)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    log.exception("unhandled error on %s", request.url.path)
    return JSONResponse(status_code=500,
                        content={"detail": f"{type(exc).__name__}: {exc}"})


def run() -> None:
    import uvicorn
    uvicorn.run("app.main:app", host=settings.host, port=settings.port,
                log_level="info", ws_ping_interval=20, ws_ping_timeout=20)


if __name__ == "__main__":
    run()
