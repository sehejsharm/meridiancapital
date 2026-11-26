"""The operator credential: a short PIN, a throttle that makes it viable, and
Face ID as the stronger second path in.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.security import LoginThrottle, hash_password, verify_password
from tests.auth_helpers import bearer, login

PIN = "3579"
# Deliberately NOT written to the environment: other test modules read
# MERIDIAN_PASSWORD_HASH at import time, and clobbering it here would break
# their logins depending on collection order. The fixture patches settings
# directly instead.
PIN_HASH = hash_password(PIN, rounds=1000)


@pytest.fixture
def client(monkeypatch, tmp_path):
    from app import deps
    from app.config import settings
    from tests.test_algos import FakeSupervisor

    monkeypatch.setattr(settings, "db_path", tmp_path / "auth.db")
    monkeypatch.setattr(settings, "password_hash", PIN_HASH)
    monkeypatch.setattr(deps, "Supervisor", FakeSupervisor)

    from app.main import app

    # The limiter is reset around every test by an autouse fixture in conftest.
    with TestClient(app) as c:
        yield c


# ── the PIN itself ───────────────────────────────────────────────────────────
def test_the_pin_hashes_and_verifies():
    stored = hash_password(PIN, rounds=1000)
    assert verify_password(PIN, stored)
    assert not verify_password("3578", stored)
    assert not verify_password("", stored)
    assert PIN not in stored, "the PIN must not be recoverable from the hash"


def test_signing_in_with_the_pin_works(client):
    r = login(client, PIN)
    assert r.status_code == 200
    assert r.json()["token"]


def test_a_wrong_pin_is_refused(client):
    assert login(client, "0000").status_code == 401


# ── throttle: what actually protects a 4-digit keyspace ──────────────────────
def test_the_throttle_locks_after_five_failures():
    t = LoginThrottle(max_attempts=5, window_sec=300, lockout_sec=900)
    for _ in range(5):
        t.check("ip")
        t.fail("ip")
    with pytest.raises(Exception) as exc:
        t.check("ip")
    assert "too many failed attempts" in str(exc.value.detail)


def test_the_lockout_doubles_on_repeat_offences():
    import time as _t

    t = LoginThrottle(max_attempts=1, window_sec=300, lockout_sec=10, max_lockout_sec=80)
    penalties = []
    for _ in range(4):
        t.fail("ip")
        penalties.append(round(t._locked["ip"] - _t.time()))
    # 10, 20, 40, then capped at 80.
    assert penalties[0] < penalties[1] < penalties[2]
    assert penalties[3] <= 80


def test_the_lockout_is_capped():
    t = LoginThrottle(max_attempts=1, lockout_sec=10, max_lockout_sec=30)
    import time as _t

    for _ in range(8):
        t.fail("ip")
    assert t._locked["ip"] - _t.time() <= 31


def test_a_successful_sign_in_clears_the_record():
    t = LoginThrottle(max_attempts=3)
    t.fail("ip")
    t.fail("ip")
    t.succeed("ip")
    assert "ip" not in t._hits and "ip" not in t._strikes


STRANGER = {"X-Meridian-Device": "stranger-" + "s" * 40}


def test_brute_forcing_the_pin_is_throttled_end_to_end(client):
    codes = [
        client.post("/api/auth/login", json={"password": f"{i:04d}"}, headers=STRANGER).status_code
        for i in range(8)
    ]
    assert 429 in codes, "an unthrottled 4-digit PIN would be walked in minutes"
    # The real PIN is refused too while locked out — the lock is not bypassable.
    assert client.post("/api/auth/login", json={"password": PIN}, headers=STRANGER).status_code == 429
    # And the flood did not lock out the operator's approved device.
    assert login(client, PIN).status_code == 200


# ── passkeys ─────────────────────────────────────────────────────────────────
def test_availability_is_answerable_without_a_session(client):
    r = client.get("/api/auth/passkeys/available")
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is False
    assert "enrolled" not in body, "how many devices are enrolled is nobody's business before sign-in"


def test_passkey_registration_requires_a_session(client):
    assert client.post("/api/auth/passkeys/register/options").status_code == 401
    assert client.get("/api/auth/passkeys").status_code == 401


def test_registration_options_need_the_rp_configured(client, monkeypatch):
    from app.config import settings

    auth = bearer(client, PIN)

    monkeypatch.setattr(settings, "rp_id", "")
    r = client.post("/api/auth/passkeys/register/options", headers=auth)
    assert r.status_code == 400 and "MERIDIAN_RP_ID" in r.json()["detail"]


def test_registration_options_are_issued_once_configured(client, monkeypatch):
    from app.config import settings

    auth = bearer(client, PIN)

    monkeypatch.setattr(settings, "rp_id", "desk.example.com")
    monkeypatch.setattr(settings, "rp_origin", "https://desk.example.com")
    body = client.post("/api/auth/passkeys/register/options", headers=auth).json()

    assert body["handle"]
    assert body["options"]["rp"]["id"] == "desk.example.com"
    assert body["options"]["challenge"]
    # Platform authenticator with user verification — Face ID, not a USB key.
    sel = body["options"]["authenticatorSelection"]
    assert sel["authenticatorAttachment"] == "platform"
    assert sel["userVerification"] == "required"


def test_authentication_is_refused_when_nothing_is_enrolled(client, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "rp_id", "desk.example.com")
    monkeypatch.setattr(settings, "rp_origin", "https://desk.example.com")
    r = client.post("/api/auth/passkeys/authenticate/options")
    assert r.status_code == 400 and "no device is enrolled" in r.json()["detail"]


def test_a_forged_assertion_is_rejected(client, monkeypatch):
    """A credential the server never issued a challenge for must not sign in."""
    from app.config import settings
    from app.deps import ctx

    monkeypatch.setattr(settings, "rp_id", "desk.example.com")
    monkeypatch.setattr(settings, "rp_origin", "https://desk.example.com")
    ctx().db.add_passkey("fake-id", "AAAA", 0, "Planted")

    r = client.post(
        "/api/auth/passkeys/authenticate",
        json={"handle": "never-issued", "credential": {"id": "fake-id"}},
        headers=STRANGER,
    )
    assert r.status_code == 401


def test_a_failed_passkey_attempt_counts_against_the_same_limiter(client, monkeypatch):
    """Otherwise the passkey route would be a way around the PIN lockout."""
    from app.config import settings
    from app.deps import ctx

    monkeypatch.setattr(settings, "rp_id", "desk.example.com")
    monkeypatch.setattr(settings, "rp_origin", "https://desk.example.com")
    ctx().db.add_passkey("fake-id", "AAAA", 0, "Planted")

    for _ in range(6):
        client.post(
            "/api/auth/passkeys/authenticate",
            json={"handle": "nope", "credential": {"id": "fake-id"}},
            headers=STRANGER,
        )
    assert client.post("/api/auth/login", json={"password": PIN}, headers=STRANGER).status_code == 429
    # The operator's approved device has a lockout of its own.
    assert login(client, PIN).status_code == 200


# ── challenge store ──────────────────────────────────────────────────────────
def test_a_challenge_is_single_use():
    from app.passkeys import ChallengeStore, PasskeyError

    store = ChallengeStore()
    handle = store.issue(b"challenge-bytes")
    assert store.take(handle) == b"challenge-bytes"
    with pytest.raises(PasskeyError):
        store.take(handle)


def test_a_challenge_expires():
    import time as _t

    from app.passkeys import ChallengeStore, PasskeyError

    store = ChallengeStore(ttl=-1)  # already expired on issue
    handle = store.issue(b"stale")
    _t.sleep(0.01)
    with pytest.raises(PasskeyError):
        store.take(handle)


def test_an_unknown_handle_is_refused():
    from app.passkeys import ChallengeStore, PasskeyError

    with pytest.raises(PasskeyError):
        ChallengeStore().take("made-up")


# ── passkey storage ──────────────────────────────────────────────────────────
def test_passkeys_round_trip(tmp_path):
    from shared.db import Database

    db = Database(tmp_path / "pk.db")
    db.add_passkey("cred-1", "pubkey", 0, "iPhone")
    assert [p["label"] for p in db.passkeys()] == ["iPhone"]

    db.touch_passkey("cred-1", 7)
    assert db.passkey("cred-1")["sign_count"] == 7
    assert db.passkey("cred-1")["last_used_ts"]

    db.delete_passkey("cred-1")
    assert db.passkeys() == []


def test_the_public_key_listing_never_exposes_the_key(tmp_path):
    from shared.db import Database

    db = Database(tmp_path / "pk.db")
    db.add_passkey("cred-1", "SECRETPUBKEY", 0, "iPhone")
    assert "public_key" not in db.passkeys()[0]
