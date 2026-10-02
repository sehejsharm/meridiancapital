"""Process-wide singletons, wired once during app startup."""

from __future__ import annotations

from dataclasses import dataclass

from app.config import settings
from app.fleet import Fleet
from app.hub import Hub
from app.program_status import live_snapshot_key
from app.scheduler import Scheduler
from app.supervisor import Supervisor
from shared.db import K_MODE, Database
from shared.nse_holidays import NSE_HOLIDAYS
from shared.nse_holidays import SOURCE as NSE

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


def _load_nse_holidays(db: Database) -> None:
    """NSE's published closures go into the calendar without anyone typing them."""
    added = db.seed_holidays(NSE_HOLIDAYS, source=NSE)
    if added:
        years = sorted({d[:4] for d in added})
        db.add_event(
            "info",
            f"loaded {len(added)} NSE trading holidays for {', '.join(years)} — "
            f"armed algorithms stay down on those days",
            source="scheduler",
        )


def build_context() -> Context:
    global _ctx
    db = Database(settings.db_path)
    _seed_builtin(db)
    _load_nse_holidays(db)
    sup = Supervisor(db)
    sched = Scheduler(db, sup)
    fleet = Fleet(db, supervisor_factory=Supervisor)
    # The built-in's supervisor is shared with the scheduler, so automatic
    # start/stop and manual fleet control cannot disagree about its state.
    fleet._sups[BUILTIN_ID] = sup
    # And the scheduler drives the rest of the fleet through the same window.
    sched.fleet = fleet
    hub = Hub(
        db,
        status_provider=lambda: system_status(db, sup, sched, fleet),
        snapshot_key=lambda: live_snapshot_key(db, fleet),
    )
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
