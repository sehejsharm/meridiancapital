"""The built-in algorithm (GANESH KAVACH 50K) is removed from the desk (7 Nov 2026).

Production runs with it retired: it is listed nowhere, every route answers for
it as for an algorithm that does not exist, nothing can start it, and the
scheduler stops it if it is somehow up. Its past trades stay in the record.
"""

from __future__ import annotations

from datetime import datetime

import pytest

import app.scheduler as scheduler_mod
from app.fleet import Fleet
from app.scheduler import Scheduler
from app.supervisor import DEFAULT_ALGO
from tests.test_autostart import AFTER_WARMUP
from tests.test_autostart import ModeSupervisor as _Mode


class ModeSupervisor(_Mode):
    def snapshot(self):
        return {"running": self.state.running, "pid": None, "mode": self.state.mode}


@pytest.fixture(autouse=True)
def _retired(monkeypatch):
    monkeypatch.setenv("MERIDIAN_BUILTIN_RETIRED", "1")


def _registry(db):
    db.upsert_algo(DEFAULT_ALGO, "GANESH KAVACH 50K", kind="builtin")
    db.set_algo_fields(DEFAULT_ALGO, enabled=1, mode="live")
    db.upsert_algo("og", "ganesh_kavach_og_real (3)", kind="uploaded")


def test_retired_is_the_default(tmp_db):
    assert tmp_db.builtin_retired()
    tmp_db.kv_set("builtin:retired", False)
    assert not tmp_db.builtin_retired(), "the database can still say otherwise"


def test_it_is_listed_nowhere_but_its_row_and_trades_stay(tmp_db):
    _registry(tmp_db)
    assert [a["id"] for a in tmp_db.algos()] == ["og"]
    assert DEFAULT_ALGO in [a["id"] for a in tmp_db.algos(include_retired=True)]
    tmp_db.add_trade({"entry_ts": "2026-10-01 10:00", "exit_ts": "2026-10-01 11:00", "session_date": "2026-10-01",
                      "mode": "live", "net": 500.0, "algo_id": DEFAULT_ALGO})
    assert tmp_db.trades(limit=10)[0]["algo_id"] == DEFAULT_ALGO, "its history is the account's history"


def test_the_fleet_has_no_supervisor_for_it_and_will_not_start_it(tmp_db):
    _registry(tmp_db)
    fleet = Fleet(tmp_db, supervisor_factory=ModeSupervisor)
    assert DEFAULT_ALGO not in fleet.all()
    assert [a["algo_id"] for a in fleet.overview()["algos"]] == ["og"]
    res = fleet.start(DEFAULT_ALGO)
    assert not res["ok"] and "removed" in res["detail"]


def _sched(tmp_db, monkeypatch, running: bool):
    _registry(tmp_db)
    fleet = Fleet(tmp_db, supervisor_factory=ModeSupervisor)
    builtin = ModeSupervisor(tmp_db)
    builtin.state.running = running
    sched = Scheduler(tmp_db, builtin, fleet=fleet)
    sched.set_enabled(True)
    monkeypatch.setattr(scheduler_mod, "now_ist", lambda: datetime.strptime(AFTER_WARMUP, "%Y-%m-%d %H:%M"))
    return sched, builtin


def test_the_scheduler_never_starts_it_even_armed_at_the_open(tmp_db, monkeypatch):
    sched, builtin = _sched(tmp_db, monkeypatch, running=False)
    sched.tick()
    assert builtin.starts == 0, "a retired built-in came up at the open"
    assert sched.last_decision == "in the session window"


def test_the_scheduler_stops_it_if_it_is_up(tmp_db, monkeypatch):
    sched, builtin = _sched(tmp_db, monkeypatch, running=True)
    sched.tick()
    assert builtin.stops == 1
    assert any("removed from the desk" in e["message"] for e in tmp_db.events(10))


def test_startup_disarms_it(tmp_path, monkeypatch):
    from app import deps
    from app.config import settings

    monkeypatch.setattr(settings, "db_path", tmp_path / "boot.db")
    context = deps.build_context()
    assert context.db.algo(DEFAULT_ALGO)["enabled"] == 0
    assert DEFAULT_ALGO not in context.fleet.all()


# ── the API ──────────────────────────────────────────────────────────────────
@pytest.fixture
def api(monkeypatch, tmp_path):
    """The desk's API built with the built-in retired, as production runs it."""
    import os

    from fastapi.testclient import TestClient

    from app import deps
    from app.config import settings
    from tests.test_algos import FakeSupervisor as FS
    from tests.test_desk import PASSWORD

    monkeypatch.setattr(settings, "db_path", tmp_path / "api.db")
    monkeypatch.setattr(settings, "password_hash", os.environ["MERIDIAN_PASSWORD_HASH"])
    monkeypatch.setattr(deps, "Supervisor", FS)
    from app.main import app

    with TestClient(app) as c:
        token = c.post("/api/auth/login", json={"password": PASSWORD}).json()["token"]
        yield c, {"Authorization": f"Bearer {token}"}


def test_the_api_answers_as_if_it_does_not_exist(api):
    c, auth = api
    assert DEFAULT_ALGO not in [a["id"] for a in c.get("/api/algos", headers=auth).json()["algos"]]
    for method, path in (("get", f"/api/algos/{DEFAULT_ALGO}"), ("post", f"/api/algos/{DEFAULT_ALGO}/start"),
                         ("post", f"/api/algos/{DEFAULT_ALGO}/stop"), ("delete", f"/api/algos/{DEFAULT_ALGO}")):
        r = getattr(c, method)(path, headers=auth, **({"json": {"mode": "live"}} if path.endswith("start") else {}))
        assert r.status_code == 404, (method, path, r.status_code)


def test_controls_ask_which_algorithm_instead_of_reaching_for_it(api):
    c, auth = api
    r = c.post("/api/control/halt", headers=auth)
    assert r.status_code == 409 and "choose the algorithm" in r.json()["detail"]
    r = c.post("/api/control/flatten", json={"confirm": "FLATTEN"}, headers=auth)
    assert r.status_code in (409, 422)
