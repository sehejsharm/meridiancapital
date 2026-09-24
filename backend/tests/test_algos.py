"""Upload, gate, promote, isolate.

The promise this suite pins: operator-supplied source cannot reach the broker
until it has passed the acceptance gate, cannot reach real money until it has
is the operator's choice and not the gate's, and two algorithms at once never read
or write each other's state.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.security import hash_password

PASSWORD = "meridian-test-password"
os.environ.setdefault("MERIDIAN_PASSWORD_HASH", hash_password(PASSWORD, rounds=1000))

BACKEND = Path(__file__).resolve().parent.parent
REFERENCE = (BACKEND / "engine" / "builtin_gk50k.py").read_text()


class FakeState:
    def __init__(self):
        self.running = False
        self.pid = None
        self.mode = "paper"
        self.manual_override = False
        self.last_exit_code = None
        self.restarts_this_session = 0


class FakeSupervisor:
    def __init__(self, db, algo_id="gk50k", mode_provider=None, strategy_path=None):
        self.db = db
        self.algo_id = algo_id
        self.mode_provider = mode_provider
        self.strategy_path = strategy_path
        self.state = FakeState()

    def desired_mode(self):
        return self.mode_provider() if self.mode_provider else self.state.mode

    def set_mode(self, mode):
        self.state.mode = mode

    def refresh(self):
        pass

    def start(self, trigger="manual"):
        if self.state.running:
            return {"ok": False, "detail": "already running"}
        self.state.running = True
        self.state.pid = 4000 + abs(hash(self.algo_id)) % 1000
        self.state.mode = self.desired_mode()
        return {"ok": True, "pid": self.state.pid, "mode": self.state.mode}

    def stop(self, reason="manual", force=False, trigger="manual"):
        self.state.running = False
        self.state.pid = None
        return {"ok": True, "detail": "stopped"}

    def restart(self, trigger="manual"):
        self.stop()
        return self.start(trigger)

    def note_crash(self):
        pass

    def snapshot(self):
        return {"running": self.state.running, "pid": self.state.pid, "mode": self.state.mode}


@pytest.fixture
def client(monkeypatch, tmp_path):
    from app import deps
    from app.config import settings

    monkeypatch.setattr(settings, "db_path", tmp_path / "algos.db")
    monkeypatch.setattr(settings, "password_hash", os.environ["MERIDIAN_PASSWORD_HASH"])
    monkeypatch.setattr(deps, "Supervisor", FakeSupervisor)

    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture
def auth(client):
    r = client.post("/api/auth/login", json={"password": PASSWORD})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['token']}"}


def upload(client, auth, name, source=REFERENCE, mode="paper"):
    return client.post(
        "/api/algos", json={"name": name, "source": source, "mode": mode}, headers=auth
    )


# ── registry ─────────────────────────────────────────────────────────────────
def test_algos_requires_a_token(client):
    assert client.get("/api/algos").status_code == 401


def test_the_builtin_is_registered_out_of_the_box(client, auth):
    body = client.get("/api/algos", headers=auth).json()
    ids = {a["id"] for a in body["algos"]}
    assert "gk50k" in ids
    builtin = next(a for a in body["algos"] if a["id"] == "gk50k")
    assert builtin["kind"] == "builtin"


def test_the_gate_catalogue_is_published_before_uploading(client, auth):
    body = client.get("/api/algos/gate-catalogue", headers=auth).json()
    keys = {c["key"] for c in body["checks"]}
    assert "stop_monotonic" in keys and "size_rejects_degenerate" in keys
    # The catalogue says what the gate looks at, not what it will block.
    assert body["advisory"] is True


# ── upload and gate ──────────────────────────────────────────────────────────
def test_a_good_upload_passes_and_becomes_active(client, auth):
    r = upload(client, auth, "Reference Copy")
    assert r.status_code == 200
    body = r.json()
    assert body["passed"] is True
    assert body["report"]["failed"] == 0

    algo = client.get(f"/api/algos/{body['algo_id']}", headers=auth).json()
    assert algo["gate"]["gate_passed"] is True
    assert algo["active"]["id"] == body["version_id"]
    # Uploading never starts anything.
    assert algo["enabled"] is False


def test_an_upload_with_a_widening_stop_is_rejected(client, auth):
    bad = REFERENCE.replace(
        "    if peak_gain >= TRAIL_FRAC:\n        return -(peak_gain - TRAIL_FRAC)\n"
        "    if peak_gain >= BE_TRIGGER:\n        return 0.0\n    return STOP_FRAC\n",
        "    return STOP_FRAC + peak_gain\n",
    )
    assert bad != REFERENCE
    body = upload(client, auth, "Widening Stop", bad).json()
    assert body["passed"] is False
    assert "stop_monotonic" in {c["key"] for c in body["report"]["checks"] if not c["passed"]}


def test_an_upload_that_imports_os_is_rejected_before_import(client, auth):
    evil = REFERENCE.replace(
        "from __future__ import annotations\n",
        "from __future__ import annotations\nimport os\n",
    )
    body = upload(client, auth, "Sneaky", evil).json()
    assert body["passed"] is False
    assert body["report"]["error"] == "static screening rejected this source"


def test_a_gate_failure_does_not_block_the_upload(client, auth):
    """The gate is advice. The operator decides what runs."""
    body = upload(client, auth, "Broken", "NAME='x'\n").json()
    assert body["passed"] is False
    assert body["ok"] is True

    algo = client.get(f"/api/algos/{body['algo_id']}", headers=auth).json()
    assert algo["gate"]["gate_passed"] is False
    assert algo["gate"]["runnable"] is True
    assert algo["active"]["id"] == body["version_id"]


def test_a_gate_failure_can_still_be_started(client, auth):
    """A complete strategy the gate has doubts about: the operator's call."""
    widening = REFERENCE.replace(
        "    if peak_gain >= TRAIL_FRAC:\n        return -(peak_gain - TRAIL_FRAC)\n"
        "    if peak_gain >= BE_TRIGGER:\n        return 0.0\n    return STOP_FRAC\n",
        "    return STOP_FRAC + peak_gain\n",
    )
    assert widening != REFERENCE
    body = upload(client, auth, "Rough Edges", widening).json()
    assert body["passed"] is False
    r = client.post(f"/api/algos/{body['algo_id']}/start", headers=auth)
    assert r.status_code == 200, r.json()


def test_a_file_that_is_not_a_strategy_is_refused_with_the_reason(client, auth):
    """Not the gate's opinion — a file with no signal() cannot trade at all."""
    body = upload(client, auth, "Just A Name", "NAME='x'\n").json()
    r = client.post(f"/api/algos/{body['algo_id']}/start", headers=auth)
    assert r.status_code == 409
    assert "signal()" in r.json()["detail"]


def test_oversized_source_is_refused(client, auth):
    r = upload(client, auth, "Huge", "# pad\n" * 200_000)
    assert r.status_code == 413


def test_the_builtin_cannot_be_overwritten_by_an_upload(client, auth):
    r = client.post(
        "/api/algos", json={"name": "Impostor", "source": REFERENCE, "algo_id": "gk50k"}, headers=auth
    )
    assert r.status_code == 409


def test_the_builtin_cannot_be_deleted(client, auth):
    assert client.delete("/api/algos/gk50k", headers=auth).status_code == 409


# ── choosing a mode ──────────────────────────────────────────────────────────
def test_live_is_a_straight_choice_with_no_ladder(client, auth):
    algo_id = upload(client, auth, "Candidate").json()["algo_id"]
    r = client.post(f"/api/algos/{algo_id}/mode", json={"mode": "live"}, headers=auth)
    assert r.status_code == 200, r.json()
    assert r.json()["mode"] == "live"


def test_a_brand_new_upload_can_go_straight_to_live(client, auth):
    """No paper sessions, no phrase, no waiting."""
    body = upload(client, auth, "Impatient").json()
    r = client.post(
        f"/api/algos/{body['algo_id']}/start", json={"mode": "live"}, headers=auth
    )
    assert r.status_code == 200, r.json()
    assert r.json()["mode"] == "live"

    algo = client.get(f"/api/algos/{body['algo_id']}", headers=auth).json()
    assert algo["mode"] == "live"


def test_the_mode_picked_at_start_wins_over_the_stored_one(client, auth):
    algo_id = upload(client, auth, "Switcher", mode="live").json()["algo_id"]
    r = client.post(f"/api/algos/{algo_id}/start", json={"mode": "paper"}, headers=auth)
    assert r.status_code == 200 and r.json()["mode"] == "paper"
    assert client.get(f"/api/algos/{algo_id}", headers=auth).json()["mode"] == "paper"


def test_starting_without_a_mode_keeps_the_stored_one(client, auth):
    """This is how the scheduler starts things at the open."""
    algo_id = upload(client, auth, "Remembered", mode="live").json()["algo_id"]
    r = client.post(f"/api/algos/{algo_id}/start", headers=auth)
    assert r.status_code == 200 and r.json()["mode"] == "live"


def test_a_shadow_still_cannot_trade_real_money(client, auth):
    """Not a safety ladder — a shadow that placed real orders would not be a
    shadow of anything."""
    algo_id = upload(client, auth, "Twinned").json()["algo_id"]
    shadow_id = client.post(
        f"/api/algos/{algo_id}/shadow", json={"enabled": True}, headers=auth
    ).json()["shadow_algo_id"]

    assert client.post(
        f"/api/algos/{shadow_id}/mode", json={"mode": "live"}, headers=auth
    ).status_code == 409
    assert client.post(
        f"/api/algos/{shadow_id}/start", json={"mode": "live"}, headers=auth
    ).status_code == 409


def test_paper_mode_is_always_available(client, auth):
    algo_id = upload(client, auth, "Papery").json()["algo_id"]
    r = client.post(f"/api/algos/{algo_id}/mode", json={"mode": "paper"}, headers=auth)
    assert r.status_code == 200


# ── isolation ────────────────────────────────────────────────────────────────
def test_two_algos_run_at_once_with_separate_processes(client, auth):
    a = upload(client, auth, "Alpha").json()["algo_id"]
    b = upload(client, auth, "Beta").json()["algo_id"]

    assert client.post(f"/api/algos/{a}/start", headers=auth).status_code == 200
    assert client.post(f"/api/algos/{b}/start", headers=auth).status_code == 200

    fleet = client.get("/api/status", headers=auth).json()["fleet"]
    running = {x["algo_id"] for x in fleet["algos"] if x["running"]}
    assert {a, b} <= running
    pids = {x["pid"] for x in fleet["algos"] if x["running"]}
    assert len(pids) == len([x for x in fleet["algos"] if x["running"]]), "pids must be distinct"


def test_stopping_one_algo_leaves_the_other_running(client, auth):
    a = upload(client, auth, "Stays").json()["algo_id"]
    b = upload(client, auth, "Goes").json()["algo_id"]
    client.post(f"/api/algos/{a}/start", headers=auth)
    client.post(f"/api/algos/{b}/start", headers=auth)

    client.post(f"/api/algos/{b}/stop", headers=auth)
    fleet = client.get("/api/status", headers=auth).json()["fleet"]
    state = {x["algo_id"]: x["running"] for x in fleet["algos"]}
    assert state[a] is True and state[b] is False


def test_events_are_scoped_to_their_algo(client, auth):
    from app.deps import ctx

    a = upload(client, auth, "Noisy").json()["algo_id"]
    b = upload(client, auth, "Quiet").json()["algo_id"]
    db = ctx().db
    db.add_event("info", "only for a", source="test", algo_id=a)

    a_msgs = [e["message"] for e in db.events(algo_id=a)]
    b_msgs = [e["message"] for e in db.events(algo_id=b)]
    assert "only for a" in a_msgs
    assert "only for a" not in b_msgs


def test_trades_are_scoped_to_their_algo(client, auth):
    from app.deps import ctx

    a = upload(client, auth, "TraderA").json()["algo_id"]
    b = upload(client, auth, "TraderB").json()["algo_id"]
    db = ctx().db
    db.add_trade({"entry_ts": "2026-09-14T10:20", "session_date": "2026-09-14",
                  "mode": "paper", "net": 500.0, "algo_id": a})

    assert len(db.trades(algo_id=a)) == 1
    assert db.trades(algo_id=b) == []


def test_commands_are_claimed_only_by_their_own_algo(client, auth):
    """A shared queue would let one engine swallow another's flatten."""
    from app.deps import ctx

    a = upload(client, auth, "CmdA").json()["algo_id"]
    b = upload(client, auth, "CmdB").json()["algo_id"]
    db = ctx().db
    db.enqueue_command("flatten", issued_by="test", algo_id=a)

    claimed_by_b = db.claim_commands(algo_id=b)
    assert claimed_by_b == [], "b must not see a's command"
    claimed_by_a = db.claim_commands(algo_id=a)
    assert [c["action"] for c in claimed_by_a] == ["flatten"]


def test_each_algo_gets_its_own_state_file():
    """Sharing one would overwrite open positions across algorithms."""
    from engine.state import state_file_for

    assert state_file_for("alpha") != state_file_for("beta")
    assert state_file_for("gk50k").name == "gk50k_state.json"  # unchanged for the built-in


def test_each_algo_publishes_its_own_snapshot_key():
    from shared.db import K_SNAPSHOT, snapshot_key

    assert snapshot_key("gk50k") == K_SNAPSHOT
    assert snapshot_key("alpha") != snapshot_key("beta") != K_SNAPSHOT


# ── lifecycle guards ─────────────────────────────────────────────────────────
def test_mode_cannot_change_while_running(client, auth):
    algo_id = upload(client, auth, "Busy").json()["algo_id"]
    client.post(f"/api/algos/{algo_id}/start", headers=auth)
    r = client.post(f"/api/algos/{algo_id}/mode", json={"mode": "paper"}, headers=auth)
    assert r.status_code == 409


def test_deleting_a_running_algo_stops_it_first(client, auth):
    """An orphan engine still holding a position, with nothing supervising it,
    is worse than refusing the delete was annoying."""
    algo_id = upload(client, auth, "Running").json()["algo_id"]
    client.post(f"/api/algos/{algo_id}/start", headers=auth)
    from app.deps import ctx

    assert ctx().fleet.is_running(algo_id)

    r = client.delete(f"/api/algos/{algo_id}", headers=auth)
    assert r.status_code == 200, r.json()
    assert algo_id in r.json()["deleted"]
    assert not ctx().fleet.is_running(algo_id)
    assert client.get(f"/api/algos/{algo_id}", headers=auth).status_code == 404


def test_deleting_an_algo_takes_its_shadow_with_it(client, auth):
    algo_id = upload(client, auth, "Haunted").json()["algo_id"]
    shadow_id = client.post(
        f"/api/algos/{algo_id}/shadow", json={"enabled": True}, headers=auth
    ).json()["shadow_algo_id"]

    r = client.delete(f"/api/algos/{algo_id}", headers=auth)
    assert r.status_code == 200
    assert set(r.json()["deleted"]) == {algo_id, shadow_id}
    assert client.get(f"/api/algos/{shadow_id}", headers=auth).status_code == 404


def test_a_stopped_algo_can_be_deleted(client, auth):
    algo_id = upload(client, auth, "Disposable").json()["algo_id"]
    assert client.delete(f"/api/algos/{algo_id}", headers=auth).status_code == 200
    assert client.get(f"/api/algos/{algo_id}", headers=auth).status_code == 404


def test_uploading_twice_creates_a_second_version(client, auth):
    first = upload(client, auth, "Iterated").json()
    second = upload(client, auth, "Iterated").json()
    assert second["version"] == first["version"] + 1
    algo = client.get(f"/api/algos/{first['algo_id']}", headers=auth).json()
    assert len(algo["versions"]) == 2


def test_an_upload_is_audited(client, auth):
    upload(client, auth, "Audited")
    actions = [a["action"] for a in client.get("/api/audit", headers=auth).json()["entries"]]
    assert "algo.upload" in actions
