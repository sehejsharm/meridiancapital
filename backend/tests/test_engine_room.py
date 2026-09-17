"""Rate gauges, hardware alarms, shadow mode and the emergency stop."""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from app.security import hash_password

PASSWORD = "meridian-test-password"
os.environ.setdefault("MERIDIAN_PASSWORD_HASH", hash_password(PASSWORD, rounds=1000))

REFERENCE = None


@pytest.fixture
def client(monkeypatch, tmp_path):
    from app import deps
    from app.config import settings
    from tests.test_algos import FakeSupervisor

    monkeypatch.setattr(settings, "db_path", tmp_path / "room.db")
    monkeypatch.setattr(settings, "password_hash", os.environ["MERIDIAN_PASSWORD_HASH"])
    monkeypatch.setattr(deps, "Supervisor", FakeSupervisor)

    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture
def auth(client):
    return {"Authorization": f"Bearer {client.post('/api/auth/login', json={'password': PASSWORD}).json()['token']}"}


# ── rate limiter gauges ──────────────────────────────────────────────────────
def test_the_limiter_reports_a_live_rate_not_just_a_total():
    from engine.broker import RateLimiter

    rl = RateLimiter({"ltp": 8.0})
    for _ in range(10):
        rl.acquire("ltp")
    st = rl.stats()
    endpoint = st["endpoints"][0]
    assert endpoint["calls"] == 10
    assert endpoint["rate_per_sec"] > 0
    assert 0 < endpoint["utilisation"] <= 1.0


def test_utilisation_is_capped_at_one():
    import time

    from engine.broker import RateLimiter

    # A 0.5/s cap over a 10s window is 5 calls at 100%; 8 puts it past the cap.
    rl = RateLimiter({"ltp": 0.5})
    now = time.time()
    for _ in range(8):
        rl.recent["ltp"].append(now)
    endpoint = rl.stats()["endpoints"][0]
    assert endpoint["rate_per_sec"] == 0.8
    assert endpoint["utilisation"] == 1.0, "a gauge must not run past its own dial"


def test_old_calls_leave_the_window():
    import time

    from engine.broker import RateLimiter

    rl = RateLimiter({"ltp": 8.0})
    rl.recent["ltp"].append(time.time() - (rl.WINDOW_SEC + 5))
    assert rl.rate("ltp") == 0.0, "a call older than the window must not count"


def test_penalties_are_counted_per_endpoint():
    from engine.broker import RateLimiter

    rl = RateLimiter({"ltp": 8.0, "candle": 2.0})
    rl.penalise("candle", 0.01)
    rl.penalise("candle", 0.01)
    stats = {e["endpoint"]: e for e in rl.stats()["endpoints"]}
    assert stats["candle"]["throttled"] == 2
    assert stats["ltp"]["throttled"] == 0


def test_peak_utilisation_picks_the_tightest_endpoint():
    import time

    from engine.broker import RateLimiter

    rl = RateLimiter({"ltp": 8.0, "candle": 2.0})
    now = time.time()
    for _ in range(4):
        rl.recent["ltp"].append(now)
    for _ in range(16):
        rl.recent["candle"].append(now)
    st = rl.stats()
    # candle is at 1.6/s of 2/s; ltp at 0.4/s of 8/s. The tighter one wins.
    assert st["peak_utilisation"] == max(e["utilisation"] for e in st["endpoints"])
    assert st["peak_utilisation"] > 0.5


# ── hardware alarms ──────────────────────────────────────────────────────────
def test_health_reports_machine_memory(client, auth):
    body = client.get("/api/health/detail", headers=auth).json()
    keys = {c["key"] for c in body["checks"]}
    assert "system_memory" in keys
    assert "position_open" in body


def test_memory_pressure_is_critical_when_a_position_is_open(client, auth, monkeypatch):
    """The scenario worth shouting about: an OOM kill with capital in the market."""
    from app import health

    monkeypatch.setattr(
        health, "_meminfo",
        lambda: {"total_mb": 1000.0, "available_mb": 150.0, "used_mb": 850.0,
                 "used_fraction": 0.85, "swap_total_mb": 0.0, "swap_used_mb": 0.0,
                 "swap_used_fraction": 0.0},
    )

    monkeypatch.setattr(health, "_position_open", lambda db: False)
    flat = client.get("/api/health/detail", headers=auth).json()
    mem_flat = next(c for c in flat["checks"] if c["key"] == "system_memory")
    assert mem_flat["state"] == "warning"

    monkeypatch.setattr(health, "_position_open", lambda db: True)
    exposed = client.get("/api/health/detail", headers=auth).json()
    mem_open = next(c for c in exposed["checks"] if c["key"] == "system_memory")
    assert mem_open["state"] == "critical"
    assert "POSITION IS OPEN" in mem_open["detail"]
    assert exposed["state"] == "critical"


def test_position_detection_reads_engine_snapshots(client, auth):
    from app.deps import ctx
    from app.health import _position_open
    from shared.db import snapshot_key

    db = ctx().db
    assert _position_open(db) is False
    db.kv_set(snapshot_key("gk50k"), {"position": {"tsym": "NIFTY", "lots": 2}})
    assert _position_open(db) is True


def test_unreadable_meminfo_degrades_quietly(client, auth, monkeypatch):
    from app import health

    monkeypatch.setattr(health, "_meminfo", dict)
    body = client.get("/api/health/detail", headers=auth).json()
    assert body["state"] in ("ok", "warning", "critical", "unknown")
    assert "system_memory" not in {c["key"] for c in body["checks"]}


# ── shadow mode ──────────────────────────────────────────────────────────────
def test_shadow_is_absent_until_configured(client, auth):
    body = client.get("/api/algos/gk50k/shadow", headers=auth).json()
    assert body["configured"] is False


def test_creating_a_shadow_pins_it_to_paper(client, auth):
    r = client.post("/api/algos/gk50k/shadow", json={"enabled": True}, headers=auth)
    assert r.status_code == 200
    shadow_id = r.json()["shadow_algo_id"]
    assert shadow_id == "gk50k-shadow"

    shadow = client.get(f"/api/algos/{shadow_id}", headers=auth).json()
    assert shadow["mode"] == "paper"
    assert shadow["shadow_of"] == "gk50k"


def test_a_shadow_can_never_be_switched_to_live(client, auth):
    """The whole point is that only one of the pair sends orders."""
    client.post("/api/algos/gk50k/shadow", json={"enabled": True}, headers=auth)
    r = client.post(
        "/api/algos/gk50k-shadow/mode",
        json={"mode": "live", "confirm": "TRADE REAL MONEY"},
        headers=auth,
    )
    assert r.status_code == 409
    assert "paper twin" in r.json()["detail"]


def test_a_shadow_cannot_have_a_shadow(client, auth):
    client.post("/api/algos/gk50k/shadow", json={"enabled": True}, headers=auth)
    r = client.post("/api/algos/gk50k-shadow/shadow", json={"enabled": True}, headers=auth)
    assert r.status_code == 400


def test_shadow_can_be_removed(client, auth):
    client.post("/api/algos/gk50k/shadow", json={"enabled": True}, headers=auth)
    r = client.post("/api/algos/gk50k/shadow", json={"enabled": False}, headers=auth)
    assert r.status_code == 200 and r.json()["shadow_algo_id"] is None
    assert client.get("/api/algos/gk50k/shadow", headers=auth).json()["configured"] is False


def test_drag_is_paper_minus_live(client, auth):
    from app.deps import ctx
    from app.shadow import compare

    db = ctx().db
    # Same session: paper thinks +2000 gross with no costs; live kept 1500
    # after 120 of real charges, so 500 of drag, 380 of it slippage.
    db.add_trade({"entry_ts": "2026-09-16T10:30", "exit_ts": "2026-09-16T11:00",
                  "session_date": "2026-09-16", "mode": "paper", "gross": 2000.0,
                  "charges": 0.0, "net": 2000.0, "algo_id": "gk50k-shadow"})
    db.add_trade({"entry_ts": "2026-09-16T10:30", "exit_ts": "2026-09-16T11:00",
                  "session_date": "2026-09-16", "mode": "live", "gross": 1620.0,
                  "charges": 120.0, "net": 1500.0, "algo_id": "gk50k"})

    out = compare(db, "gk50k", "gk50k-shadow")
    assert out["paired_sessions"] == 1
    totals = out["totals"]
    assert totals["paper_net"] == 2000.0
    assert totals["live_net"] == 1500.0
    assert totals["drag"] == 500.0
    assert totals["charges_paid"] == 120.0
    assert totals["slippage_est"] == 380.0
    assert totals["drag_pct_of_paper"] == 25.0


def test_a_session_only_one_side_traded_is_not_paired(client, auth):
    from app.deps import ctx
    from app.shadow import compare

    db = ctx().db
    db.add_trade({"entry_ts": "2026-09-16T10:30", "exit_ts": "2026-09-16T11:00",
                  "session_date": "2026-09-16", "mode": "paper", "net": 900.0,
                  "algo_id": "gk50k-shadow"})
    out = compare(db, "gk50k", "gk50k-shadow")
    assert out["paired_sessions"] == 0, "one-sided sessions must not distort the drag"
    assert out["sessions"][0]["both_traded"] is False


def test_favourable_slippage_shows_as_negative_drag(client, auth):
    from app.deps import ctx
    from app.shadow import compare

    db = ctx().db
    db.add_trade({"entry_ts": "2026-09-16T10:30", "exit_ts": "2026-09-16T11:00",
                  "session_date": "2026-09-16", "mode": "paper", "net": 1000.0,
                  "algo_id": "gk50k-shadow"})
    db.add_trade({"entry_ts": "2026-09-16T10:30", "exit_ts": "2026-09-16T11:00",
                  "session_date": "2026-09-16", "mode": "live", "net": 1100.0,
                  "charges": 100.0, "algo_id": "gk50k"})
    assert compare(db, "gk50k", "gk50k-shadow")["totals"]["drag"] == -100.0


# ── emergency stop ───────────────────────────────────────────────────────────
def test_the_nuke_needs_the_phrase(client, auth):
    r = client.post("/api/control/nuke", json={"confirm": "please"}, headers=auth)
    assert r.status_code == 400 and "NUKE ALL" in r.json()["detail"]


def test_the_nuke_needs_a_session(client):
    assert client.post("/api/control/nuke", json={"confirm": "NUKE ALL"}).status_code == 401


def test_the_nuke_queues_exits_before_stopping_engines(client, auth):
    """A stopped engine cannot square off — the exit has to be queued first."""
    from app.deps import ctx

    client.post("/api/control/engine/start", headers=auth)
    db = ctx().db
    db.kv_set("engine:snapshot", {"position": {"tsym": "NIFTY18SEP26", "lots": 2}})

    r = client.post("/api/control/nuke", json={"confirm": "NUKE ALL"}, headers=auth)
    assert r.status_code == 200
    body = r.json()

    assert body["engines_stopped"], "the fleet must come down"
    assert "gk50k" in body["had_open_positions"]
    # The flatten was enqueued for the algo that was holding.
    queued = [c for c in db.recent_commands() if c["action"] == "flatten"]
    assert queued, "an exit must be queued for the open position"


def test_the_nuke_disarms_automation(client, auth):
    """Otherwise the scheduler brings the fleet straight back up."""
    client.post("/api/control/schedule", json={"enabled": True}, headers=auth)
    client.post("/api/control/engine/start", headers=auth)

    body = client.post("/api/control/nuke", json={"confirm": "NUKE ALL"}, headers=auth).json()
    assert body["automation_disarmed"] is True
    assert client.get("/api/status", headers=auth).json()["schedule"]["enabled"] is False


def test_the_nuke_is_audited_as_critical(client, auth):
    client.post("/api/control/nuke", json={"confirm": "NUKE ALL"}, headers=auth)
    actions = [a["action"] for a in client.get("/api/audit", headers=auth).json()["entries"]]
    assert "risk.nuke" in actions
    levels = [e["level"] for e in client.get("/api/events", headers=auth).json()["events"]]
    assert "critical" in levels


def test_the_nuke_is_safe_with_nothing_running(client, auth):
    r = client.post("/api/control/nuke", json={"confirm": "NUKE ALL"}, headers=auth)
    assert r.status_code == 200
    assert r.json()["had_open_positions"] == []
    assert "No engine was holding a position" in r.json()["detail"]


# ── the account budget is shared, not per process ────────────────────────────
def test_two_processes_cannot_each_spend_the_whole_cap(tmp_path):
    """Angel's caps are per API key. Several engines pacing themselves
    independently would present the sum of them to Angel and get banned."""
    from shared.ratelimit import SharedRateLimiter

    db = tmp_path / "budget.db"
    engine_a = SharedRateLimiter(db)
    engine_b = SharedRateLimiter(db)

    admitted = sum(
        1
        for _ in range(10)
        for limiter in (engine_a, engine_b)
        if limiter.reserve("ltp", 4.0) == 0
    )
    assert admitted == 4, f"the account budget was breached: {admitted} calls against a 4/s cap"


def test_the_budget_refills_after_its_window(tmp_path):
    import time

    from shared.ratelimit import SharedRateLimiter

    rl = SharedRateLimiter(tmp_path / "budget.db")
    for _ in range(6):
        rl.reserve("ltp", 2.0)
    assert rl.reserve("ltp", 2.0) > 0, "should be exhausted"
    time.sleep(1.05)
    assert rl.reserve("ltp", 2.0) == 0, "should refill once the window passes"


def test_an_exhausted_budget_advises_a_bounded_wait(tmp_path):
    from shared.ratelimit import SharedRateLimiter

    rl = SharedRateLimiter(tmp_path / "budget.db")
    for _ in range(8):
        rl.reserve("ltp", 2.0)
    wait = rl.reserve("ltp", 2.0)
    assert 0 < wait <= 1.0, "a caller must stay responsive to a shutdown signal"


def test_endpoints_have_independent_budgets(tmp_path):
    from shared.ratelimit import SharedRateLimiter

    rl = SharedRateLimiter(tmp_path / "budget.db")
    for _ in range(5):
        rl.reserve("candle", 2.0)
    assert rl.reserve("candle", 2.0) > 0
    assert rl.reserve("ltp", 8.0) == 0, "exhausting one endpoint must not block another"


def test_usage_is_visible_to_any_reader(tmp_path):
    from shared.ratelimit import SharedRateLimiter

    db = tmp_path / "budget.db"
    writer = SharedRateLimiter(db)
    for _ in range(3):
        writer.reserve("ltp", 8.0)
    assert SharedRateLimiter(db).usage("ltp") == 3


def test_a_broken_ledger_never_stops_the_engine(tmp_path):
    """Losing the shared budget degrades to per-process pacing, not to a halt."""
    from shared.ratelimit import SharedRateLimiter

    rl = SharedRateLimiter(tmp_path / "budget.db")
    rl.path = tmp_path / "nonexistent-dir" / "gone.db"
    assert rl.reserve("ltp", 8.0) == 0.0


def test_the_broker_limiter_defers_to_the_account_budget(tmp_path):
    from engine.broker import RateLimiter
    from shared.ratelimit import SharedRateLimiter

    shared = SharedRateLimiter(tmp_path / "budget.db")
    for _ in range(20):  # another engine has already spent the budget
        shared.reserve("ltp", 2.0)

    waits: list[float] = []
    rl = RateLimiter({"ltp": 2.0}, shared=shared)
    rl.shared.acquire = lambda key, cap, sleep=None: (waits.append(1.0), 1.0)[1]
    rl.acquire("ltp")
    assert waits, "the broker must wait on the account budget, not only on itself"
    assert rl.stats()["shared_budget"] is True
