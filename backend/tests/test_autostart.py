"""Starting an algorithm arms it for the daily open.

The scheduler used to reconcile only the built-in engine's supervisor, so an
uploaded algorithm started once never came back the next morning. These tests
drive the scheduler's tick at chosen times of day and assert what the fleet
does around 09:15 and 15:30.
"""

from __future__ import annotations

from app.scheduler import Scheduler
from app.supervisor import DEFAULT_ALGO
from shared.market_calendar import should_be_running

# A Thursday, outside any configured holiday.
#
# The supervision window is 09:05–15:25, not the 09:15–15:30 market session:
# the engine needs a few minutes to log in to Angel, pull the scrip master and
# build 90+ candles, so it has to be up *before* the bell. When it may actually
# enter a trade is the strategy's business (ENTRY_START, 10:15).
TRADING_DAY = "2026-09-10"
BEFORE_WARMUP = f"{TRADING_DAY} 08:55"
AFTER_WARMUP = f"{TRADING_DAY} 09:06"
AT_THE_BELL = f"{TRADING_DAY} 09:15"
AFTER_CLOSE = f"{TRADING_DAY} 15:45"


class FakeState:
    def __init__(self):
        self.running = False
        self.manual_override = False
        self.last_exit_code = None
        self.restarts_this_session = 0


class FakeSupervisor:
    """Records what the scheduler asked of it."""

    def __init__(self):
        self.state = FakeState()
        self.starts = 0
        self.stops = 0

    def refresh(self):
        pass

    def start(self, trigger="manual"):
        self.starts += 1
        self.state.running = True
        return {"ok": True, "detail": "started"}

    def stop(self, reason="", force=False):
        self.stops += 1
        self.state.running = False
        return {"ok": True, "detail": "stopped"}

    def note_crash(self):
        self.restarts = self.state.restarts_this_session = self.state.restarts_this_session + 1


class FakeFleet:
    def __init__(self, sups):
        self._sups = sups

    def sync(self):
        pass

    def get(self, algo_id):
        return self._sups.get(algo_id)

    def start(self, algo_id, trigger="manual"):
        return self._sups[algo_id].start(trigger=trigger)

    def stop(self, algo_id, reason="", force=False, manual=True):
        res = self._sups[algo_id].stop(reason=reason, force=force)
        if manual:
            self._sups[algo_id].state.manual_override = True
        return res


def _window(moment: str) -> bool:
    from datetime import datetime

    return should_be_running(datetime.strptime(moment, "%Y-%m-%d %H:%M"), set())


def _sched(tmp_db, algos, sups):
    for algo in algos:
        tmp_db.upsert_algo(algo["id"], algo["id"], kind="uploaded")
        tmp_db.set_algo_fields(algo["id"], enabled=1 if algo.get("armed") else 0)
    sched = Scheduler(tmp_db, FakeSupervisor(), fleet=FakeFleet(sups))
    return sched


# ── the window itself ────────────────────────────────────────────────────────
def test_the_engine_is_up_and_warm_before_the_bell():
    assert _window(BEFORE_WARMUP) is False
    assert _window(AFTER_WARMUP) is True, "not up in time to warm up for the open"
    assert _window(AT_THE_BELL) is True, "not running when the market opens"
    assert _window(AFTER_CLOSE) is False


# ── arming ───────────────────────────────────────────────────────────────────
def test_an_armed_algo_is_started_at_the_open(tmp_db):
    sup = FakeSupervisor()
    sched = _sched(tmp_db, [{"id": "alpha", "armed": True}], {"alpha": sup})

    sched.tick_fleet(want=_window(BEFORE_WARMUP))
    assert sup.starts == 0, "started before the warm-up window"

    sched.tick_fleet(want=_window(AFTER_WARMUP))
    assert sup.starts == 1
    assert sup.state.running


def test_an_unarmed_algo_is_left_alone(tmp_db):
    sup = FakeSupervisor()
    sched = _sched(tmp_db, [{"id": "idle", "armed": False}], {"idle": sup})
    sched.tick_fleet(want=_window(AFTER_WARMUP))
    assert sup.starts == 0
    assert sched.fleet_decisions["idle"] == "not armed"


def test_an_armed_algo_is_stopped_at_the_close(tmp_db):
    sup = FakeSupervisor()
    sched = _sched(tmp_db, [{"id": "alpha", "armed": True}], {"alpha": sup})
    sched.tick_fleet(want=_window(AFTER_WARMUP))
    assert sup.state.running

    sched.tick_fleet(want=_window(AFTER_CLOSE))
    assert sup.stops == 1
    assert not sup.state.running


def test_the_close_does_not_disarm_it_for_tomorrow(tmp_db):
    """The end-of-session stop is the scheduler's, not the operator's — if it
    counted as a manual stop, nothing would ever run a second day."""
    sup = FakeSupervisor()
    sched = _sched(tmp_db, [{"id": "alpha", "armed": True}], {"alpha": sup})
    sched.tick_fleet(want=_window(AFTER_WARMUP))
    sched.tick_fleet(want=_window(AFTER_CLOSE))

    assert sup.state.manual_override is False
    assert tmp_db.algo("alpha")["enabled"] == 1

    sched.tick_fleet(want=_window(AFTER_WARMUP))  # the next morning
    assert sup.starts == 2


def test_an_operator_stop_keeps_it_down_for_the_rest_of_the_day(tmp_db):
    sup = FakeSupervisor()
    sched = _sched(tmp_db, [{"id": "alpha", "armed": True}], {"alpha": sup})
    sched.tick_fleet(want=_window(AFTER_WARMUP))
    assert sup.starts == 1

    sup.stop()
    sup.state.manual_override = True

    sched.tick_fleet(want=_window(AFTER_WARMUP))
    assert sup.starts == 1, "the scheduler undid an operator stop"
    assert sched.fleet_decisions["alpha"] == "stopped by operator"


def test_a_running_algo_is_not_started_twice(tmp_db):
    sup = FakeSupervisor()
    sched = _sched(tmp_db, [{"id": "alpha", "armed": True}], {"alpha": sup})
    for _ in range(4):
        sched.tick_fleet(want=_window(AFTER_WARMUP))
    assert sup.starts == 1


def test_the_builtin_is_left_to_the_other_half_of_the_tick(tmp_db):
    """Its supervisor is shared with `tick`; reconciling it twice would race."""
    sup = FakeSupervisor()
    tmp_db.upsert_algo(DEFAULT_ALGO, "builtin", kind="builtin")
    tmp_db.set_algo_fields(DEFAULT_ALGO, enabled=1)
    sched = Scheduler(tmp_db, FakeSupervisor(), fleet=FakeFleet({DEFAULT_ALGO: sup}))

    sched.tick_fleet(want=_window(AFTER_WARMUP))
    assert sup.starts == 0


# ── crash handling ───────────────────────────────────────────────────────────
def test_a_crashed_algo_is_restarted_inside_the_window(tmp_db):
    sup = FakeSupervisor()
    sup.state.last_exit_code = 2
    sched = _sched(tmp_db, [{"id": "alpha", "armed": True}], {"alpha": sup})

    sched.tick_fleet(want=_window(AFTER_WARMUP))
    assert sup.starts == 1
    assert any("down mid-session" in e["message"] for e in tmp_db.events(20))


def test_a_serial_crasher_is_given_up_on(tmp_db):
    from app.config import settings

    sup = FakeSupervisor()
    sup.state.last_exit_code = 2
    sup.state.restarts_this_session = settings.max_restarts_per_session
    sched = _sched(tmp_db, [{"id": "alpha", "armed": True}], {"alpha": sup})

    sched.tick_fleet(want=_window(AFTER_WARMUP))
    assert sup.starts == 0
    assert "not restarting again today" in sched.fleet_decisions["alpha"]


# ── wiring ───────────────────────────────────────────────────────────────────
def test_the_tick_drives_the_fleet_as_well_as_the_builtin(tmp_db, monkeypatch):
    """Without this call the whole feature is dead code."""
    sched = _sched(tmp_db, [], {})
    seen = []
    monkeypatch.setattr(sched, "tick_fleet", lambda want: seen.append(want))
    sched.set_enabled(True)
    sched.tick()
    assert seen, "tick() never reconciled the fleet"


def test_a_disarmed_scheduler_leaves_the_fleet_alone(tmp_db, monkeypatch):
    sched = _sched(tmp_db, [], {})
    seen = []
    monkeypatch.setattr(sched, "tick_fleet", lambda want: seen.append(want))
    sched.set_enabled(False)
    sched.tick()
    assert not seen


def test_a_fleet_failure_does_not_stop_the_builtin_being_reconciled(tmp_db, monkeypatch):
    sched = _sched(tmp_db, [], {})

    def boom(want):
        raise RuntimeError("fleet exploded")

    monkeypatch.setattr(sched, "tick_fleet", boom)
    sched.set_enabled(True)
    sched.tick()

    assert sched.last_decision, "the built-in half of the tick never ran"
    assert any("fleet reconcile failed" in e["message"] for e in tmp_db.events(20))


def test_the_application_context_connects_the_scheduler_to_the_fleet(tmp_path, monkeypatch):
    """deps.py builds the scheduler and the fleet separately, and the scheduler
    cannot be handed the fleet at construction time because the fleet needs the
    scheduler's supervisor. A missed assignment there is silent: every algorithm
    would simply never start in the morning."""
    from app import deps
    from app.config import settings

    monkeypatch.setattr(settings, "db_path", tmp_path / "wiring.db")
    context = deps.build_context()
    assert context.sched.fleet is context.fleet
