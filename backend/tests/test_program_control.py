"""The control file v13 programs read: halt while holding, sell now and keep running."""

from __future__ import annotations

import json
import time

from app import program_control
from tests.test_desk import auth, client  # noqa: F401  (fixtures)

V13 = ("import sys\nCONTROL_FILE = 'gk50k_control.json'\n"
       "def control_state(c):\n    return False, None\n"
       "if __name__ == '__main__':\n    live = '--live' in sys.argv or '--paper' in sys.argv\n")
V12 = "import sys\nif __name__ == '__main__':\n    live = '--live' in sys.argv or '--paper' in sys.argv\n"


def _upload(client, auth, name, src):  # noqa: F811
    return client.post("/api/algos", headers=auth, json={"name": name, "source": src}).json()["algo_id"]


def _holding(algo_id, sup):
    from app.deps import ctx
    from engine.clock import now_ist
    from shared.db import snapshot_key

    ctx().db.kv_set(snapshot_key(algo_id), {"ts": now_ist().replace(tzinfo=None).isoformat(timespec="seconds"),
                                            "engine": {"pid": sup.state.pid}, "position": {"tsym": "NIFTY-X-25500CE", "qty": 65}})


def test_a_v13_program_is_halted_while_it_holds_a_trade_without_being_stopped(client, auth):  # noqa: F811
    from app.deps import ctx

    algo_id = _upload(client, auth, "v13 probe", V13)
    assert next(a for a in client.get("/api/algos", headers=auth).json()["algos"] if a["id"] == algo_id)["control_file"] is True
    assert client.post(f"/api/algos/{algo_id}/start", headers=auth, json={"mode": "paper"}).status_code == 200
    sup = ctx().fleet.get(algo_id)
    _holding(algo_id, sup)
    r = client.post("/api/control/halt", headers=auth, json={"algo_id": algo_id})
    assert r.status_code == 200 and "keeps running" in r.json()["detail"]
    assert sup.state.running is True                                    # not stopped: the trade is still managed
    assert program_control.read(algo_id)["entries"] == "halt"
    r = client.post("/api/control/resume", headers=auth, json={"algo_id": algo_id})
    assert r.status_code == 200 and program_control.read(algo_id)["entries"] == "allow"


def test_sell_now_writes_a_fresh_one_time_instruction_and_needs_the_phrase(client, auth):  # noqa: F811
    from app.deps import ctx
    from app.routers.control import FLATTEN_PHRASE

    algo_id = _upload(client, auth, "v13 seller", V13)
    client.post(f"/api/algos/{algo_id}/start", headers=auth, json={"mode": "paper"})
    sup = ctx().fleet.get(algo_id)
    _holding(algo_id, sup)
    assert client.post("/api/control/sell-now", headers=auth, json={"algo_id": algo_id, "confirm": "yes"}).status_code == 400
    before = time.time()
    r = client.post("/api/control/sell-now", headers=auth, json={"algo_id": algo_id, "confirm": FLATTEN_PHRASE})
    assert r.status_code == 200 and "keeps running" in r.json()["detail"]
    ex = program_control.read(algo_id)["exit_now"]
    assert len(ex["id"]) == 12 and before - 1 <= ex["ts"] <= time.time() + 1
    first = ex["id"]
    client.post("/api/control/sell-now", headers=auth, json={"algo_id": algo_id, "confirm": FLATTEN_PHRASE})
    assert program_control.read(algo_id)["exit_now"]["id"] != first       # each press is its own instruction
    assert sup.state.running is True
    assert any(a["action"] == "risk.sell_now" for a in ctx().db.audit_tail(10))


def test_sell_now_is_refused_when_flat_and_for_an_older_program(client, auth):  # noqa: F811
    from app.deps import ctx
    from app.routers.control import FLATTEN_PHRASE
    from engine.clock import now_ist
    from shared.db import snapshot_key

    new = _upload(client, auth, "v13 flat", V13)
    client.post(f"/api/algos/{new}/start", headers=auth, json={"mode": "paper"})
    sup = ctx().fleet.get(new)
    ctx().db.kv_set(snapshot_key(new), {"ts": now_ist().replace(tzinfo=None).isoformat(timespec="seconds"),
                                        "engine": {"pid": sup.state.pid}, "position": None})
    r = client.post("/api/control/sell-now", headers=auth, json={"algo_id": new, "confirm": FLATTEN_PHRASE})
    assert r.status_code == 409 and "nothing to sell" in r.json()["detail"]
    assert "exit_now" not in program_control.read(new)

    old = _upload(client, auth, "v12 probe", V12)
    client.post(f"/api/algos/{old}/start", headers=auth, json={"mode": "paper"})
    _holding(old, ctx().fleet.get(old))
    r = client.post("/api/control/sell-now", headers=auth, json={"algo_id": old, "confirm": FLATTEN_PHRASE})
    assert r.status_code == 409 and "needs v13" in r.json()["detail"]
    # An older program is still halted the old way: refused while it holds a trade.
    r = client.post("/api/control/halt", headers=auth, json={"algo_id": old})
    assert r.status_code == 409 and "use Flatten" in r.json()["detail"]


def test_the_file_is_written_whole_and_a_bad_one_reads_as_nothing(client, auth):  # noqa: F811
    algo_id = _upload(client, auth, "v13 file", V13)
    program_control.set_entries(algo_id, True)
    path = program_control.path_for(algo_id)
    assert json.loads(path.read_text())["entries"] == "halt" and not path.with_suffix(".json.tmp").exists()
    path.write_text("{not json")
    assert program_control.read(algo_id) == {}
    program_control.clear(algo_id)
    assert not path.exists() and program_control.read(algo_id) == {}
    program_control.clear(algo_id)                                       # clearing nothing is fine


def test_a_restart_keeps_todays_halt_and_drops_yesterdays_and_any_sell_now(client, auth):  # noqa: F811
    algo_id = _upload(client, auth, "v13 restart", V13)
    program_control.set_entries(algo_id, True)
    program_control.exit_now(algo_id)
    program_control.start_of_run(algo_id)
    now = program_control.read(algo_id)
    assert now["entries"] == "halt" and "exit_now" not in now            # a crash restart does not undo the halt
    data = program_control.read(algo_id)
    data["entries_ts"] = time.time() - 30 * 3600                         # set yesterday
    program_control.path_for(algo_id).write_text(json.dumps(data))
    program_control.start_of_run(algo_id)
    assert program_control.read(algo_id) == {}                           # a new day starts with entries allowed
