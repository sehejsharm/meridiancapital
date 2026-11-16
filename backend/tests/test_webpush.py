"""Phone notifications: the message a phone receives decrypts to what was sent, and only a push service is ever called."""

from __future__ import annotations

import json
import struct

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app import notify, webpush
from shared.db import Database
from tests.test_desk import auth, client  # noqa: F401

ENDPOINT = "https://fcm.googleapis.com/fcm/send/abc123"


class Phone:
    """What a browser holds: a key pair and an auth secret. It decrypts as RFC 8291 says a receiver must."""

    def __init__(self):
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.public = self.key.public_key().public_bytes(serialization.Encoding.X962,
                                                         serialization.PublicFormat.UncompressedPoint)
        self.auth = b"0123456789abcdef"
        self.p256dh, self.auth_b64 = webpush.b64u(self.public), webpush.b64u(self.auth)

    def decrypt(self, body: bytes) -> bytes:
        salt, (rs, idlen) = body[:16], struct.unpack("!IB", body[16:21])
        assert rs == 4096 and idlen == 65
        as_public = body[21:86]
        shared = self.key.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), as_public))
        ikm = HKDF(hashes.SHA256(), 32, self.auth, b"WebPush: info\x00" + self.public + as_public).derive(shared)
        cek = HKDF(hashes.SHA256(), 16, salt, b"Content-Encoding: aes128gcm\x00").derive(ikm)
        nonce = HKDF(hashes.SHA256(), 12, salt, b"Content-Encoding: nonce\x00").derive(ikm)
        plain = AESGCM(cek).decrypt(nonce, body[86:], None)
        assert plain.endswith(b"\x02")
        return plain[:-1]


@pytest.fixture
def db(tmp_path):
    return Database(tmp_path / "push.db")


def test_rfc8291_example_vector():
    """The worked example in RFC 8291, section 5: same keys and salt in, the same bytes out."""
    sender = ec.derive_private_key(int.from_bytes(webpush.unb64u("yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw"), "big"),
                                   ec.SECP256R1())
    body = webpush.encrypt(b"When I grow up, I want to be a watermelon",
                           "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4",
                           "BTBZMqHH6r4Tts7J_aSIgg", salt=webpush.unb64u("DGv6ra1nlYgDCS1FRnbzlw"), sender=sender)
    assert webpush.b64u(body) == ("DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6Tlz"
                                  "AC8wEqKK6PBru3jl7A_yl95bQpu6cVPTpK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLVWGNWQexSgSxsj_Qulcy4a-fN")


def test_a_phone_decrypts_exactly_what_was_sent_and_the_sender_is_signed(db):
    phone, calls = Phone(), []
    webpush.subscribe(db, ENDPOINT, phone.p256dh, phone.auth_b64, "my phone")
    out = webpush.send_all("Trade entered", "NIFTY 24500 CE at ₹212.40", "entry", "info", db=db,
                           deliver=lambda url, body, headers: calls.append((url, body, headers)) or 201)
    assert out == {"sent": 1, "failed": 0, "removed": 0}
    url, body, headers = calls[0]
    msg = json.loads(phone.decrypt(body))
    assert url == ENDPOINT and msg["title"] == "Trade entered" and msg["body"] == "NIFTY 24500 CE at ₹212.40"
    assert headers["Content-Encoding"] == "aes128gcm" and headers["Urgency"] == "normal"
    # The VAPID token is a JWT for this push service, signed by the key the phone subscribed with.
    token, k = headers["Authorization"].removeprefix("vapid t=").split(", k=")
    head, claims, sig = token.split(".")
    assert json.loads(webpush.unb64u(claims))["aud"] == "https://fcm.googleapis.com"
    raw = webpush.unb64u(sig)
    public = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), webpush.unb64u(k))
    public.verify(encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big")),
                  f"{head}.{claims}".encode(), ec.ECDSA(hashes.SHA256()))
    assert k == webpush.status(db)["public_key"]                 # the same key every time, not a new one per send


def test_only_push_services_are_ever_called(db):
    phone = Phone()
    for bad in ("https://example.com/x", "http://fcm.googleapis.com/x", "https://fcm.googleapis.com.evil.io/x",
                "https://user@fcm.googleapis.com/x", "https://169.254.169.254/latest", "notaurl"):
        assert not webpush.allowed_endpoint(bad)
        with pytest.raises(ValueError):
            webpush.subscribe(db, bad, phone.p256dh, phone.auth_b64)
    assert webpush.allowed_endpoint("https://web.push.apple.com/abc")
    assert webpush.allowed_endpoint("https://updates.push.services.mozilla.com/wpush/v2/abc")
    with pytest.raises(ValueError):
        webpush.subscribe(db, ENDPOINT, "AAAA", phone.auth_b64)   # keys that cannot be encrypted to


def test_a_subscription_the_service_says_is_gone_is_forgotten_and_a_failure_never_raises(db):
    a, b = Phone(), Phone()
    webpush.subscribe(db, ENDPOINT, a.p256dh, a.auth_b64)
    webpush.subscribe(db, "https://web.push.apple.com/zzz", b.p256dh, b.auth_b64)
    out = webpush.send_all("t", "x", db=db, deliver=lambda url, *_: 410 if "apple" in url else 201)
    assert out == {"sent": 1, "failed": 0, "removed": 1}
    assert [s["endpoint"] for s in webpush.subscriptions(db)] == [ENDPOINT]

    def boom(*_):
        raise OSError("network down")
    assert webpush.send_all("t", "x", db=db, deliver=boom) == {"sent": 0, "failed": 1, "removed": 0}
    assert webpush.send_all("t", "x", db=None) == {"sent": 0, "failed": 0, "removed": 0}


def test_alerts_reach_the_phone_beside_the_other_channels(db, monkeypatch):
    for k in ("MERIDIAN_TELEGRAM_BOT_TOKEN", "MERIDIAN_TELEGRAM_CHAT_ID", "MERIDIAN_ALERT_WEBHOOK"):
        monkeypatch.delenv(k, raising=False)
    webpush.attach(db)
    try:
        assert notify.send("Trade exited", "net +₹450.00", "exit") == {}       # no phone: nothing claimed
        phone, got = Phone(), []
        webpush.subscribe(db, ENDPOINT, phone.p256dh, phone.auth_b64)
        monkeypatch.setattr(webpush, "_deliver", lambda url, body, headers: got.append(body) or 201)
        assert notify.send("Trade exited", "net +₹450.00", "exit") == {"push": True}
        assert json.loads(phone.decrypt(got[0]))["kind"] == "exit"
    finally:
        webpush.attach(None)


def test_the_routes(client, auth):  # noqa: F811
    from app.deps import ctx

    phone = Phone()
    assert client.get("/api/push").status_code == 401
    first = client.get("/api/push", headers=auth).json()
    assert len(webpush.unb64u(first["public_key"])) == 65 and first["devices"] == []
    assert client.post("/api/push/test", headers=auth).json()["ok"] is False
    body = {"endpoint": ENDPOINT, "keys": {"p256dh": phone.p256dh, "auth": phone.auth_b64}, "label": "my phone"}
    assert client.post("/api/push/subscribe", headers=auth, json={**body, "endpoint": "https://example.com/aaaaaaaaaaaaaaaa"}).status_code == 400
    r = client.post("/api/push/subscribe", headers=auth, json=body).json()
    assert r["devices"] == [{"label": "my phone", "added": r["devices"][0]["added"], "service": "fcm.googleapis.com"}]
    assert r["public_key"] == first["public_key"]
    assert "auth" not in json.dumps(r["devices"]) and "p256dh" not in json.dumps(r)   # the keys never come back out
    assert any(a["action"] == "push.subscribe" for a in ctx().db.audit_tail(5))
    assert client.post("/api/push/unsubscribe", headers=auth, json={"endpoint": ENDPOINT}).json()["devices"] == []
