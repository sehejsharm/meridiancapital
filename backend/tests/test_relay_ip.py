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
    return {"X-Meridian-Relay": secret, "X-Meridian-Client-IP": ip}


def login(client, password, headers):
    return client.post("/api/auth/login", json={"password": password}, headers=headers)


def test_a_bot_locking_itself_out_does_not_lock_out_the_operator(client):
    for _ in range(5):
        assert login(client, "guess", via_relay("203.0.113.9")).status_code == 401
    assert login(client, "guess", via_relay("203.0.113.9")).status_code == 429

    # Same connecting address, different real visitor: still gets in.
    r = login(client, PASSWORD, via_relay("198.51.100.4"))
    assert r.status_code == 200, r.json()


def test_the_locked_out_bot_stays_locked_out_even_with_the_right_password(client):
    for _ in range(5):
        login(client, "guess", via_relay("203.0.113.9"))
    assert login(client, PASSWORD, via_relay("203.0.113.9")).status_code == 429


def test_a_forged_address_without_the_secret_is_ignored(client):
    """A script calling the API directly must not be able to rotate a made-up
    address on every attempt and never hit the lockout."""
    for i in range(5):
        login(client, "guess", via_relay(f"10.0.0.{i}", secret="not-the-secret"))
    # All five counted against the real connecting address, so it is locked.
    assert login(client, "guess", via_relay("10.0.0.99", secret="not-the-secret")).status_code == 429


def test_a_forged_address_with_no_secret_header_is_ignored(client):
    for i in range(5):
        login(client, "guess", {"X-Meridian-Client-IP": f"10.0.1.{i}"})
    assert login(client, "guess", {"X-Meridian-Client-IP": "10.0.1.99"}).status_code == 429


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
