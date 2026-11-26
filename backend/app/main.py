"""Meridian Capital — trading control plane API.

Runs on the Oracle Cloud VM alongside the engine. Serves the Vercel dashboard
over authenticated HTTPS and a WebSocket, supervises the engine process, and
starts and stops it automatically around the NSE session.
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
import time
from collections.abc import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response

from app import webpush
from app.config import settings
from app.deps import build_context, ctx
from app.notify import Notifier
from app.program_output import ProgramOutput
from app.replay import Archiver
from app.routers import algos, auth, control, data, desk, push, reliability, tuning, ws
from app.security import relay_ok
from app.supervisor import StagingOnly
from app.upkeep import Upkeep
from engine.clock import now_ist
from engine.config import BANNER, BUILD_VERSION

log = logging.getLogger("meridian.api")
API_VERSION = "1.0.0"


@contextlib.asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    problems = settings.validate()
    if problems:
        raise RuntimeError("configuration errors: " + "; ".join(problems))

    c = build_context()
    c.db.add_event(
        "info", f"API v{API_VERSION} started ({BUILD_VERSION})", source="api"
    )
    await c.hub.start()
    await c.sched.start()
    programs_out = ProgramOutput(c.db, c.fleet)
    await programs_out.start()
    webpush.attach(c.db, (settings.cors_origins or [""])[0])   # phone notifications, beside Telegram
    notifier = Notifier(c.db, fleet=c.fleet)
    await notifier.start()
    archiver = Archiver(c.db)
    await archiver.start()
    # Heartbeat to the outside monitor, the after-close check, the weekly summary,
    # journal retention and machine warnings. It controls no algorithm.
    c.upkeep = Upkeep(c.db, fleet=c.fleet, dashboard=(settings.cors_origins or [""])[0])
    await c.upkeep.start()
    # Reconcile immediately so a mid-session restart brings the engine back at once.
    c.sched.tick()
    try:
        yield
    finally:
        await c.upkeep.stop()
        await archiver.stop()
        await notifier.stop()
        webpush.attach(None)
        await programs_out.stop()
        await c.sched.stop()
        await c.hub.stop()
        c.db.add_event("info", "API shutting down (engine left running)", source="api")


app = FastAPI(
    title="Meridian Capital Control Plane",
    version=API_VERSION,
    description=BANNER,
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "If-None-Match"],
    expose_headers=["ETag"],
    max_age=600,
)


# A request slower than this is written to the service log with its timing
# (journalctl -u meridian-api | grep "slow request"), so a slow desk can be
# traced to the call that made it slow instead of guessed at.
SLOW_REQUEST_MS = 800.0


async def _not_modified(request: Request, response):
    """ETag on a GET's JSON answer, and 304 with no body when the dashboard already has that answer.

    The dashboard polls: most polls ask for figures that have not changed since the last one (the day's
    chart, the trade list, the chain outside market hours). They are still worked out here, but only a
    few bytes travel back through the relay instead of the whole answer, and the browser skips parsing
    and redrawing it.
    """
    if request.method != "GET" or response.status_code != 200 or \
            not (response.headers.get("content-type") or "").startswith("application/json"):
        return response
    body = b"".join([chunk async for chunk in response.body_iterator])
    tag = 'W/"' + hashlib.blake2b(body, digest_size=12).hexdigest() + '"'
    headers = {k: v for k, v in response.headers.items() if k.lower() not in ("content-length", "etag")}
    headers["ETag"] = tag
    if tag in [t.strip() for t in (request.headers.get("if-none-match") or "").split(",")]:
        headers.pop("content-type", None)
        return Response(status_code=304, headers=headers)
    return Response(content=body, status_code=200, headers=headers, media_type=response.media_type)


# Paths a caller other than the dashboard's relay may reach. /health is the
# outside monitor's; the live WebSocket (not HTTP middleware) is ticket-gated.
OPEN_PATHS = frozenset({"/health"})


@app.middleware("http")
async def relay_lock(request: Request, call_next):
    """With MERIDIAN_RELAY_SECRET set, the API answers only the dashboard's relay.

    Every REST call the dashboard makes goes through its server-side relay on
    Vercel, which presents the secret. Anything else on the internet — a script, a
    scanner, someone holding a stolen session token — is refused before any route
    runs, so the sign-in route cannot even be reached to guess at.
    """
    if settings.relay_secret and request.url.path not in OPEN_PATHS and not relay_ok(request):
        return JSONResponse(status_code=403, content={"detail": "forbidden"})
    return await call_next(request)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    started = time.perf_counter()
    response = await _not_modified(request, await call_next(request))
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    response.headers["Server-Timing"] = f"app;dur={elapsed_ms:.1f}"
    if elapsed_ms >= SLOW_REQUEST_MS:
        log.warning("slow request %s %s %.0f ms", request.method, request.url.path, elapsed_ms)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store"
    response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
    response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    return response


app.include_router(auth.router)
app.include_router(data.router)
app.include_router(control.router)
app.include_router(tuning.router)
app.include_router(algos.router)
app.include_router(desk.router)
app.include_router(reliability.router)
app.include_router(push.router)
app.include_router(ws.router)


def _local_or_relay(request: Request) -> bool:
    """A call made on the VM itself (the install script's check), or by the dashboard."""
    if relay_ok(request):
        return True
    host = request.client.host if request.client else ""
    # Through Caddy, uvicorn rewrites the client to the visitor's address from
    # X-Forwarded-For, which Caddy always sets; only a direct local call has neither.
    return host in ("127.0.0.1", "::1") and "x-forwarded-for" not in request.headers


@app.get("/health", tags=["meta"])
async def health(request: Request) -> dict:
    """Unauthenticated liveness probe — deliberately exposes no trading data. To
    the internet it says only that the service is up; whether anything is running
    is for the VM itself and the dashboard."""
    if not _local_or_relay(request):
        return {"ok": True, "service": "meridian-api"}
    try:
        running = ctx().sup.state.running
    except RuntimeError:
        running = None
    return {
        "ok": True,
        "service": "meridian-api",
        "version": API_VERSION,
        "engine_running": running,
        "now_ist": now_ist().isoformat(timespec="seconds"),
    }


@app.exception_handler(StagingOnly)
async def staging_only(_request: Request, exc: StagingOnly) -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": str(exc)})


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception) -> JSONResponse:
    log.exception("unhandled error on %s", request.url.path)
    with contextlib.suppress(Exception):
        ctx().db.add_event("error", f"API error on {request.url.path}: {exc}", source="api")
    return JSONResponse(status_code=500, content={"detail": "internal error"})
