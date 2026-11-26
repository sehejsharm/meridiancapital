from __future__ import annotations

import time

import pytest

from app.security import (
    LoginThrottle,
    authenticate_ws_ticket,
    decode_token,
    hash_password,
    issue_token,
    verify_password,
)


def test_password_round_trips():
    stored = hash_password("correct horse battery staple", rounds=1000)
    assert verify_password("correct horse battery staple", stored)


def test_wrong_password_is_rejected():
    stored = hash_password("correct horse battery staple", rounds=1000)
    assert not verify_password("Correct horse battery staple", stored)


def test_each_hash_uses_a_fresh_salt():
    assert hash_password("same", rounds=1000) != hash_password("same", rounds=1000)


def test_malformed_hash_is_rejected_not_crashed():
    assert not verify_password("anything", "garbage")
    assert not verify_password("anything", "")


def test_token_round_trips():
    payload = decode_token(issue_token("sehej", 60))
    assert payload["sub"] == "sehej" and payload["scope"] == "dashboard"


def test_expired_token_is_rejected():
    with pytest.raises(ValueError, match="expired"):
        decode_token(issue_token("sehej", -1))


def test_tampered_payload_is_rejected():
    header, payload, sig = issue_token("sehej", 60).split(".")
    forged = issue_token("attacker", 60).split(".")[1]
    with pytest.raises(ValueError, match="signature"):
        decode_token(f"{header}.{forged}.{sig}")


def test_malformed_token_is_rejected():
    with pytest.raises(ValueError):
        decode_token("not-a-token")


def test_dashboard_token_cannot_open_a_websocket():
    with pytest.raises(ValueError, match="not a websocket ticket"):
        authenticate_ws_ticket(issue_token("sehej", 60))


def test_ws_ticket_is_accepted(client, auth):  # noqa: F811
    from app.config import settings

    ticket = client.post("/api/auth/ws-ticket", headers=auth).json()["ticket"]
    principal = authenticate_ws_ticket(ticket)
    assert principal.subject == settings.operator and principal.device


def test_a_ws_ticket_without_an_approved_device_is_refused(client, auth):  # noqa: F811
    with pytest.raises(ValueError, match="approved device"):
        authenticate_ws_ticket(issue_token("sehej", 60, scope="ws"))


def test_throttle_locks_out_after_repeated_failures():
    from fastapi import HTTPException

    t = LoginThrottle(max_attempts=3, window_sec=60, lockout_sec=60)
    t.check("1.2.3.4")
    for _ in range(3):
        t.fail("1.2.3.4")
    with pytest.raises(HTTPException) as e:
        t.check("1.2.3.4")
    assert e.value.status_code == 429


def test_throttle_is_per_client():
    t = LoginThrottle(max_attempts=2, window_sec=60, lockout_sec=60)
    for _ in range(2):
        t.fail("1.2.3.4")
    t.check("5.6.7.8")  # must not raise


def test_successful_login_clears_the_counter():
    t = LoginThrottle(max_attempts=2, window_sec=60, lockout_sec=60)
    t.fail("1.2.3.4")
    t.succeed("1.2.3.4")
    t.fail("1.2.3.4")
    t.check("1.2.3.4")  # must not raise


def test_old_failures_fall_out_of_the_window():
    t = LoginThrottle(max_attempts=2, window_sec=1, lockout_sec=60)
    t.fail("1.2.3.4")
    time.sleep(1.1)
    t.fail("1.2.3.4")
    t.check("1.2.3.4")  # must not raise


from tests.test_desk import auth, client  # noqa: E402, F401  (fixtures)

# ── a session that renews itself ─────────────────────────────────────────────

def test_a_valid_session_is_renewed_and_keeps_its_sign_in_time(monkeypatch):
    import time as _t

    from app import security

    tok = security.issue_token("sehej", 600)
    first = security.decode_token(tok)
    assert first["at"] == first["iat"]
    monkeypatch.setattr(security.time, "time", lambda: first["iat"] + 3600 * 5)
    again = security.decode_token(security.issue_token("sehej", 600, auth_time=first["at"]))
    assert again["at"] == first["at"] and again["exp"] == first["iat"] + 3600 * 5 + 600
    assert _t.time() > 0


def test_renewal_stops_a_week_after_sign_in(client, auth, monkeypatch):  # noqa: F811
    from app import security
    from app.config import settings

    r = client.post("/api/auth/renew", headers=auth)
    assert r.status_code == 200 and r.json()["expires_in"] == settings.token_ttl_min * 60
    fresh = {"Authorization": f"Bearer {r.json()['token']}"}
    assert client.get("/api/auth/me", headers=fresh).status_code == 200
    assert client.post("/api/auth/renew").status_code == 401                       # no session, nothing to renew
    old = security.issue_token("sehej", 600, auth_time=int(__import__("time").time()) - security.MAX_SESSION_SEC - 60)
    late = client.post("/api/auth/renew", headers={"Authorization": f"Bearer {old}"})
    assert late.status_code == 401 and "sign in again" in late.json()["detail"]
