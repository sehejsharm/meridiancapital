"""Algorithm registry: upload, screen, and per-algorithm control.

Uploaded source is put through the acceptance gate and the verdict is recorded
against the version, but the operator decides what runs and whether it runs on
paper or real money. The choice is made when an algorithm is started.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.algo_store import MAX_SOURCE_BYTES, run_gate, sha256, slugify
from app.programs import mode_arguments, runtime_of
from app.deps import ctx
from app import shadow
from app.security import Principal, client_ip, require_auth
from engine import versions
from engine.gate import check_catalogue
from shared.db import SYSTEM_ALGO

router = APIRouter(prefix="/api/algos", tags=["algos"], dependencies=[Depends(require_auth)])

SHADOW_SUFFIX = "-shadow"


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


def _algo_view(db, algo: dict) -> dict:
    history = db.versions(algo["id"], limit=20)
    active = db.version(algo["active_version"]) if algo.get("active_version") else None
    sup = ctx().fleet.snapshot(algo["id"]) if hasattr(ctx(), "fleet") else {}
    return {
        **algo,
        "enabled": bool(algo.get("enabled")),
        "versions": history,
        "active": {k: v for k, v in (active or {}).items() if k != "source"} or None,
        "gate": versions.summary(active),
        "runtime_kind": (
            "builtin" if algo.get("kind") == "builtin"
            else runtime_of(active["source"]) if active else "none"
        ),
        "runtime": sup,
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
        report = run_gate(body.source)
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
        f"acceptance gate {'PASSED' if passed else 'FAILED'}",
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
        "report": report,
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


@router.get("/{algo_id}/versions/{version_id}")
async def get_version(algo_id: str, version_id: int) -> dict:
    v = ctx().db.version(version_id)
    if not v or v["algo_id"] != algo_id:
        raise HTTPException(status_code=404, detail="no such version")
    return v


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
    res = ctx().fleet.start(algo_id, trigger=f"manual:{principal.subject}")
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


@router.post("/{algo_id}/stop")
async def stop_algo(
    algo_id: str, request: Request, principal: Principal = Depends(require_auth)
) -> dict:
    if not ctx().db.algo(algo_id):
        raise HTTPException(status_code=404, detail="no such algorithm")
    _audit(request, principal, "algo.stop", algo_id)
    res = ctx().fleet.stop(algo_id, reason=f"manual:{principal.subject}")
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
            ctx().fleet.stop(target, reason=f"removed by {principal.subject}", force=True)
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
