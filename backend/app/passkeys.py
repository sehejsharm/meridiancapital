"""Face ID / Touch ID sign-in, over WebAuthn.

What the platform authenticator actually does: the private key lives in the
phone's secure enclave and never leaves it. Face ID unlocks the key locally, the
device signs a challenge this server issued, and the server checks the signature
against the public key it stored at registration. No biometric data is
transmitted, and nothing here could reconstruct a face from what is stored.

Two things this relies on that are worth stating plainly:

*   The relying-party ID is the *dashboard's* domain, not this API's. A
    credential is bound to the origin of the page that created it, so the
    Vercel host is what must be configured — getting this wrong produces a
    credential the browser will refuse to use, not a silent weakening.
*   Registration requires an already-authenticated session. Enrolling a
    passkey is equivalent to issuing a second key to the account, so it is
    gated behind the PIN rather than offered to anonymous visitors.

Challenges are held in memory with a short expiry. They are single-use: a
replayed challenge is refused even inside its window.
"""

from __future__ import annotations

import base64
import secrets
import time
from dataclasses import dataclass

from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import base64url_to_bytes
from webauthn.helpers.structs import (
    AuthenticatorAttachment,
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

CHALLENGE_TTL_SEC = 180


class PasskeyError(Exception):
    """A ceremony could not be completed."""


@dataclass
class _Pending:
    challenge: bytes
    expires_at: float


class ChallengeStore:
    """Single-use challenges, expiring on their own.

    In memory deliberately: a challenge is meaningless after a couple of
    minutes, and an API restart invalidating in-flight sign-ins is the correct
    behaviour rather than a problem to engineer around.
    """

    def __init__(self, ttl: int = CHALLENGE_TTL_SEC):
        self.ttl = ttl
        self._items: dict[str, _Pending] = {}

    def issue(self, challenge: bytes) -> str:
        self._sweep()
        handle = secrets.token_urlsafe(16)
        self._items[handle] = _Pending(challenge=challenge, expires_at=time.time() + self.ttl)
        return handle

    def take(self, handle: str) -> bytes:
        self._sweep()
        pending = self._items.pop(handle, None)  # pop: a challenge is used once
        if pending is None:
            raise PasskeyError("this sign-in attempt expired or was already used — try again")
        return pending.challenge

    def _sweep(self) -> None:
        now = time.time()
        for handle in [h for h, p in self._items.items() if p.expires_at < now]:
            self._items.pop(handle, None)


store = ChallengeStore()


def _require_config(settings) -> tuple[str, str]:
    if not settings.rp_id or not settings.rp_origin:
        raise PasskeyError(
            "passkeys are not configured — set MERIDIAN_RP_ID to the dashboard's "
            "domain and MERIDIAN_RP_ORIGIN to its https:// origin"
        )
    return settings.rp_id, settings.rp_origin


def registration_options(settings, db) -> tuple[str, str]:
    """Options for enrolling this device. Returns (handle, options JSON)."""
    rp_id, _ = _require_config(settings)
    existing = [
        PublicKeyCredentialDescriptor(id=base64url_to_bytes(p["credential_id"]))
        for p in db.passkeys()
    ]
    options = generate_registration_options(
        rp_id=rp_id,
        rp_name="Meridian Capital",
        user_name=settings.operator,
        user_display_name=settings.operator,
        # Exclude what is already enrolled so the same device cannot silently
        # register twice and leave a stale credential behind.
        exclude_credentials=existing,
        authenticator_selection=AuthenticatorSelectionCriteria(
            # Platform only: this is for Face ID and Touch ID on the operator's
            # own phone, not a roaming key that could be left in a drawer.
            authenticator_attachment=AuthenticatorAttachment.PLATFORM,
            resident_key=ResidentKeyRequirement.PREFERRED,
            user_verification=UserVerificationRequirement.REQUIRED,
        ),
    )
    return store.issue(options.challenge), options_to_json(options)


def verify_registration(settings, db, credential: dict, handle: str, label: str) -> dict:
    rp_id, origin = _require_config(settings)
    challenge = store.take(handle)
    try:
        result = verify_registration_response(
            credential=credential,
            expected_challenge=challenge,
            expected_rp_id=rp_id,
            expected_origin=origin,
            require_user_verification=True,
        )
    except Exception as exc:
        raise PasskeyError(f"could not verify this device: {exc}") from exc

    credential_id = base64.urlsafe_b64encode(result.credential_id).rstrip(b"=").decode()
    public_key = base64.b64encode(result.credential_public_key).decode()
    db.add_passkey(
        credential_id=credential_id,
        public_key=public_key,
        sign_count=result.sign_count,
        label=label or "This device",
    )
    return {"credential_id": credential_id, "label": label or "This device"}


def authentication_options(settings, db) -> tuple[str, str]:
    rp_id, _ = _require_config(settings)
    keys = db.passkeys()
    if not keys:
        raise PasskeyError("no device is enrolled yet — sign in with the PIN and add one")
    options = generate_authentication_options(
        rp_id=rp_id,
        allow_credentials=[
            PublicKeyCredentialDescriptor(id=base64url_to_bytes(p["credential_id"]))
            for p in keys
        ],
        user_verification=UserVerificationRequirement.REQUIRED,
    )
    return store.issue(options.challenge), options_to_json(options)


def verify_authentication(settings, db, credential: dict, handle: str) -> dict:
    rp_id, origin = _require_config(settings)
    challenge = store.take(handle)

    raw_id = credential.get("id") or credential.get("rawId")
    if not raw_id:
        raise PasskeyError("the browser returned no credential id")
    stored = db.passkey(raw_id)
    if stored is None:
        raise PasskeyError("this device is not enrolled")

    try:
        result = verify_authentication_response(
            credential=credential,
            expected_challenge=challenge,
            expected_rp_id=rp_id,
            expected_origin=origin,
            credential_public_key=base64.b64decode(stored["public_key"]),
            credential_current_sign_count=int(stored["sign_count"] or 0),
            require_user_verification=True,
        )
    except Exception as exc:
        raise PasskeyError(f"sign-in failed: {exc}") from exc

    # A counter that fails to advance can indicate a cloned authenticator. Apple's
    # platform authenticators legitimately report 0 always, so only a genuine
    # regression from a non-zero counter is treated as suspicious.
    previous = int(stored["sign_count"] or 0)
    if result.new_sign_count and previous and result.new_sign_count <= previous:
        raise PasskeyError("credential counter did not advance — refusing this sign-in")

    db.touch_passkey(raw_id, result.new_sign_count)
    return {"credential_id": raw_id, "label": stored["label"]}
