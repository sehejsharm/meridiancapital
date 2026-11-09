"""Moving real money from one algorithm to another in one step (POST /api/algos/{id}/switch).

Only one algorithm may trade real money on the account, so changing which one
does — in a normal session or Muhurat trading — means stopping the one that
trades (a program sells what it holds on its way out) and starting the other on
real money. The switch does both, in that order, and never leaves two live.
"""

from __future__ import annotations

import pytest

from tests.test_desk import auth, client  # noqa: F401  (fixtures)

PROGRAM = 'import sys\nMODES = ("--paper", "--live")\nif __name__ == "__main__":\n    print(sys.argv)\n'


@pytest.fixture
def desk(client, auth):  # noqa: F811
    from app.deps import ctx

    c = ctx()
    for algo_id, name in (("og", "ganesh_kavach_og_real (3)"), ("v9", "ganesh_kavach_v9")):
        c.db.upsert_algo(algo_id, name, kind="uploaded")
        vid = c.db.add_version(algo_id, PROGRAM, "s" * 64, "sehej")
        c.db.set_algo_fields(algo_id, active_version=vid, mode="paper", enabled=0)
    c.fleet.sync()
    return client, auth, c


def _start(c, algo_id, mode):
    c.fleet.set_mode(algo_id, mode)
    assert c.fleet.start(algo_id)["ok"]
    c.db.set_algo_fields(algo_id, enabled=1)


def test_real_money_moves_to_the_chosen_algorithm(desk):
    http, hdrs, c = desk
    _start(c, "og", "live")
    r = http.post("/api/algos/v9/switch", json={"confirm": True}, headers=hdrs)
    assert r.status_code == 200, r.text
    assert r.json()["stopped"] == ["og"]
    og, v9 = c.fleet.get("og"), c.fleet.get("v9")
    assert not og.state.running and c.db.algo("og")["enabled"] == 0, "the old one is stopped and disarmed"
    assert v9.state.running and v9.state.mode == "live" and c.db.algo("v9")["enabled"] == 1
    assert c.db.algo("v9")["mode"] == "live", "it stays on real money for the next open"
    msgs = [e["message"] for e in c.db.events(10)]
    assert any("SWITCH: 'ganesh_kavach_v9' started on REAL MONEY" in m for m in msgs)


def test_it_asks_first_and_says_what_it_will_stop(desk):
    http, hdrs, c = desk
    _start(c, "og", "live")
    r = http.post("/api/algos/v9/switch", json={}, headers=hdrs)
    assert r.status_code == 409
    assert "stops ganesh_kavach_og_real (3)" in r.json()["detail"] and "sells" in r.json()["detail"]
    assert c.fleet.get("og").state.running and not c.fleet.get("v9").state.running, "nothing changed"


def test_with_nothing_live_it_simply_starts_on_real_money(desk):
    http, hdrs, c = desk
    r = http.post("/api/algos/v9/switch", json={"confirm": True}, headers=hdrs)
    assert r.status_code == 200 and r.json()["stopped"] == []
    assert c.fleet.get("v9").state.mode == "live"


def test_a_paper_run_is_restarted_on_real_money(desk):
    http, hdrs, c = desk
    _start(c, "v9", "paper")
    r = http.post("/api/algos/v9/switch", json={"confirm": True}, headers=hdrs)
    assert r.status_code == 200
    v9 = c.fleet.get("v9")
    assert v9.state.running and v9.state.mode == "live"
    assert not v9.state.manual_override, "the restart is not an operator stop"


def test_switching_to_the_one_already_live_is_refused(desk):
    http, hdrs, c = desk
    _start(c, "v9", "live")
    r = http.post("/api/algos/v9/switch", json={"confirm": True}, headers=hdrs)
    assert r.status_code == 409 and "already trading real money" in r.json()["detail"]


def test_if_the_old_one_will_not_stop_nothing_is_switched(desk, monkeypatch):
    http, hdrs, c = desk
    _start(c, "og", "live")
    monkeypatch.setattr(c.fleet.get("og"), "stop", lambda **k: {"ok": False, "detail": "position still open"})
    r = http.post("/api/algos/v9/switch", json={"confirm": True}, headers=hdrs)
    assert r.status_code == 409 and "nothing was switched" in r.json()["detail"]
    assert not c.fleet.get("v9").state.running, "never two live algorithms"


def test_if_the_new_one_will_not_start_it_says_nothing_is_live(desk, monkeypatch):
    http, hdrs, c = desk
    _start(c, "og", "live")
    monkeypatch.setattr(c.fleet.get("v9"), "start", lambda trigger="": {"ok": False, "detail": "login failed"})
    r = http.post("/api/algos/v9/switch", json={"confirm": True}, headers=hdrs)
    assert r.status_code == 409
    assert "login failed" in r.json()["detail"] and "nothing is trading real money" in r.json()["detail"]
