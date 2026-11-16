"""Alerts on the phone's lock screen, straight from the desk (Web Push).

The dashboard, installed on the phone's home screen, registers a service worker
and hands the server a *subscription*: an address at the phone maker's push
service (Apple, Google, Mozilla) and two keys. Each alert is encrypted to those
keys (RFC 8291, ``aes128gcm``) so the push service carries it without being able
to read it, and is signed with this server's own key pair (VAPID, RFC 8292) so
the push service knows who is sending.

* The key pair is made on first use and kept in the database (which the nightly
  backup encrypts before it leaves the machine). No account, no third party.
* Only addresses at the known push services are accepted: a subscription cannot
  point the server at an arbitrary host.
* A subscription the push service says is gone (404, 410) is forgotten.
* Sending never raises and never blocks trading: it runs in the notifier's
  thread, with a short timeout, beside Telegram and the webhook.

Nothing here can start, stop or change an algorithm.
"""

from __future__ import annotations

import base64
import json
import os
import struct
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

K_VAPID = "push:vapid"
K_SUBS = "push:subscriptions"
MAX_SUBSCRIPTIONS = 8
TIMEOUT_SEC = 8.0
TTL_SEC = 3600
RECORD_SIZE = 4096
MAX_PAYLOAD = 2800           # well inside every push service's 4 KB limit after encryption
PUSH_HOSTS = ("fcm.googleapis.com", ".push.apple.com", "updates.push.services.mozilla.com",
              ".notify.windows.com", "android.googleapis.com")

_db = None
_subject = "https://localhost"


def attach(db, subject: str = "") -> None:
    """Give the sender its database (called once when the API starts)."""
    global _db, _subject
    _db = db
    if db is not None and subject.lower().startswith("https://"):
        _subject = subject


def b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def unb64u(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def allowed_endpoint(url: str) -> bool:
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or not host or parts.username or parts.password:
        return False
    return any(host == h or (h.startswith(".") and host.endswith(h)) for h in PUSH_HOSTS)


# ── this server's key pair ───────────────────────────────────────────────────
def _raw_public(key: ec.EllipticCurvePrivateKey) -> bytes:
    return key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


def keypair(db) -> tuple[ec.EllipticCurvePrivateKey, str]:
    """The VAPID key pair, made on first use. Returns (private key, public key as base64url)."""
    stored = db.kv_get(K_VAPID, None)
    if stored and stored.get("private"):
        key = serialization.load_pem_private_key(stored["private"].encode(), password=None)
        return key, stored["public"]
    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption()).decode()
    public = b64u(_raw_public(key))
    db.kv_set(K_VAPID, {"private": pem, "public": public})
    return key, public


def vapid_header(key: ec.EllipticCurvePrivateKey, public: str, endpoint: str, subject: str, now: float | None = None) -> str:
    parts = urlsplit(endpoint)
    claims = {"aud": f"{parts.scheme}://{parts.netloc}", "exp": int((now or time.time()) + 12 * 3600), "sub": subject}
    head = b64u(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode())
    body = b64u(json.dumps(claims, separators=(",", ":")).encode())
    r, s = decode_dss_signature(key.sign(f"{head}.{body}".encode(), ec.ECDSA(hashes.SHA256())))
    sig = b64u(r.to_bytes(32, "big") + s.to_bytes(32, "big"))
    return f"vapid t={head}.{body}.{sig}, k={public}"


# ── encryption to the phone (RFC 8291) ───────────────────────────────────────
def encrypt(payload: bytes, p256dh: str, auth: str, *, salt: bytes | None = None,
            sender: ec.EllipticCurvePrivateKey | None = None) -> bytes:
    ua_public = unb64u(p256dh)
    auth_secret = unb64u(auth)
    if len(ua_public) != 65 or len(auth_secret) != 16:
        raise ValueError("not a push subscription's keys")
    sender = sender or ec.generate_private_key(ec.SECP256R1())
    salt = salt or os.urandom(16)
    as_public = _raw_public(sender)
    shared = sender.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_public))
    ikm = HKDF(hashes.SHA256(), 32, auth_secret, b"WebPush: info\x00" + ua_public + as_public).derive(shared)
    cek = HKDF(hashes.SHA256(), 16, salt, b"Content-Encoding: aes128gcm\x00").derive(ikm)
    nonce = HKDF(hashes.SHA256(), 12, salt, b"Content-Encoding: nonce\x00").derive(ikm)
    sealed = AESGCM(cek).encrypt(nonce, payload + b"\x02", None)      # 0x02: the last (only) record
    return salt + struct.pack("!IB", RECORD_SIZE, len(as_public)) + as_public + sealed


# ── subscriptions ────────────────────────────────────────────────────────────
def subscriptions(db) -> list[dict]:
    return list(db.kv_get(K_SUBS, None) or [])


def subscribe(db, endpoint: str, p256dh: str, auth: str, label: str = "") -> int:
    if not allowed_endpoint(endpoint):
        raise ValueError("that is not an address at a known push service")
    encrypt(b"check", p256dh, auth)                                   # the keys must be usable
    subs = [s for s in subscriptions(db) if s["endpoint"] != endpoint]
    subs.append({"endpoint": endpoint, "p256dh": p256dh, "auth": auth, "label": label[:60],
                 "added": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
    db.kv_set(K_SUBS, subs[-MAX_SUBSCRIPTIONS:])
    return len(subs[-MAX_SUBSCRIPTIONS:])


def unsubscribe(db, endpoint: str) -> int:
    subs = [s for s in subscriptions(db) if s["endpoint"] != endpoint]
    db.kv_set(K_SUBS, subs)
    return len(subs)


def status(db) -> dict:
    _key, public = keypair(db)
    return {"public_key": public,
            "devices": [{"label": s.get("label") or "phone", "added": s.get("added"),
                         "service": urlsplit(s["endpoint"]).hostname} for s in subscriptions(db)]}


# ── sending ──────────────────────────────────────────────────────────────────
def _deliver(endpoint: str, body: bytes, headers: dict) -> int:
    req = urllib.request.Request(endpoint, data=body, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SEC) as resp:  # noqa: S310 - https, known push hosts only
            return resp.status
    except urllib.error.HTTPError as e:
        return e.code


def send_all(title: str, text: str, kind: str = "info", level: str = "info", db=None, deliver=None) -> dict:
    """Send to every subscribed device. Returns {"sent", "failed", "removed"}; never raises."""
    db = db or _db
    deliver = deliver or _deliver
    out = {"sent": 0, "failed": 0, "removed": 0}
    if db is None:
        return out
    try:
        subs = subscriptions(db)
        if not subs:
            return out
        key, public = keypair(db)
        payload = json.dumps({"title": title[:120], "body": text[:MAX_PAYLOAD - 400], "kind": kind, "level": level,
                              "ts": int(time.time())}, ensure_ascii=False).encode()[:MAX_PAYLOAD]
        gone = []
        for s in subs:
            try:
                if not allowed_endpoint(s["endpoint"]):
                    gone.append(s["endpoint"])
                    continue
                code = deliver(s["endpoint"], encrypt(payload, s["p256dh"], s["auth"]), {
                    "Authorization": vapid_header(key, public, s["endpoint"], _subject),
                    "Content-Encoding": "aes128gcm", "Content-Type": "application/octet-stream",
                    "TTL": str(TTL_SEC), "Urgency": "high" if level in ("critical", "warn") else "normal"})
                if code in (404, 410):
                    gone.append(s["endpoint"])
                elif 200 <= code < 300:
                    out["sent"] += 1
                else:
                    out["failed"] += 1
            except Exception:
                out["failed"] += 1
        if gone:
            db.kv_set(K_SUBS, [s for s in subscriptions(db) if s["endpoint"] not in gone])
            out["removed"] = len(gone)
    except Exception:
        out["failed"] += 1
    return out
