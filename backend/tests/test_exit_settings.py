"""Exit settings as data: only a v14 program, only while it is stopped, only inside the program's own ranges."""

from __future__ import annotations

import json

from app import exit_settings
from tests.test_desk import auth, client  # noqa: F401  (fixtures)

CONSTANTS = ("STOP_FRAC = 0.45\nBE_TRIGGER = 0.20\nTRAIL_FRAC = 0.20\nLATE_TP_AFTER_MIN = 180\nLATE_TP_FRAC = 0.40\n"
             "RUNNER_AT = 0.90\nRUNNER_FRAC = 0.10\n")
MAIN = "import sys\nif __name__ == '__main__':\n    live = '--live' in sys.argv or '--paper' in sys.argv\n"
V14 = CONSTANTS + "EXIT_SETTINGS_FILE = 'gk50k_exit_settings.json'\ndef parse_exit_settings(t):\n    return None, None\n" + MAIN
V13 = CONSTANTS + MAIN
BUILT_IN = {"STOP_FRAC": 0.45, "BE_TRIGGER": 0.20, "TRAIL_FRAC": 0.20, "LATE_TP_AFTER_MIN": 180, "LATE_TP_FRAC": 0.40,
            "RUNNER_AT": 0.90, "RUNNER_FRAC": 0.10}


def _upload(client, auth, name, src):  # noqa: F811
    return client.post("/api/algos", headers=auth, json={"name": name, "source": src}).json()["algo_id"]


def test_the_checks_are_the_programs_own():
    assert exit_settings.check({"TRAIL_FRAC": 0.25}, BUILT_IN) == ({**BUILT_IN, "TRAIL_FRAC": 0.25}, None)
    assert exit_settings.check({"LATE_TP_AFTER_MIN": 210.0}, BUILT_IN)[0]["LATE_TP_AFTER_MIN"] == 210
    for bad in ({}, {"MAX_LOTS": 50}, {"STOP_FRAC": 0.95}, {"STOP_FRAC": 0.05}, {"STOP_FRAC": True}, {"STOP_FRAC": "0.4"},
                {"LATE_TP_AFTER_MIN": 200.5}, {"TRAIL_FRAC": 0.10, "RUNNER_FRAC": 0.20}, {"STOP_FRAC": float("nan")}):
        assert exit_settings.check(bad, BUILT_IN)[0] is None, bad
    assert exit_settings.check({"TRAIL_FRAC": 0.25}, {"STOP_FRAC": 0.45})[0] is None      # built-in values unknown: refuse


def test_a_change_needs_the_phrase_a_v14_program_and_a_stopped_program(client, auth):  # noqa: F811
    from app.deps import ctx

    old = _upload(client, auth, "v13 probe", V13)
    new = _upload(client, auth, "v14 probe", V14)
    assert client.get(f"/api/control/exit-settings?algo_id={new}").status_code == 401
    v = client.get(f"/api/control/exit-settings?algo_id={new}", headers=auth).json()
    assert v["supported"] is True and v["changed"] == [] and v["running"] is False
    trail = next(s for s in v["settings"] if s["key"] == "TRAIL_FRAC")
    assert (trail["built_in"], trail["next_start"], trail["low"], trail["high"]) == (0.2, 0.2, 0.1, 0.5)
    assert client.get(f"/api/control/exit-settings?algo_id={old}", headers=auth).json()["supported"] is False

    body = {"algo_id": new, "values": {"TRAIL_FRAC": 0.25, "BE_TRIGGER": 0.25}}
    assert client.post("/api/control/exit-settings", headers=auth, json=body).status_code == 400            # no phrase
    ok = {**body, "confirm": "CHANGE EXITS"}
    assert client.post("/api/control/exit-settings", headers=auth, json={**ok, "algo_id": old}).status_code == 409   # v13
    bad = client.post("/api/control/exit-settings", headers=auth, json={**ok, "values": {"STOP_FRAC": 0.9}})
    assert bad.status_code == 400 and "outside" in bad.json()["detail"]
    assert not exit_settings.path_for(new).exists()                                                           # nothing written yet

    assert client.post(f"/api/algos/{new}/start", headers=auth, json={"mode": "paper"}).status_code == 200
    running = client.post("/api/control/exit-settings", headers=auth, json=ok)
    assert running.status_code == 409 and "only when it starts" in running.json()["detail"]
    assert not exit_settings.path_for(new).exists()
    ctx().fleet.get(new).state.running = False

    done = client.post("/api/control/exit-settings", headers=auth, json=ok).json()
    assert done["changed"] == ["BE_TRIGGER", "TRAIL_FRAC"]
    assert json.loads(exit_settings.path_for(new).read_text()) == {"BE_TRIGGER": 0.25, "TRAIL_FRAC": 0.25}   # only what differs
    assert next(s for s in done["settings"] if s["key"] == "TRAIL_FRAC")["next_start"] == 0.25
    assert next(s for s in done["settings"] if s["key"] == "STOP_FRAC")["next_start"] == 0.45
    db = ctx().db
    assert any(a["action"] == "risk.exit_settings" for a in db.audit_tail(10))
    assert any("EXIT SETTINGS" in e["message"] and e["level"] == "critical" for e in db.events(limit=10))

    back = client.post("/api/control/exit-settings/reset", headers=auth, json={"algo_id": new}).json()
    assert back["changed"] == [] and not exit_settings.path_for(new).exists()


def test_setting_everything_back_to_built_in_removes_the_file_and_a_damaged_file_is_reported(client, auth):  # noqa: F811
    new = _upload(client, auth, "v14 probe", V14)
    ok = {"algo_id": new, "confirm": "CHANGE EXITS"}
    client.post("/api/control/exit-settings", headers=auth, json={**ok, "values": {"TRAIL_FRAC": 0.3}})
    assert exit_settings.path_for(new).exists()
    client.post("/api/control/exit-settings", headers=auth, json={**ok, "values": {"TRAIL_FRAC": 0.2}})
    assert not exit_settings.path_for(new).exists()
    exit_settings.path_for(new).write_text('{"STOP_FRAC": 0.99}')                # put there by hand, out of range
    v = client.get(f"/api/control/exit-settings?algo_id={new}", headers=auth).json()
    assert "outside" in v["problem"] and v["changed"] == []
    assert next(s for s in v["settings"] if s["key"] == "STOP_FRAC")["next_start"] == 0.45   # the program will ignore the file
