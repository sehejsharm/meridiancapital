"""Signing in as the dashboard does: the PIN, from a device an operator approved.

The dashboard's relay sends the device's secret (held in an httpOnly cookie) in
X-Meridian-Device. Tests approve a fixed one directly in the running app's
database, the way an operator would from Controls.
"""

from __future__ import annotations

DEVICE = "test-device-" + "d" * 40
LABEL = "Test browser"


def device_headers(device: str = DEVICE, label: str = LABEL) -> dict:
    from urllib.parse import quote

    return {"X-Meridian-Device": device, "X-Meridian-Device-Label": quote(label)}


def approve(device: str = DEVICE, label: str = LABEL) -> str:
    """Approve the device in the app's database (inside a TestClient block); returns its id."""
    from app import devices
    from app.deps import ctx

    return devices.approve_secret(ctx().db, device, label, "127.0.0.1", by="test", via="test fixture")["id"]


def login(client, password: str, headers: dict | None = None, device: str = DEVICE):
    """POST /api/auth/login from an approved device; returns the response."""
    approve(device)
    return client.post("/api/auth/login", json={"password": password},
                       headers={**device_headers(device), **(headers or {})})


def token(client, password: str) -> str:
    r = login(client, password)
    assert r.status_code == 200, r.text
    return r.json()["token"]


def bearer(client, password: str) -> dict:
    return {"Authorization": f"Bearer {token(client, password)}"}
