"""Process-wide singletons, wired once during app startup."""

from __future__ import annotations

from dataclasses import dataclass

from app.config import settings
from app.fleet import Fleet
from app.hub import Hub
from app.scheduler import Scheduler
from app.supervisor import Supervisor
from shared.db import K_MODE, Database

BUILTIN_ID = "gk50k"


@dataclass
class Context:
    db: Database
    sup: Supervisor
    sched: Scheduler
    hub: Hub
    fleet: Fleet


_ctx: Context | None = None


def _seed_builtin(db: Database) -> None:
    """The built-in build is always present in the registry.

    It is registered rather than special-cased so the dashboard can show it in
    the same list as everything else, with the same controls. Its source is
    compiled into the engine, so it has no uploaded version and needs no gate.
    """
    if db.algo(BUILTIN_ID):
        return
    db.upsert_algo(
        BUILTIN_ID,
        "GANESH KAVACH 50K",
        kind="builtin",
        notes="Config #5 v3 — the backtested build, compiled into the engine",
    )
    db.set_algo_fields(BUILTIN_ID, mode=db.kv_get(K_MODE, "paper"))


def build_context() -> Context:
    global _ctx
    db = Database(settings.db_path)
    _seed_builtin(db)
    sup = Supervisor(db)
    sched = Scheduler(db, sup)
    fleet = Fleet(db, supervisor_factory=Supervisor)
    # The built-in's supervisor is shared with the scheduler, so automatic
    # start/stop and manual fleet control cannot disagree about its state.
    fleet._sups[BUILTIN_ID] = sup
    hub = Hub(db, status_provider=lambda: system_status(db, sup, sched, fleet))
    _ctx = Context(db=db, sup=sup, sched=sched, hub=hub, fleet=fleet)
    return _ctx


def ctx() -> Context:
    if _ctx is None:
        raise RuntimeError("application context is not initialised")
    return _ctx


def system_status(db: Database, sup: Supervisor, sched: Scheduler, fleet: Fleet) -> dict:
    offline = db.kv_get("engine:offline", None)
    return {
        "engine": sup.snapshot(),
        "schedule": sched.status(),
        "offline_note": offline if not sup.state.running else None,
        "operator": settings.operator,
        "fund": "Meridian Capital",
        "fleet": fleet.overview(),
    }
