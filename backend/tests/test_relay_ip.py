"""Sign-in lockouts belong to the attacker, not to everyone behind the relay.

Every dashboard sign-in reaches the API from Vercel's servers. Before the relay
forwarded the real client address, the lockout was keyed on Vercel's — so five
wrong guesses from any bot on the internet locked the operator out too.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.security import hash_password

PASSWORD = "correct-horse-12"
SECRET = "relay-" + "s" * 40


@pytest.fixture
def client(monkeypatch, tmp_path):
    from app import deps
    from app.config import settings
    from tests.test_algos import FakeSupervisor

    monkeypatch.setattr(settings, "db_path", tmp_path / "relay.db")
    monkeypatch.setattr(settings, "password_hash", hash_password(PASSWORD, rounds=1000))
    monkeypatch.setattr(settings, "relay_secret", SECRET)
    monkeypatch.setattr(deps, "Supervisor", FakeSupervisor)

    from app.main import app

    with TestClient(app) as c:
        yield c


def via_relay(ip: str, secret: str = SECRET) -> dict:
    # What the dashboard's relay sends. Every call shares one connecting address
    # (the TestClient's), exactly as every Vercel call shares Vercel's.
    from app.security import relay_token

    return {"X-Meridian-Relay": relay_token(secret) if secret else "", "X-Meridian-Client-IP": ip}


def login(client, password, headers):
    # A device not yet approved, as a bot's would be: the lockout is per address.
    return client.post("/api/auth/login", json={"password": password},
                       headers={**headers, "X-Meridian-Device": "unapproved-" + "u" * 40})


def test_a_bot_locking_itself_out_does_not_lock_out_the_operator(client):
    for _ in range(5):
        assert login(client, "guess", via_relay("203.0.113.9")).status_code == 401
    assert login(client, "guess", via_relay("203.0.113.9")).status_code == 429

    # Same connecting address, different real visitor: not locked out. The right
    # PIN from a device nobody approved gets an approval request, not a session.
    r = login(client, PASSWORD, via_relay("198.51.100.4"))
    assert r.status_code == 403 and r.json()["pending"] is True, r.json()
    # And the operator's approved device signs straight in.
    from tests.auth_helpers import login as approved_login

    assert approved_login(client, PASSWORD, headers=via_relay("198.51.100.4")).status_code == 200


def test_the_locked_out_bot_stays_locked_out_even_with_the_right_password(client):
    for _ in range(5):
        login(client, "guess", via_relay("203.0.113.9"))
    assert login(client, PASSWORD, via_relay("203.0.113.9")).status_code == 429


def test_without_the_relay_secret_the_api_refuses_everything(client):
    """With the secret set, only the dashboard's relay is answered: a script
    calling the API directly cannot reach the sign-in route at all, let alone
    rotate made-up addresses to dodge the lockout."""
    for i in range(8):
        r = login(client, "guess", via_relay(f"10.0.0.{i}", secret="not-the-secret"))
        assert r.status_code == 403 and r.json() == {"detail": "forbidden"}
    assert login(client, PASSWORD, {"X-Meridian-Client-IP": "10.0.1.1"}).status_code == 403
    assert client.get("/api/status").status_code == 403
    # Nothing was even counted against the lockout: the route never ran.
    assert login(client, "guess", via_relay("10.0.0.1")).status_code == 401


def test_the_health_check_stays_open_but_says_almost_nothing(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json() == {"ok": True, "service": "meridian-api"}
    full = client.get("/health", headers=via_relay("10.0.0.1")).json()
    assert full["ok"] is True and "engine_running" in full


def test_with_no_secret_configured_the_forwarded_address_is_never_trusted(client, monkeypatch):
    """The feature is opt-in: a deployment that never set the secret keeps the
    old behaviour exactly, rather than trusting any header it is sent."""
    from app.config import settings

    monkeypatch.setattr(settings, "relay_secret", "")
    for i in range(5):
        login(client, "guess", via_relay(f"10.0.2.{i}", secret=""))
    assert login(client, "guess", via_relay("10.0.2.99", secret="")).status_code == 429


def test_the_audit_log_records_the_real_visitor(client):
    from app.deps import ctx

    login(client, "guess", via_relay("203.0.113.77"))
    failed = [r for r in ctx().db.audit_tail(20) if r["action"] == "login.failed"]
    assert failed and failed[0]["ip"] == "203.0.113.77"


def test_a_path_smuggled_in_the_host_header_does_not_open_the_lock(client):
    """request.url is rebuilt from the Host header; the lock must judge the path
    the router actually dispatches, or "Host: x/health?" would walk past it."""
    r = client.get("/api/status", headers={"Host": "testserver/health?"})
    assert r.status_code == 403


def test_the_secret_itself_never_opens_the_lock(client):
    """The relay presents a short-lived signature, never the secret: a secret that
    leaked into a log, or a token copied out of one, opens nothing."""
    import time as _t

    from app.security import relay_token

    assert client.get("/api/status", headers={"X-Meridian-Relay": SECRET}).status_code == 403
    stale = relay_token(SECRET, _t.time() - 301)
    assert client.get("/api/status", headers={"X-Meridian-Relay": stale}).status_code == 403
    early = relay_token(SECRET, _t.time() + 301)
    assert client.get("/api/status", headers={"X-Meridian-Relay": early}).status_code == 403
    other = relay_token("another-" + "s" * 40)
    assert client.get("/api/status", headers={"X-Meridian-Relay": other}).status_code == 403
    # A current token passes the lock (and then needs a session, as always).
    assert client.get("/api/status", headers={"X-Meridian-Relay": relay_token(SECRET)}).status_code == 401
