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


def test_ws_ticket_is_accepted():
    assert authenticate_ws_ticket(issue_token("sehej", 60, scope="ws")).subject == "sehej"


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
