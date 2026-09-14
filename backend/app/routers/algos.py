"""Algorithm registry: upload, gate, promote, and per-algorithm control.

The one rule this router exists to enforce: source the operator pasted in a
browser cannot reach the broker until it has passed the acceptance gate, and
cannot reach *real money* until it has also survived the required run of clean
paper sessions and the operator has typed the phrase.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.algo_store import MAX_SOURCE_BYTES, run_gate, sha256, slugify
from app.deps import ctx
from app.security import Principal, client_ip, require_auth
from engine import promotion
from engine.gate import check_catalogue

router = APIRouter(prefix="/api/algos", tags=["algos"], dependencies=[Depends(require_auth)])


class UploadRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    source: str = Field(min_length=1)
    notes: str = Field(default="", max_length=500)
    algo_id: str | None = None


class ModeRequest(BaseModel):
    mode: str
    confirm: str = ""


class ActivateRequest(BaseModel):
    version_id: int


def _audit(request: Request, principal: Principal, action: str, detail: str = "") -> None:
    ctx().db.audit(principal.subject, action, detail, client_ip(request))


def _algo_view(db, algo: dict) -> dict:
    versions = db.versions(algo["id"], limit=20)
    active = db.version(algo["active_version"]) if algo.get("active_version") else None
    sup = ctx().fleet.snapshot(algo["id"]) if hasattr(ctx(), "fleet") else {}
    return {
        **algo,
        "enabled": bool(algo.get("enabled")),
        "versions": versions,
        "active": {k: v for k, v in (active or {}).items() if k != "source"} or None,
        "promotion": promotion.progress(active),
        "runtime": sup,
    }


@router.get("")
async def list_algos() -> dict:
    db = ctx().db
    return {
        "algos": [_algo_view(db, a) for a in db.algos()],
        "paper_sessions_required": promotion.PAPER_SESSIONS_REQUIRED,
        "go_live_phrase": promotion.GO_LIVE_PHRASE,
    }


@router.get("/gate-catalogue")
async def gate_catalogue() -> dict:
    """What an upload will be held to, published before anyone uploads."""
    return {
        "checks": check_catalogue(),
        "paper_sessions_required": promotion.PAPER_SESSIONS_REQUIRED,
    }


@router.post("")
async def upload_algo(
    body: UploadRequest, request: Request, principal: Principal = Depends(require_auth)
) -> dict:
    """Accept source, run the acceptance gate, and record the verdict.

    The gate runs synchronously: the operator is waiting for a yes or no, and
    an upload that silently lands in a queue is an upload someone assumes
    passed.
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

    existing = db.algo(algo_id)
    if existing and existing.get("kind") == "builtin":
        raise HTTPException(
            status_code=409,
            detail="the built-in build cannot be overwritten; upload under a different name",
        )

    db.upsert_algo(algo_id, body.name.strip(), kind="uploaded", notes=body.notes)
    version_id = db.add_version(algo_id, body.source, sha256(body.source), principal.subject)
    version = db.version(version_id)

    report = run_gate(body.source)
    passed = bool(report.get("passed"))
    db.set_version_status(
        version_id, promotion.STATUS_PASSED if passed else promotion.STATUS_FAILED, report
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

    if passed and not existing:
        # First accepted version of a new algorithm becomes its active one, in
        # paper mode. Nothing starts running on its own.
        db.set_algo_fields(algo_id, active_version=version_id, mode="paper", enabled=0)

    return {
        "ok": True,
        "algo_id": algo_id,
        "version_id": version_id,
        "version": version["version"],
        "passed": passed,
        "report": report,
        "next": (
            f"cleared for paper trading — {promotion.PAPER_SESSIONS_REQUIRED} clean paper "
            f"sessions are required before it can trade real money"
            if passed
            else "rejected — fix the failures below and upload again"
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

    ok, why = promotion.may_run_paper(v)
    if not ok:
        raise HTTPException(status_code=409, detail=why)
    if ctx().fleet.is_running(algo_id):
        raise HTTPException(
            status_code=409, detail="stop this algorithm before changing its active version"
        )

    db.set_algo_fields(algo_id, active_version=body.version_id)
    # A version that has not cleared live cannot remain selected in live mode.
    algo = db.algo(algo_id)
    if algo and algo.get("mode") == "live" and not promotion.may_run_live(v)[0]:
        db.set_algo_fields(algo_id, mode="paper")
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

    if body.mode == "live":
        version = db.version(algo["active_version"]) if algo.get("active_version") else None
        if not version:
            raise HTTPException(status_code=409, detail="no active version to run")
        ok, why = promotion.may_run_live(version)
        if not ok:
            raise HTTPException(status_code=409, detail=why)
        if body.confirm != promotion.GO_LIVE_PHRASE:
            raise HTTPException(
                status_code=400,
                detail=f"switching to LIVE requires confirm == '{promotion.GO_LIVE_PHRASE}'",
            )

    db.set_algo_fields(algo_id, mode=body.mode)
    _audit(request, principal, "algo.mode", f"{algo_id} -> {body.mode}")
    db.add_event(
        "critical" if body.mode == "live" else "info",
        f"'{algo['name']}' set to {body.mode.upper()} by {principal.subject}",
        source="api", algo_id=algo_id,
    )
    return _algo_view(db, db.algo(algo_id))


@router.post("/{algo_id}/start")
async def start_algo(
    algo_id: str, request: Request, principal: Principal = Depends(require_auth)
) -> dict:
    db = ctx().db
    algo = db.algo(algo_id)
    if not algo:
        raise HTTPException(status_code=404, detail="no such algorithm")
    version = db.version(algo["active_version"]) if algo.get("active_version") else None
    if not version:
        raise HTTPException(status_code=409, detail="no active version to run")

    checker = promotion.may_run_live if algo["mode"] == "live" else promotion.may_run_paper
    ok, why = checker(version)
    if not ok:
        raise HTTPException(status_code=409, detail=why)

    _audit(request, principal, "algo.start", f"{algo_id} mode={algo['mode']}")
    res = ctx().fleet.start(algo_id, trigger=f"manual:{principal.subject}")
    if not res.get("ok"):
        raise HTTPException(status_code=409, detail=res.get("detail"))
    db.set_algo_fields(algo_id, enabled=1)
    return res


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
    if ctx().fleet.is_running(algo_id):
        raise HTTPException(status_code=409, detail="stop this algorithm before removing it")
    db.delete_algo(algo_id)
    _audit(request, principal, "algo.delete", algo_id)
    return {"ok": True, "deleted": algo_id}
