"""Upload, gate, promote, isolate.

The promise this suite pins: operator-supplied source cannot reach the broker
until it has passed the acceptance gate, cannot reach real money until it has
also served its paper sessions, and two algorithms running at once never read
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


def upload(client, auth, name, source=REFERENCE):
    return client.post("/api/algos", json={"name": name, "source": source}, headers=auth)


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
    assert body["paper_sessions_required"] == 5


# ── upload and gate ──────────────────────────────────────────────────────────
def test_a_good_upload_passes_and_is_paper_only(client, auth):
    r = upload(client, auth, "Reference Copy")
    assert r.status_code == 200
    body = r.json()
    assert body["passed"] is True
    assert body["report"]["failed"] == 0

    algo = client.get(f"/api/algos/{body['algo_id']}", headers=auth).json()
    assert algo["promotion"]["can_paper"] is True
    assert algo["promotion"]["can_live"] is False
    assert "5" in algo["promotion"]["live_blocker"]


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


def test_a_rejected_upload_cannot_be_started(client, auth):
    body = upload(client, auth, "Broken", "NAME='x'\n").json()
    assert body["passed"] is False
    r = client.post(f"/api/algos/{body['algo_id']}/start", headers=auth)
    assert r.status_code == 409


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


# ── promotion ────────────────────────────────────────────────────────────────
def test_live_mode_is_refused_until_the_paper_sessions_are_served(client, auth):
    algo_id = upload(client, auth, "Candidate").json()["algo_id"]
    r = client.post(
        f"/api/algos/{algo_id}/mode",
        json={"mode": "live", "confirm": "TRADE REAL MONEY"},
        headers=auth,
    )
    assert r.status_code == 409
    assert "paper sessions" in r.json()["detail"]


def test_live_mode_still_needs_the_phrase_once_cleared(client, auth):
    from app.deps import ctx
    from engine import promotion

    body = upload(client, auth, "Seasoned").json()
    for _ in range(promotion.PAPER_SESSIONS_REQUIRED):
        promotion.record_paper_session(ctx().db, body["version_id"])

    without = client.post(f"/api/algos/{body['algo_id']}/mode", json={"mode": "live"}, headers=auth)
    assert without.status_code == 400 and "TRADE REAL MONEY" in without.json()["detail"]

    with_phrase = client.post(
        f"/api/algos/{body['algo_id']}/mode",
        json={"mode": "live", "confirm": "TRADE REAL MONEY"},
        headers=auth,
    )
    assert with_phrase.status_code == 200 and with_phrase.json()["mode"] == "live"


def test_clean_paper_sessions_accumulate_then_promote(client, auth):
    from app.deps import ctx
    from engine import promotion

    version_id = upload(client, auth, "Grinder").json()["version_id"]
    for i in range(1, promotion.PAPER_SESSIONS_REQUIRED):
        out = promotion.record_paper_session(ctx().db, version_id)
        assert out["promoted_to_live_eligible"] is False, f"promoted early at session {i}"
    final = promotion.record_paper_session(ctx().db, version_id)
    assert final["promoted_to_live_eligible"] is True
    assert ctx().db.version(version_id)["status"] == promotion.STATUS_CLEARED


def test_paper_mode_needs_no_phrase(client, auth):
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


def test_a_running_algo_cannot_be_deleted(client, auth):
    algo_id = upload(client, auth, "Running").json()["algo_id"]
    client.post(f"/api/algos/{algo_id}/start", headers=auth)
    assert client.delete(f"/api/algos/{algo_id}", headers=auth).status_code == 409


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
