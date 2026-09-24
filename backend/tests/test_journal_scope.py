"""The journal, filed by algorithm.

With several algorithms on one desk, the journal is only useful if each line
belongs to the algorithm that wrote it. These tests pin three things: desk-wide
events are no longer filed under the built-in, every journal endpoint can be
narrowed to one algorithm, and one engine stopping never expires the command
queue of another — an emergency flatten queued for a live engine must survive a
paper engine shutting down next to it.
"""

from __future__ import annotations

from app.config import settings
from app.supervisor import Supervisor
from shared.db import DEFAULT_ALGO, SYSTEM_ALGO

from tests.test_algos import auth, client, upload  # noqa: F401  (fixtures)
from tests.test_supervisor import _fake_python, _local, _wait_for_exit


# ── the command queue ────────────────────────────────────────────────────────
def test_expiring_one_algos_commands_leaves_the_others(tmp_db):
    tmp_db.enqueue_command("flatten", algo_id="paper-one")
    live_cmd = tmp_db.enqueue_command("flatten", algo_id="live-one")

    assert tmp_db.expire_stale_commands("paper-one") == 1

    claimed = tmp_db.claim_commands("live-one")
    assert [c["id"] for c in claimed] == [live_cmd]


def test_a_claimed_command_of_another_algo_is_not_expired(tmp_db):
    """The live engine may be mid-exit on a claimed flatten when a neighbour stops."""
    cmd = tmp_db.enqueue_command("flatten", algo_id="live-one")
    tmp_db.claim_commands("live-one")

    tmp_db.expire_stale_commands("paper-one")

    row = next(r for r in tmp_db.recent_commands() if r["id"] == cmd)
    assert row["status"] == "claimed"


def test_unscoped_expiry_still_clears_everything(tmp_db):
    tmp_db.enqueue_command("flatten", algo_id="a")
    tmp_db.enqueue_command("flatten", algo_id="b")
    assert tmp_db.expire_stale_commands() == 2


def test_an_engine_stopping_keeps_its_neighbours_flatten(tmp_db, tmp_path, monkeypatch):
    """Through a real spawn: the supervisor's own stop path is what used to do it."""
    monkeypatch.setattr(settings, "python_bin", str(_fake_python(tmp_path, "exit 0\n")))
    queued = tmp_db.enqueue_command("flatten", issued_by="nuke:op", algo_id="live-one")

    sup = _local(Supervisor(tmp_db, algo_id="paper-one"), tmp_path)
    assert sup.start()["ok"] is True
    assert _wait_for_exit(sup)

    assert [c["id"] for c in tmp_db.claim_commands("live-one")] == [queued]


# ── events ───────────────────────────────────────────────────────────────────
def test_an_untagged_event_is_a_desk_event_not_the_builtins(tmp_db):
    tmp_db.add_event("info", "API started", source="api")
    assert [e["message"] for e in tmp_db.events(algo_id=SYSTEM_ALGO)] == ["API started"]
    assert tmp_db.events(algo_id=DEFAULT_ALGO) == []


def test_adopting_an_engine_is_filed_under_that_engine(tmp_db, tmp_path, monkeypatch):
    import app.supervisor as supervisor_mod

    pidfile = tmp_path / "engine-adoptee.pid"
    pidfile.write_text("1")
    monkeypatch.setattr(supervisor_mod, "pidfile_for", lambda algo_id: pidfile)
    monkeypatch.setattr(supervisor_mod, "_alive", lambda pid: True)
    monkeypatch.setattr(supervisor_mod, "_is_engine", lambda pid: True)

    Supervisor(tmp_db, algo_id="adoptee")

    adopted = [e for e in tmp_db.events(algo_id="adoptee") if "adopted" in e["message"]]
    assert adopted, "the adoption event was not filed under the adopted algorithm"


# ── the journal endpoints ────────────────────────────────────────────────────
def test_events_endpoint_filters_by_algo(client, auth):  # noqa: F811
    from app.deps import ctx

    a = upload(client, auth, "Journal A").json()["algo_id"]
    ctx().db.add_event("info", "a's line", source="engine", algo_id=a)
    ctx().db.add_event("info", "builtin's line", source="engine", algo_id=DEFAULT_ALGO)

    only_a = client.get(f"/api/events?algo={a}", headers=auth).json()["events"]
    assert {e["message"] for e in only_a} >= {"a's line"}
    assert "builtin's line" not in {e["message"] for e in only_a}
    assert all(e["algo_id"] == a for e in only_a)

    everything = client.get("/api/events", headers=auth).json()["events"]
    assert {"a's line", "builtin's line"} <= {e["message"] for e in everything}


def test_desk_events_land_under_system(client, auth):  # noqa: F811
    system = client.get(f"/api/events?algo={SYSTEM_ALGO}", headers=auth).json()["events"]
    assert any(e["message"].startswith("API v") for e in system), "API start event not filed as system"
    builtin = client.get(f"/api/events?algo={DEFAULT_ALGO}", headers=auth).json()["events"]
    assert not any(e["message"].startswith("API v") for e in builtin)


def test_runs_and_commands_filter_by_algo(client, auth):  # noqa: F811
    from app.deps import ctx

    db = ctx().db
    db.start_run(111, "paper", "manual", algo_id="alpha")
    db.start_run(222, "live", "manual", algo_id="beta")
    db.enqueue_command("halt", algo_id="alpha")
    db.enqueue_command("flatten", algo_id="beta")

    runs = client.get("/api/runs?algo=beta", headers=auth).json()["runs"]
    assert [r["pid"] for r in runs] == [222]
    cmds = client.get("/api/commands?algo=alpha", headers=auth).json()["commands"]
    assert [c["action"] for c in cmds] == ["halt"]
    assert len(client.get("/api/runs", headers=auth).json()["runs"]) >= 2


def test_eod_report_per_algo_with_builtin_fallback(client, auth):  # noqa: F811
    from app.deps import ctx

    db = ctx().db
    db.kv_set("report:eod:latest", {"session_date": "2026-09-23", "net": 10})
    db.kv_set("report:eod:latest:alpha", {"session_date": "2026-09-24", "net": 99})

    alpha = client.get("/api/reports/eod?algo=alpha", headers=auth).json()["report"]
    assert alpha["net"] == 99
    # The built-in's reports from before the split are still found.
    builtin = client.get(f"/api/reports/eod?algo={DEFAULT_ALGO}", headers=auth).json()["report"]
    assert builtin["net"] == 10
    # Another algorithm with no report of its own does not borrow one.
    assert client.get("/api/reports/eod?algo=beta", headers=auth).json()["report"] is None


def test_system_is_a_reserved_algorithm_id(client, auth):  # noqa: F811
    r = upload(client, auth, "System")
    assert r.status_code == 400
    assert "reserved" in r.json()["detail"]


def test_removing_an_algorithm_is_filed_under_it(client, auth):  # noqa: F811
    from app.deps import ctx

    a = upload(client, auth, "Short Lived").json()["algo_id"]
    assert client.delete(f"/api/algos/{a}", headers=auth).status_code == 200
    removed = [e for e in ctx().db.events(algo_id=a) if "removed" in e["message"]]
    assert removed
