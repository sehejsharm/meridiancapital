"""Sign-in needs the PIN *and* a device an operator approved.

A PIN alone can be guessed from enough addresses. With approval required, a
right guess from a stranger's device buys an approval request the operator never
grants, a code nobody types, and an alert to change the PIN.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.security import hash_password
from tests.auth_helpers import DEVICE, approve, bearer, device_headers, login

PIN = "4826"
NEW = "new-phone-" + "n" * 40
OTHER = "other-one-" + "o" * 40


@pytest.fixture
def client(monkeypatch, tmp_path):
    from app import deps
    from app.config import settings
    from tests.test_algos import FakeSupervisor

    monkeypatch.setattr(settings, "db_path", tmp_path / "devices.db")
    monkeypatch.setattr(settings, "password_hash", hash_password(PIN, rounds=1000))
    monkeypatch.setattr(settings, "relay_secret", "")
    monkeypatch.setattr(deps, "Supervisor", FakeSupervisor)
    sent: list[tuple] = []
    from app import notify

    monkeypatch.setattr(notify, "send", lambda title, text, **kw: sent.append((title, text)) or {})
    from app.main import app

    with TestClient(app) as c:
        c.alerts = sent
        yield c


def ask(client, device=NEW, pin=PIN, label="iPhone · Safari"):
    return client.post("/api/auth/login", json={"password": pin}, headers=device_headers(device, label))


def operator(client) -> dict:
    return bearer(client, PIN)


# ── a new device ─────────────────────────────────────────────────────────────
def test_the_right_pin_on_a_new_device_gets_a_code_not_a_session(client):
    r = ask(client)
    assert r.status_code == 403
    body = r.json()
    assert body["pending"] is True and "token" not in body
    assert len(body["code"]) == 9 and body["code"][4] == "-"
    assert body["expires_in"] == 600


def test_a_wrong_pin_on_a_new_device_raises_no_request(client):
    from app import devices
    from app.deps import ctx

    assert ask(client, pin="0000").status_code == 401
    assert devices.listing(ctx().db)["pending"] == []


def test_a_sign_in_page_that_sends_no_device_is_refused(client):
    r = client.post("/api/auth/login", json={"password": PIN})
    assert r.status_code == 400 and "reload" in r.json()["detail"]


def test_the_operators_hear_about_a_request(client):
    ask(client)
    assert client.alerts and "sign in" in client.alerts[0][0]
    assert "change the PIN" in client.alerts[0][1]


def test_requests_are_not_a_way_to_flood_the_operators_phone(client):
    for _ in range(5):
        ask(client)
    assert len(client.alerts) == 1


def test_the_listing_never_shows_the_code_or_the_secret(client):
    ask(client)
    listing = client.get("/api/auth/devices", headers=operator(client)).json()
    (waiting,) = listing["pending"]
    text = str(listing)
    assert "code" not in waiting and "secret_hash" not in text and NEW not in text
    assert waiting["label"] == "iPhone · Safari" and waiting["expires_in"] > 0


# ── approving it ─────────────────────────────────────────────────────────────
def test_approval_needs_the_code_on_the_device(client):
    code = ask(client).json()["code"]
    auth = operator(client)
    dev = client.get("/api/auth/devices", headers=auth).json()["pending"][0]["id"]

    bad = client.post(f"/api/auth/devices/{dev}/approve", json={"code": "AAAA-AAAA"}, headers=auth)
    assert bad.status_code == 400 and "does not match" in bad.json()["detail"]
    # Typed in lower case, without the dash: still the code.
    ok = client.post(f"/api/auth/devices/{dev}/approve", json={"code": code.lower().replace("-", "")}, headers=auth)
    assert ok.status_code == 200
    assert [d["id"] for d in ok.json()["approved"]].count(dev) == 1


def test_five_wrong_codes_refuse_the_request(client):
    ask(client)
    auth = operator(client)
    dev = client.get("/api/auth/devices", headers=auth).json()["pending"][0]["id"]
    for _ in range(4):
        client.post(f"/api/auth/devices/{dev}/approve", json={"code": "AAAA-AAAA"}, headers=auth)
    last = client.post(f"/api/auth/devices/{dev}/approve", json={"code": "AAAA-AAAA"}, headers=auth)
    assert "refused" in last.json()["detail"]
    assert client.get("/api/auth/devices", headers=auth).json()["pending"] == []


def test_the_waiting_device_collects_its_session_once(client):
    code = ask(client).json()["code"]
    claim = client.post("/api/auth/device/claim", headers=device_headers(NEW))
    assert claim.status_code == 202 and claim.json()["pending"] is True

    auth = operator(client)
    dev = client.get("/api/auth/devices", headers=auth).json()["pending"][0]["id"]
    client.post(f"/api/auth/devices/{dev}/approve", json={"code": code}, headers=auth)

    got = client.post("/api/auth/device/claim", headers=device_headers(NEW))
    assert got.status_code == 200
    token = got.json()["token"]
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"}).json()["device"] == dev
    # Once: the PIN it typed is spent.
    assert client.post("/api/auth/device/claim", headers=device_headers(NEW)).status_code == 403
    # From now on the PIN signs it straight in.
    assert ask(client).status_code == 200


def test_a_refused_device_gets_nothing(client):
    ask(client)
    auth = operator(client)
    dev = client.get("/api/auth/devices", headers=auth).json()["pending"][0]["id"]
    assert client.post(f"/api/auth/devices/{dev}/deny", headers=auth).status_code == 200
    assert client.post("/api/auth/device/claim", headers=device_headers(NEW)).status_code == 403


def test_another_device_cannot_collect_the_session(client):
    code = ask(client).json()["code"]
    auth = operator(client)
    dev = client.get("/api/auth/devices", headers=auth).json()["pending"][0]["id"]
    client.post(f"/api/auth/devices/{dev}/approve", json={"code": code}, headers=auth)
    assert client.post("/api/auth/device/claim", headers=device_headers(OTHER)).status_code == 403


def test_approving_takes_a_signed_in_operator(client):
    ask(client)
    from app import devices
    from app.deps import ctx

    dev = devices.listing(ctx().db)["pending"][0]["id"]
    assert client.post(f"/api/auth/devices/{dev}/approve", json={"code": "AAAA-AAAA"}).status_code == 401
    assert client.get("/api/auth/devices").status_code == 401


# ── ending sessions ──────────────────────────────────────────────────────────
def test_revoking_a_device_ends_its_session_at_once(client):
    auth = operator(client)
    assert client.get("/api/status", headers=auth).status_code == 200
    me = client.get("/api/auth/me", headers=auth).json()["device"]
    r = client.delete(f"/api/auth/devices/{me}", headers=auth)
    assert r.status_code == 200 and r.json()["signed_out"] is True
    after = client.get("/api/status", headers=auth)
    assert after.status_code == 401 and "no longer approved" in after.json()["detail"]
    # And the PIN alone does not bring it back: it is a new request now.
    assert ask(client, device=DEVICE).status_code == 403


def test_signing_out_ends_this_devices_sessions_on_the_server(client):
    auth = operator(client)
    assert client.post("/api/auth/logout", headers=auth).status_code == 200
    assert client.get("/api/status", headers=auth).status_code == 401
    # The device stays approved: the PIN signs it in again.
    assert ask(client, device=DEVICE).status_code == 200


def test_sign_out_everywhere_ends_every_session(client):
    mine = operator(client)
    approve(OTHER, "Laptop")
    theirs = {"Authorization": f"Bearer {ask(client, device=OTHER).json()['token']}"}
    assert client.post("/api/auth/sessions/revoke-all", headers=mine).status_code == 200
    assert client.get("/api/status", headers=mine).status_code == 401
    assert client.get("/api/status", headers=theirs).status_code == 401
    assert ask(client, device=OTHER).status_code == 200


def test_a_websocket_ticket_dies_with_its_device(client):
    auth = operator(client)
    me = client.get("/api/auth/me", headers=auth).json()["device"]
    ticket = client.post("/api/auth/ws-ticket", headers=auth).json()["ticket"]
    client.delete(f"/api/auth/devices/{me}", headers=auth)
    from app.security import authenticate_ws_ticket

    with pytest.raises(ValueError):
        authenticate_ws_ticket(ticket)


def test_an_open_socket_is_closed_when_its_device_is_revoked(client, monkeypatch):
    from starlette.websockets import WebSocketDisconnect

    from app.routers import ws as ws_router

    monkeypatch.setattr(ws_router, "RECHECK_SEC", 0.05)
    auth = operator(client)
    me = client.get("/api/auth/me", headers=auth).json()["device"]
    ticket = client.post("/api/auth/ws-ticket", headers=auth).json()["ticket"]
    with client.websocket_connect(f"/ws/live?ticket={ticket}") as sock:
        sock.receive_json()                                # the hello
        from app import devices
        from app.deps import ctx

        devices.revoke(ctx().db, me)
        with pytest.raises(WebSocketDisconnect) as closed:
            for _ in range(200):
                sock.receive_json()
        assert closed.value.code == 1008


# ── sessions from before approval was required ───────────────────────────────
def test_a_session_from_before_approval_carries_its_device_over(client):
    from app import devices, security
    from app.deps import ctx

    db = ctx().db
    # Issued before this code first ran: approval was not required then.
    since = devices.devices_since(db)
    old = security.issue_token("sehej", 600)
    db.kv_set(devices.K_DEVICES_SINCE, since + 60_000)
    devices._forget()
    legacy = {"Authorization": f"Bearer {old}"}
    assert client.get("/api/status", headers=legacy).status_code == 200

    renewed = client.post("/api/auth/renew", headers={**legacy, **device_headers(NEW, "Old laptop")})
    assert renewed.status_code == 200
    fresh = {"Authorization": f"Bearer {renewed.json()['token']}"}
    dev = client.get("/api/auth/me", headers=fresh).json()["device"]
    assert dev and devices.get(db, dev, fresh=True)["status"] == "approved"
    assert "already signed in" in devices.get(db, dev, fresh=True)["approved_via"]


def test_a_device_less_token_minted_now_opens_nothing(client):
    from app import security

    forged = security.issue_token("sehej", 600)
    r = client.get("/api/status", headers={"Authorization": f"Bearer {forged}"})
    assert r.status_code == 401 and "approved device" in r.json()["detail"]
    # Nor can it be renewed into a device-bound one.
    assert client.post("/api/auth/renew", headers={"Authorization": f"Bearer {forged}",
                                                    **device_headers(NEW)}).status_code == 401


def test_a_token_bound_to_a_device_nobody_approved_opens_nothing(client):
    from app import security

    made_up = security.issue_token("sehej", 600, device="0123456789abcdef")
    assert client.get("/api/status", headers={"Authorization": f"Bearer {made_up}"}).status_code == 401


# ── passkeys ─────────────────────────────────────────────────────────────────
def test_a_passkey_approves_the_device_it_is_used_on(client, monkeypatch):
    from app import passkeys

    monkeypatch.setattr(passkeys, "verify_authentication", lambda *a, **k: {"label": "Face ID"})
    r = client.post("/api/auth/passkeys/authenticate", json={"handle": "h", "credential": {"id": "x"}},
                    headers=device_headers(NEW, "iPad"))
    assert r.status_code == 200
    # The device is approved: the PIN now signs it in directly.
    assert ask(client).status_code == 200


# ── many addresses guessing together ─────────────────────────────────────────
def test_wrong_pins_from_many_addresses_pause_new_devices_but_not_the_operator(client, monkeypatch):
    from app import devices
    from app.config import settings

    monkeypatch.setattr(settings, "relay_secret", "relay-" + "r" * 40)
    monkeypatch.setattr(devices, "unknown_budget", devices.UnknownDeviceBudget(max_failures=6))
    relay = {"X-Meridian-Relay": settings.relay_secret}
    for i in range(6):
        h = {**relay, **device_headers(f"bot-{i:02d}-" + "b" * 40), "X-Meridian-Client-IP": f"203.0.113.{i}"}
        assert client.post("/api/auth/login", json={"password": f"{i:04d}"}, headers=h).status_code == 401
    # A seventh address, even with the right PIN: paused.
    h = {**relay, **device_headers(NEW), "X-Meridian-Client-IP": "198.51.100.7"}
    r = client.post("/api/auth/login", json={"password": PIN}, headers=h)
    assert r.status_code == 429 and "paused" in r.json()["detail"]
    # The operator's approved device is not affected.
    assert login(client, PIN, headers=relay).status_code == 200


# ── the console ──────────────────────────────────────────────────────────────
def test_the_console_approves_by_code(client, capsys):
    from scripts import devices as console

    code = ask(client).json()["code"]
    assert console.main(["approve", code]) == 0
    assert "approved: iPhone · Safari" in capsys.readouterr().out
    assert client.post("/api/auth/device/claim", headers=device_headers(NEW)).status_code == 200
    assert console.main(["approve", "ZZZZ-ZZZZ"]) == 1
    assert console.main([]) == 0
    assert "iPhone · Safari" in capsys.readouterr().out


def test_labels_are_cleaned():
    from app.devices import clean_label

    assert clean_label("iPhone\x00 · Safari\n\t") == "iPhone · Safari"
    assert clean_label("") == "Unnamed device"
    assert len(clean_label("x" * 500)) == 60
