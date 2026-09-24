"""Meridian Capital — trading control plane API.

Runs on the Oracle Cloud VM alongside the engine. Serves the Vercel dashboard
over authenticated HTTPS and a WebSocket, supervises the engine process, and
starts and stops it automatically around the NSE session.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import settings
from app.deps import build_context, ctx
from app.program_output import ProgramOutput
from app.routers import algos, auth, control, data, desk, tuning, ws
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
    # Reconcile immediately so a mid-session restart brings the engine back at once.
    c.sched.tick()
    try:
        yield
    finally:
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
    allow_headers=["Authorization", "Content-Type"],
    max_age=600,
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store"
    return response


app.include_router(auth.router)
app.include_router(data.router)
app.include_router(control.router)
app.include_router(tuning.router)
app.include_router(algos.router)
app.include_router(desk.router)
app.include_router(ws.router)


@app.get("/health", tags=["meta"])
async def health() -> dict:
    """Unauthenticated liveness probe — deliberately exposes no trading data."""
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


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception) -> JSONResponse:
    log.exception("unhandled error on %s", request.url.path)
    with contextlib.suppress(Exception):
        ctx().db.add_event("error", f"API error on {request.url.path}: {exc}", source="api")
    return JSONResponse(status_code=500, content={"detail": "internal error"})
