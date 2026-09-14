"""End-to-end API tests against the real app, with the supervisor stubbed.

The supervisor is the one component that must never be exercised for real in a
test — starting it would log into a live brokerage — so it is replaced with a
fake that records what it was asked to do.
"""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from app.security import hash_password

PASSWORD = "meridian-test-password"
os.environ["MERIDIAN_PASSWORD_HASH"] = hash_password(PASSWORD, rounds=1000)


class FakeSupervisorState:
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
        self.state = FakeSupervisorState()
        self.calls: list[tuple] = []

    def desired_mode(self):
        return self.state.mode

    def set_mode(self, mode):
        self.state.mode = mode

    def refresh(self):
        pass

    def start(self, trigger="manual"):
        self.calls.append(("start", trigger))
        if self.state.running:
            return {"ok": False, "detail": "engine already running"}
        self.state.running = True
        self.state.pid = 1234
        return {"ok": True, "pid": 1234, "mode": self.state.mode}

    def stop(self, reason="manual", force=False, trigger="manual"):
        self.calls.append(("stop", reason, force))
        self.state.running = False
        self.state.pid = None
        return {"ok": True, "detail": "stopped"}

    def restart(self, trigger="manual"):
        self.stop(force=True)
        return self.start(trigger)

    def note_crash(self):
        self.state.restarts_this_session += 1

    def snapshot(self):
        return {"running": self.state.running, "pid": self.state.pid, "mode": self.state.mode}

    def refresh_state(self):
        pass


@pytest.fixture
def client(monkeypatch, tmp_path):
    from app import deps
    from app.config import settings

    monkeypatch.setattr(settings, "db_path", tmp_path / "api.db")
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


# ── auth ─────────────────────────────────────────────────────────────────────
def test_health_needs_no_token(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["ok"] is True


def test_health_leaks_no_trading_data(client):
    body = r"{}".join([]) or client.get("/health").text
    for forbidden in ("equity", "position", "strike", "premium"):
        assert forbidden not in body.lower()


def test_login_with_wrong_password_is_rejected(client):
    assert client.post("/api/auth/login", json={"password": "nope"}).status_code == 401


def test_status_without_a_token_is_rejected(client):
    assert client.get("/api/status").status_code == 401


def test_status_with_a_garbage_token_is_rejected(client):
    r = client.get("/api/status", headers={"Authorization": "Bearer garbage"})
    assert r.status_code == 401


def test_status_with_a_valid_token_succeeds(client, auth):
    r = client.get("/api/status", headers=auth)
    assert r.status_code == 200
    assert r.json()["fund"] == "Meridian Capital"


def test_me_returns_the_operator(client, auth):
    assert client.get("/api/auth/me", headers=auth).json()["operator"]


def test_ws_ticket_is_scoped_to_ws(client, auth):
    from app.security import authenticate_ws_ticket

    ticket = client.post("/api/auth/ws-ticket", headers=auth).json()["ticket"]
    assert authenticate_ws_ticket(ticket).scope == "ws"


def test_security_headers_are_present(client):
    h = client.get("/health").headers
    assert h["X-Frame-Options"] == "DENY"
    assert h["X-Content-Type-Options"] == "nosniff"
    assert h["Cache-Control"] == "no-store"


# ── control surface ──────────────────────────────────────────────────────────
def test_engine_start_and_stop(client, auth):
    assert client.post("/api/control/engine/start", headers=auth).json()["ok"]
    assert client.get("/api/status", headers=auth).json()["engine"]["running"] is True
    assert client.post("/api/control/engine/stop", json={}, headers=auth).json()["ok"]
    assert client.get("/api/status", headers=auth).json()["engine"]["running"] is False


def test_starting_a_running_engine_conflicts(client, auth):
    client.post("/api/control/engine/start", headers=auth)
    assert client.post("/api/control/engine/start", headers=auth).status_code == 409


def test_manual_stop_blocks_the_scheduler_from_restarting(client, auth):
    client.post("/api/control/engine/start", headers=auth)
    client.post("/api/control/engine/stop", json={}, headers=auth)
    assert client.get("/api/status", headers=auth).json()["schedule"]["manual_override"] is True


def test_switching_to_live_requires_the_confirmation_phrase(client, auth):
    r = client.post("/api/control/mode", json={"mode": "live"}, headers=auth)
    assert r.status_code == 400
    assert "GO LIVE" in r.json()["detail"]


def test_switching_to_live_with_the_phrase_succeeds(client, auth):
    r = client.post("/api/control/mode", json={"mode": "live", "confirm": "GO LIVE"}, headers=auth)
    assert r.status_code == 200 and r.json()["mode"] == "live"


def test_mode_cannot_change_while_the_engine_runs(client, auth):
    client.post("/api/control/engine/start", headers=auth)
    r = client.post("/api/control/mode", json={"mode": "live", "confirm": "GO LIVE"}, headers=auth)
    assert r.status_code == 409


def test_invalid_mode_is_rejected(client, auth):
    assert client.post("/api/control/mode", json={"mode": "yolo"}, headers=auth).status_code == 422


def test_flatten_requires_the_confirmation_phrase(client, auth):
    client.post("/api/control/engine/start", headers=auth)
    assert client.post("/api/control/flatten", json={}, headers=auth).status_code == 400


def test_flatten_queues_a_command_for_the_engine(client, auth):
    client.post("/api/control/engine/start", headers=auth)
    r = client.post("/api/control/flatten", json={"confirm": "FLATTEN"}, headers=auth)
    assert r.status_code == 200
    actions = [c["action"] for c in client.get("/api/commands", headers=auth).json()["commands"]]
    assert "flatten" in actions


def test_flatten_without_a_running_engine_is_refused(client, auth):
    r = client.post("/api/control/flatten", json={"confirm": "FLATTEN"}, headers=auth)
    assert r.status_code == 409
    assert "Angel One app" in r.json()["detail"]


def test_halt_queues_a_command(client, auth):
    client.post("/api/control/engine/start", headers=auth)
    assert client.post("/api/control/halt", headers=auth).json()["ok"]
    actions = [c["action"] for c in client.get("/api/commands", headers=auth).json()["commands"]]
    assert "halt" in actions


def test_halt_without_a_running_engine_is_refused(client, auth):
    assert client.post("/api/control/halt", headers=auth).status_code == 409


def test_schedule_can_be_armed_and_disarmed(client, auth):
    assert client.post("/api/control/schedule", json={"enabled": True}, headers=auth).json()["enabled"]
    r = client.post("/api/control/schedule", json={"enabled": False}, headers=auth)
    assert r.json()["enabled"] is False


def test_holidays_can_be_added_and_removed(client, auth):
    r = client.post("/api/control/holidays", json={"day": "2026-10-21", "label": "Diwali"}, headers=auth)
    assert r.json()["holidays"] == [{"day": "2026-10-21", "label": "Diwali"}]
    assert client.delete("/api/control/holidays/2026-10-21", headers=auth).json()["holidays"] == []


def test_bad_holiday_date_is_rejected(client, auth):
    r = client.post("/api/control/holidays", json={"day": "21-10-2026"}, headers=auth)
    assert r.status_code == 422


def test_control_endpoints_reject_anonymous_callers(client):
    assert client.post("/api/control/engine/start").status_code == 401
    assert client.post("/api/control/flatten", json={"confirm": "FLATTEN"}).status_code == 401


# ── data surface ─────────────────────────────────────────────────────────────
def test_trades_endpoint_returns_stats(client, auth):
    r = client.get("/api/trades", headers=auth)
    assert r.status_code == 200 and "stats" in r.json()


def test_equity_endpoint_returns_both_series(client, auth):
    body = client.get("/api/equity", headers=auth).json()
    assert "intraday" in body and "daily" in body


def test_events_endpoint_records_api_activity(client, auth):
    client.post("/api/control/schedule", json={"enabled": True}, headers=auth)
    messages = [e["message"] for e in client.get("/api/events", headers=auth).json()["events"]]
    assert any("auto start/stop" in m for m in messages)


def test_audit_log_records_control_actions(client, auth):
    client.post("/api/control/engine/start", headers=auth)
    actions = [e["action"] for e in client.get("/api/audit", headers=auth).json()["entries"]]
    assert "engine.start" in actions


def test_snapshot_endpoint_is_shaped_for_the_dashboard(client, auth):
    body = client.get("/api/snapshot", headers=auth).json()
    assert set(body) == {"snapshot", "status"}


# ── strategy tuning ──────────────────────────────────────────────────────────
def test_tuning_requires_a_token(client):
    assert client.get("/api/tuning").status_code == 401


def test_tuning_returns_schema_and_defaults(client, auth):
    body = client.get("/api/tuning", headers=auth).json()
    assert body["overrides"] == {} and body["changed"] == []
    keys = {p["key"] for p in body["params"]}
    assert {"STOP_FRAC", "TARGET_PTS", "ENTRY_START"} <= keys
    assert body["effective"]["STOP_FRAC"] == body["defaults"]["STOP_FRAC"]


def test_tuning_never_exposes_non_strategy_internals(client, auth):
    keys = {p["key"] for p in client.get("/api/tuning", headers=auth).json()["params"]}
    for internal in ("INDEX_TOKEN", "LOT_SIZE", "RATE_LIMITS", "DB_PATH", "NTP_SERVERS"):
        assert internal not in keys


def test_a_safer_change_saves_without_the_phrase(client, auth):
    r = client.post("/api/tuning", json={"values": {"STOP_FRAC": 0.30}}, headers=auth)
    assert r.status_code == 200
    assert r.json()["overrides"]["STOP_FRAC"] == 0.30
    assert r.json()["changed"] == ["STOP_FRAC"]


def test_a_riskier_change_is_refused_without_the_phrase(client, auth):
    r = client.post("/api/tuning", json={"values": {"STOP_FRAC": 0.70}}, headers=auth)
    assert r.status_code == 400 and "RETUNE" in r.json()["detail"]
    assert client.get("/api/tuning", headers=auth).json()["overrides"] == {}


def test_a_riskier_change_saves_with_the_phrase(client, auth):
    r = client.post(
        "/api/tuning", json={"values": {"STOP_FRAC": 0.70}, "confirm": "RETUNE"}, headers=auth
    )
    assert r.status_code == 200 and r.json()["overrides"]["STOP_FRAC"] == 0.70


def test_an_out_of_bounds_value_is_refused(client, auth):
    r = client.post("/api/tuning", json={"values": {"MAX_LOTS": 500}}, headers=auth)
    assert r.status_code == 400 and "between" in r.json()["detail"]


def test_an_incoherent_combination_is_refused(client, auth):
    r = client.post(
        "/api/tuning",
        json={"values": {"ENTRY_START": "14:00", "ENTRY_CUTOFF": "11:00"}, "confirm": "RETUNE"},
        headers=auth,
    )
    assert r.status_code == 400


def test_a_value_returned_to_its_default_stops_being_an_override(client, auth):
    client.post("/api/tuning", json={"values": {"STOP_FRAC": 0.30}}, headers=auth)
    defaults = client.get("/api/tuning", headers=auth).json()["defaults"]
    r = client.post("/api/tuning", json={"values": {"STOP_FRAC": defaults["STOP_FRAC"]}}, headers=auth)
    assert r.json()["overrides"] == {} and r.json()["changed"] == []


def test_reset_clears_every_override(client, auth):
    client.post("/api/tuning", json={"values": {"STOP_FRAC": 0.30}}, headers=auth)
    r = client.post("/api/tuning/reset", headers=auth)
    assert r.status_code == 200 and r.json()["overrides"] == {}


def test_saving_while_the_engine_runs_is_staged_not_live(client, auth):
    client.post("/api/control/engine/start", headers=auth)
    r = client.post("/api/tuning", json={"values": {"STOP_FRAC": 0.30}}, headers=auth)
    assert r.json()["pending_restart"] is True


def test_a_tuning_change_is_audited(client, auth):
    client.post("/api/tuning", json={"values": {"TARGET_PTS": 80}}, headers=auth)
    actions = [a["action"] for a in client.get("/api/audit", headers=auth).json()["entries"]]
    assert "tuning.save" in actions
