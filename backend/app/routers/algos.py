"""Algorithm registry: upload, screen, and per-algorithm control.

Uploaded source is put through the acceptance gate and the verdict is recorded
against the version, but the operator decides what runs and whether it runs on
paper or real money. The choice is made when an algorithm is started.
"""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from app.algo_store import MAX_SOURCE_BYTES, run_gate, sha256, slugify
from app.program_status import compact_snapshot
from app.programs import mode_arguments, runtime_of, runtime_of_version
from app.deps import ctx
from app import code_redact, shadow
from app.security import Principal, client_ip, require_auth
from engine import versions
from engine.clock import now_ist
from engine.gate import check_catalogue
from shared.db import DEFAULT_ALGO, SYSTEM_ALGO, snapshot_key

def _not_retired(request: Request) -> None:
    """The retired built-in answers like an algorithm that does not exist, on every route."""
    if request.path_params.get("algo_id") == DEFAULT_ALGO and ctx().db.builtin_retired():
        raise HTTPException(status_code=404, detail="no such algorithm — the built-in has been removed")


router = APIRouter(prefix="/api/algos", tags=["algos"], dependencies=[Depends(require_auth), Depends(_not_retired)])

SHADOW_SUFFIX = "-shadow"
# The lines a card shows while a run has not reported yet, or after it stopped.
RECENT_LINES = 6
RECENT_SCAN = 40


class UploadRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    source: str = Field(min_length=1)
    notes: str = Field(default="", max_length=500)
    algo_id: str | None = None
    mode: str = "paper"


class ModeRequest(BaseModel):
    mode: str


class StartRequest(BaseModel):
    """The mode the operator picked in the run dialog.

    Omitted means "whatever it was set to last", which is what the scheduler
    sends when it starts an algorithm at the open.
    """

    mode: str | None = None


class ActivateRequest(BaseModel):
    version_id: int


class ShadowRequest(BaseModel):
    enabled: bool = True


def _audit(request: Request, principal: Principal, action: str, detail: str = "") -> None:
    ctx().db.audit(principal.subject, action, detail, client_ip(request))


def _recent(db, algo_id: str, sup: dict) -> list[dict]:
    """Today's last few lines from this algorithm and its supervisor, oldest first.

    A program says what it is doing only by printing: logging in, reading the
    balance, loading contracts — or why it gave up. Until its first status the
    card had nothing to show but the last run's figures, so a start that
    failed looked like a start that never happened. While it runs, only this
    run's lines count.
    """
    now = now_ist()
    since = now.date().isoformat()
    if sup.get("running") and sup.get("uptime_sec") is not None:
        started = now - timedelta(seconds=float(sup["uptime_sec"]) + 2.0)
        since = max(since, started.isoformat(timespec="seconds"))
    rows = [r for r in db.events(limit=RECENT_SCAN, algo_id=algo_id) if str(r.get("ts") or "") >= since]
    return [
        {"ts": r["ts"], "level": r.get("level") or "info", "source": r.get("source") or "",
         "message": str(r.get("message") or "")[:300]}
        for r in reversed(rows[:RECENT_LINES])
    ]


def _algo_view(db, algo: dict) -> dict:
    history = db.versions(algo["id"], limit=20)
    # Metadata only: the source is never shown here, and what is derived from it
    # (which runtime it needs) is remembered by its hash.
    active = db.version_meta(algo["active_version"]) if algo.get("active_version") else None
    sup = ctx().fleet.snapshot(algo["id"]) if hasattr(ctx(), "fleet") else {}
    # The card keeps showing the algorithm's last recorded figures once it stops.
    last = compact_snapshot(db.kv_get(snapshot_key(algo["id"]), None))
    return {
        **algo,
        "enabled": bool(algo.get("enabled")),
        "versions": [_safe_version(v) for v in history],
        "active": {k: v for k, v in (active or {}).items() if k != "source"} or None,
        "gate": versions.summary(active),
        "runtime_kind": (
            "builtin" if algo.get("kind") == "builtin"
            else runtime_of_version(db, active) if active else "none"
        ),
        "runtime": sup,
        "last": last,
        "recent": _recent(db, algo["id"], sup),
        "shadow_of": algo.get("shadow_of"),
    }


@router.get("")
async def list_algos() -> dict:
    db = ctx().db
    return {"algos": [_algo_view(db, a) for a in db.algos()]}


@router.get("/gate-catalogue")
async def gate_catalogue() -> dict:
    """What the gate looks at. Advisory — a failure does not block an upload."""
    return {"checks": check_catalogue(), "advisory": True}


@router.post("")
async def upload_algo(
    body: UploadRequest, request: Request, principal: Principal = Depends(require_auth)
) -> dict:
    """Accept source, run the acceptance gate, and record what it found.

    The gate runs synchronously because the operator is waiting to see the
    report, but it is advisory: a failing check is shown, not enforced. What
    runs, and on paper or real money, is the operator's call.
    """
    db = ctx().db
    if len(body.source.encode("utf-8")) > MAX_SOURCE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"source exceeds {MAX_SOURCE_BYTES // 1024} KiB",
        )

    algo_id = (body.algo_id or slugify(body.name)).strip()
    if not algo_id:
        raise HTTPException(status_code=400, detail="could not derive an id from that name")
    if algo_id == SYSTEM_ALGO:
        # The journal files desk-wide events under this id.
        raise HTTPException(status_code=400, detail="'system' is reserved; pick another name")

    existing = db.algo(algo_id)
    if existing and existing.get("kind") == "builtin":
        raise HTTPException(
            status_code=409,
            detail="the built-in build cannot be overwritten; upload under a different name",
        )

    db.upsert_algo(algo_id, body.name.strip(), kind="uploaded", notes=body.notes)
    version_id = db.add_version(algo_id, body.source, sha256(body.source), principal.subject)
    version = db.version(version_id)

    kind = runtime_of(body.source)
    if kind == "program":
        # The gate tests strategy modules; a complete program is run as it is,
        # so there is nothing for it to check. Say what it is instead.
        switch = mode_arguments(body.source)
        if switch is None:
            note = ("standalone program with no paper/live switch — it will not be started until "
                    "it accepts --paper/--live or reads MERIDIAN_TRADING_MODE")
        else:
            how = "--paper / --live" if switch["paper"] else "MERIDIAN_TRADING_MODE"
            note = f"standalone program — runs as its own process, told its mode with {how}"
        report = {
            "passed": True, "program": True, "total": 0, "failed": 0, "checks": [],
            "error": None, "note": note,
        }
        passed = True
        db.set_version_status(version_id, versions.STATUS_PROGRAM, report)
    else:
        report = await asyncio.to_thread(run_gate, body.source)
        passed = bool(report.get("passed"))
        db.set_version_status(
            version_id, versions.STATUS_PASSED if passed else versions.STATUS_FAILED, report
        )

    _audit(
        request, principal, "algo.upload",
        f"{algo_id} v{version['version']} sha={version['sha256'][:12]} gate={'pass' if passed else 'fail'}",
    )
    db.add_event(
        "warn" if not passed else "info",
        f"algorithm '{body.name}' v{version['version']} uploaded by {principal.subject} — "
        + ("standalone program, runs as written (no gate)" if kind == "program"
           else f"acceptance gate {'PASSED' if passed else 'FAILED'}"),
        source="api", algo_id=algo_id,
    )

    # Every upload becomes the active version, whatever the gate thought of it.
    # Nothing starts running on its own: the operator picks a mode and starts it.
    mode = "live" if body.mode == "live" else "paper"
    db.set_algo_fields(algo_id, active_version=version_id, mode=mode, enabled=0)

    return {
        "ok": True,
        "algo_id": algo_id,
        "version_id": version_id,
        "version": version["version"],
        "passed": passed,
        "report": code_redact.scrub_strings(report),
        "mode": mode,
        "kind": kind,
        "next": (
            "standalone program — it runs exactly as written, as its own process"
            if kind == "program"
            else "ready to run — start it when you want it trading"
            if passed
            else "ready to run, but the gate flagged the checks below — worth a look first"
        ),
    }


@router.get("/{algo_id}")
async def get_algo(algo_id: str) -> dict:
    db = ctx().db
    algo = db.algo(algo_id)
    if not algo:
        raise HTTPException(status_code=404, detail="no such algorithm")
    return _algo_view(db, algo)


def _safe_version(v: dict) -> dict:
    """A version row fit for the browser: never its source (it carries the operator's Angel
    credentials), and its gate report with anything shaped like a secret blanked."""
    out = {k: val for k, val in v.items() if k != "source"}
    if out.get("gate_report"):
        try:
            out["gate_report"] = json.dumps(code_redact.scrub_strings(json.loads(out["gate_report"])))
        except (TypeError, ValueError):
            out["gate_report"] = None
    return out


@router.get("/{algo_id}/versions/{version_id}")
async def get_version(algo_id: str, version_id: int) -> dict:
    v = ctx().db.version(version_id)
    if not v or v["algo_id"] != algo_id:
        raise HTTPException(status_code=404, detail="no such version")
    return _safe_version(v)


@router.get("/{algo_id}/diff")
async def diff_versions(algo_id: str, a: int, b: int, context: int = Query(3, ge=0, le=100_000)) -> dict:
    """What changed between two uploaded versions, with every credential line blanked on
    the server before the diff is taken: no line of either file leaves unredacted."""
    db = ctx().db
    va, vb = db.version(a), db.version(b)
    if not va or not vb or va["algo_id"] != algo_id or vb["algo_id"] != algo_id:
        raise HTTPException(status_code=404, detail="no such version")
    out = await asyncio.to_thread(code_redact.diff, va.get("source") or "", vb.get("source") or "", context)
    meta = ("id", "version", "created_ts", "uploaded_by", "sha256", "status")
    return {"algo_id": algo_id, "a": {k: va.get(k) for k in meta}, "b": {k: vb.get(k) for k in meta}, **out}


@router.post("/{algo_id}/activate")
async def activate_version(
    algo_id: str, body: ActivateRequest, request: Request,
    principal: Principal = Depends(require_auth),
) -> dict:
    db = ctx().db
    v = db.version(body.version_id)
    if not v or v["algo_id"] != algo_id:
        raise HTTPException(status_code=404, detail="no such version")

    if ctx().fleet.is_running(algo_id):
        raise HTTPException(
            status_code=409, detail="stop this algorithm before changing its active version"
        )

    db.set_algo_fields(algo_id, active_version=body.version_id)
    _audit(request, principal, "algo.activate", f"{algo_id} -> v{v['version']}")
    return _algo_view(db, db.algo(algo_id))


@router.post("/{algo_id}/mode")
async def set_mode(
    algo_id: str, body: ModeRequest, request: Request,
    principal: Principal = Depends(require_auth),
) -> dict:
    if body.mode not in ("paper", "live"):
        raise HTTPException(status_code=400, detail="mode must be 'paper' or 'live'")
    db = ctx().db
    algo = db.algo(algo_id)
    if not algo:
        raise HTTPException(status_code=404, detail="no such algorithm")
    if ctx().fleet.is_running(algo_id):
        raise HTTPException(status_code=409, detail="stop this algorithm before changing its mode")

    if body.mode == "live" and algo.get("shadow_of"):
        raise HTTPException(
            status_code=409,
            detail=(
                "a shadow is a paper twin by definition — it exists to be compared "
                "against the live run, not to place orders of its own"
            ),
        )

    ctx().fleet.set_mode(algo_id, body.mode)
    _audit(request, principal, "algo.mode", f"{algo_id} -> {body.mode}")
    db.add_event(
        "critical" if body.mode == "live" else "info",
        f"'{algo['name']}' set to {body.mode.upper()} by {principal.subject}",
        source="api", algo_id=algo_id,
    )
    return _algo_view(db, db.algo(algo_id))


@router.post("/{algo_id}/start")
async def start_algo(
    algo_id: str, request: Request, body: StartRequest | None = None,
    principal: Principal = Depends(require_auth),
) -> dict:
    """Start an algorithm in the mode the operator picked.

    Starting also arms it: from here on the scheduler brings it up at the open
    and takes it down at the close, until someone stops it.
    """
    db = ctx().db
    algo = db.algo(algo_id)
    if not algo:
        raise HTTPException(status_code=404, detail="no such algorithm")
    if algo.get("kind") != "builtin" and not algo.get("active_version"):
        raise HTTPException(
            status_code=409,
            detail="this algorithm has no active version — open it and activate one, or upload it again",
        )

    mode = (body.mode if body else None) or algo.get("mode") or "paper"
    if mode not in ("paper", "live"):
        raise HTTPException(status_code=400, detail="mode must be 'paper' or 'live'")
    if mode == "live" and algo.get("shadow_of"):
        raise HTTPException(
            status_code=409, detail="a shadow is a paper twin and cannot place real orders"
        )
    ctx().fleet.set_mode(algo_id, mode)

    _audit(request, principal, "algo.start", f"{algo_id} mode={mode}")
    res = await asyncio.to_thread(ctx().fleet.start, algo_id, trigger=f"manual:{principal.subject}")
    if not res.get("ok"):
        raise HTTPException(status_code=409, detail=res.get("detail"))
    db.set_algo_fields(algo_id, enabled=1)
    db.add_event(
        "critical" if mode == "live" else "info",
        f"'{algo['name']}' started in {mode.upper()} by {principal.subject} — "
        f"it will now start itself at the open until stopped",
        source="api", algo_id=algo_id,
    )
    return {**res, "mode": mode}


class SwitchRequest(BaseModel):
    # The dashboard asks first: switching stops whatever trades real money now.
    confirm: bool = False


def _live_now(algo_id: str) -> list[tuple[str, str]]:
    """(id, name) of every other algorithm trading real money right now."""
    db, fleet = ctx().db, ctx().fleet
    out = []
    for other_id, sup in fleet.all().items():
        if other_id == algo_id:
            continue
        sup.refresh()
        if sup.state.running and sup.state.mode == "live":
            out.append((other_id, (db.algo(other_id) or {}).get("name") or other_id))
    return out


@router.post("/{algo_id}/switch")
async def switch_live(
    algo_id: str, request: Request, body: SwitchRequest | None = None,
    principal: Principal = Depends(require_auth),
) -> dict:
    """Move real-money trading to this algorithm in one step.

    Whatever trades real money now is stopped first, exactly as its Stop button
    would (a program sells any open position on its way out, and is disarmed),
    then this one starts on real money and is armed. Only one algorithm may trade
    real money on the account, so this is the way to change which one does,
    in a normal session or a special one such as Muhurat trading.
    """
    db, fleet = ctx().db, ctx().fleet
    algo = db.algo(algo_id)
    if not algo:
        raise HTTPException(status_code=404, detail="no such algorithm")
    if algo.get("shadow_of"):
        raise HTTPException(status_code=409, detail="a shadow is a paper twin and cannot place real orders")
    if algo.get("kind") != "builtin" and not algo.get("active_version"):
        raise HTTPException(
            status_code=409,
            detail="this algorithm has no active version — open it and activate one, or upload it again",
        )
    me = fleet.get(algo_id)
    if me is None:
        raise HTTPException(status_code=404, detail="no such algorithm")
    me.refresh()
    if me.state.running and me.state.mode == "live":
        raise HTTPException(status_code=409, detail=f"'{algo['name']}' is already trading real money")
    current = _live_now(algo_id)
    if not (body and body.confirm):
        what = (f"stops {', '.join(n for _, n in current)} (a program sells any open position first) and "
                if current else "")
        raise HTTPException(status_code=409, detail=f"confirm the switch: it {what}starts '{algo['name']}' on real money")

    who = principal.subject
    _audit(request, principal, "algo.switch", f"{', '.join(i for i, _ in current) or 'none'} -> {algo_id}")
    for other_id, name in current:
        res = await asyncio.to_thread(fleet.stop, other_id, reason=f"switch to {algo['name']} by {who}")
        if not res.get("ok"):
            raise HTTPException(
                status_code=409,
                detail=f"could not stop '{name}' ({res.get('detail')}) — nothing was switched; it is still trading",
            )
        db.set_algo_fields(other_id, enabled=0)
        db.add_event("critical", f"SWITCH: '{name}' stopped by {who} to move real money to '{algo['name']}'",
                     source="api", algo_id=other_id)
    if me.state.running:
        # On paper now: stopped and started again on real money. Not an operator
        # stop, so nothing is held down afterwards.
        await asyncio.to_thread(fleet.stop, algo_id, reason=f"restarting on real money for {who}", manual=False)
    fleet.set_mode(algo_id, "live")
    res = await asyncio.to_thread(fleet.start, algo_id, trigger=f"switch:{who}")
    stopped = ", ".join(n for _, n in current)
    if not res.get("ok"):
        db.add_event("critical", f"SWITCH: '{algo['name']}' did not start ({res.get('detail')})"
                     + (f" after {stopped} was stopped — nothing is trading real money" if stopped else ""),
                     source="api", algo_id=algo_id)
        raise HTTPException(
            status_code=409,
            detail=f"'{algo['name']}' did not start: {res.get('detail')}"
            + (f". {stopped} has been stopped, so nothing is trading real money now." if stopped else ""),
        )
    db.set_algo_fields(algo_id, enabled=1)
    db.add_event("critical", f"SWITCH: '{algo['name']}' started on REAL MONEY by {who}"
                 + (f", replacing {stopped}" if stopped else ""), source="api", algo_id=algo_id)
    return {**res, "mode": "live", "stopped": [i for i, _ in current],
            "detail": f"Real money moved to '{algo['name']}'" + (f" from {stopped}." if stopped else ".")}


@router.post("/{algo_id}/stop")
async def stop_algo(
    algo_id: str, request: Request, principal: Principal = Depends(require_auth)
) -> dict:
    if not ctx().db.algo(algo_id):
        raise HTTPException(status_code=404, detail="no such algorithm")
    _audit(request, principal, "algo.stop", algo_id)
    # Off the event loop: a program gets up to 75s to square off, and the whole
    # desk (every request, the live feed) used to freeze while it did.
    res = await asyncio.to_thread(ctx().fleet.stop, algo_id, reason=f"manual:{principal.subject}")
    ctx().db.set_algo_fields(algo_id, enabled=0)
    if not res.get("ok"):
        raise HTTPException(status_code=409, detail=res.get("detail"))
    return res


@router.delete("/{algo_id}")
async def delete_algo(
    algo_id: str, request: Request, principal: Principal = Depends(require_auth)
) -> dict:
    db = ctx().db
    algo = db.algo(algo_id)
    if not algo:
        raise HTTPException(status_code=404, detail="no such algorithm")
    if algo.get("kind") == "builtin":
        raise HTTPException(status_code=409, detail="the built-in build cannot be removed")

    # Removing a registration that is mid-session would leave an orphan engine
    # holding a position with nothing left to supervise it, so stop it first.
    # Its shadow goes with it for the same reason.
    removed = []
    for target in (f"{algo_id}{SHADOW_SUFFIX}", algo_id):
        if not db.algo(target):
            continue
        if ctx().fleet.is_running(target):
            await asyncio.to_thread(ctx().fleet.stop, target, reason=f"removed by {principal.subject}", force=True)
        db.delete_algo(target)
        removed.append(target)
    # Drops the supervisors that no longer have a registration behind them.
    ctx().fleet.sync()

    _audit(request, principal, "algo.delete", ", ".join(removed))
    db.add_event(
        "warn", f"algorithm '{algo['name']}' removed by {principal.subject}",
        source="api", algo_id=algo_id,
    )
    return {"ok": True, "deleted": removed}


# ── shadow mode ──────────────────────────────────────────────────────────────


@router.post("/{algo_id}/shadow")
async def set_shadow(
    algo_id: str, body: ShadowRequest, request: Request,
    principal: Principal = Depends(require_auth),
) -> dict:
    """Create or remove a paper twin of this algorithm.

    The shadow is a separate registration pinned to the same source, forced to
    paper and never promotable. It exists to be compared against, so it must
    never be switchable to live: a second engine quietly sending real orders is
    the opposite of what this is for.
    """
    db = ctx().db
    algo = db.algo(algo_id)
    if not algo:
        raise HTTPException(status_code=404, detail="no such algorithm")
    if algo.get("shadow_of"):
        raise HTTPException(status_code=400, detail="a shadow cannot have a shadow of its own")

    shadow_id = f"{algo_id}{SHADOW_SUFFIX}"

    if not body.enabled:
        if ctx().fleet.is_running(shadow_id):
            raise HTTPException(status_code=409, detail="stop the shadow before removing it")
        db.delete_algo(shadow_id)
        _audit(request, principal, "algo.shadow.remove", shadow_id)
        return {"ok": True, "shadow_algo_id": None}

    version = db.version(algo.get("active_version")) if algo.get("active_version") else None
    if not version and algo.get("kind") != "builtin":
        raise HTTPException(status_code=409, detail="no active version to shadow")

    db.upsert_algo(
        shadow_id,
        f"{algo['name']} (shadow)",
        kind=algo.get("kind") or "uploaded",
        notes=f"paper twin of {algo_id}, for measuring execution drag",
    )
    db.set_algo_fields(shadow_id, shadow_of=algo_id, mode="paper", enabled=0)

    if version:
        # Same bytes as the live one, recorded as already gated: this source
        # passed the gate when it was uploaded and has not changed since.
        shadow_version_id = db.add_version(
            shadow_id, version["source"], version["sha256"], principal.subject
        )
        db.set_version_status(shadow_version_id, versions.STATUS_PASSED, {"inherited_from": version["id"]})
        db.set_algo_fields(shadow_id, active_version=shadow_version_id)

    _audit(request, principal, "algo.shadow.create", shadow_id)
    db.add_event(
        "info",
        f"shadow created for '{algo['name']}' — paper twin measuring execution drag",
        source="api", algo_id=shadow_id,
    )
    return {"ok": True, "shadow_algo_id": shadow_id, **_algo_view(db, db.algo(shadow_id))}


@router.get("/{algo_id}/shadow")
async def shadow_comparison(algo_id: str) -> dict:
    db = ctx().db
    algo = db.algo(algo_id)
    if not algo:
        raise HTTPException(status_code=404, detail="no such algorithm")
    shadow_id = f"{algo_id}{SHADOW_SUFFIX}"
    if not db.algo(shadow_id):
        return {"configured": False, "live_algo_id": algo_id, "shadow_algo_id": None}
    return {"configured": True, **shadow.compare(db, algo_id, shadow_id)}
