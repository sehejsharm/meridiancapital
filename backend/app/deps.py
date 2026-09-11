"""Process-wide singletons, wired once during app startup."""

from __future__ import annotations

from dataclasses import dataclass

from app.config import settings
from app.hub import Hub
from app.scheduler import Scheduler
from app.supervisor import Supervisor
from shared.db import Database


@dataclass
class Context:
    db: Database
    sup: Supervisor
    sched: Scheduler
    hub: Hub


_ctx: Context | None = None


def build_context() -> Context:
    global _ctx
    db = Database(settings.db_path)
    sup = Supervisor(db)
    sched = Scheduler(db, sup)
    hub = Hub(db, status_provider=lambda: system_status(db, sup, sched))
    _ctx = Context(db=db, sup=sup, sched=sched, hub=hub)
    return _ctx


def ctx() -> Context:
    if _ctx is None:
        raise RuntimeError("application context is not initialised")
    return _ctx


def system_status(db: Database, sup: Supervisor, sched: Scheduler) -> dict:
    offline = db.kv_get("engine:offline", None)
    return {
        "engine": sup.snapshot(),
        "schedule": sched.status(),
        "offline_note": offline if not sup.state.running else None,
        "operator": settings.operator,
        "fund": "Meridian Capital",
    }
