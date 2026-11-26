"""The devices allowed to sign in to the dashboard, managed on the VM.

    sudo bash ~/meridiancapital/backend/deploy/devices.sh                    # list them
    sudo bash ~/meridiancapital/backend/deploy/devices.sh approve ABCD-EFGH  # the code on the waiting device
    sudo bash ~/meridiancapital/backend/deploy/devices.sh revoke <id>        # its sessions end at once
    sudo bash ~/meridiancapital/backend/deploy/devices.sh signout-all        # every session, every device

The way in when no signed-in device is at hand: the first device after approval
was switched on, or a new phone after the old one was lost. Anywhere else, approve
from Controls on a signed-in device. A device asks for approval by entering the
right PIN; the code it then shows lasts ten minutes.

Run through deploy/devices.sh, which loads /etc/meridian/meridian.env and runs this
as the service account (a database file written by root would lock the API out).
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _when(ms: int | None) -> str:
    if not ms:
        return "-"
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(int(ms) / 1000))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Devices allowed to sign in to the Meridian dashboard")
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("list", help="approved devices and those waiting")
    a = sub.add_parser("approve", help="approve the device showing this code")
    a.add_argument("code")
    r = sub.add_parser("revoke", help="remove a device; its sessions end at once")
    r.add_argument("device_id")
    sub.add_parser("signout-all", help="end every session on every device (devices stay approved)")
    args = parser.parse_args(argv)

    from app import devices
    from app.config import settings
    from shared.db import Database

    if not settings.jwt_secret:
        print("MERIDIAN_JWT_SECRET is not set: run this through deploy/devices.sh", file=sys.stderr)
        return 2
    db = Database(settings.db_path)
    devices.devices_since(db)

    if args.cmd == "approve":
        try:
            dev = devices.approve_by_code(db, args.code, by="console", via="approved on the VM console")
        except devices.DeviceError as exc:
            print(f"not approved: {exc}", file=sys.stderr)
            return 1
        db.audit("console", "device.approved", f"{dev['label']} (approved on the VM console)", "console")
        db.add_event("warn", f"device approved for sign-in: {dev['label']} — on the VM console", source="api")
        print(f"approved: {dev['label']} (id {dev['id']}, from {dev.get('last_ip') or '?'})")
        print("The device signs in by itself within a few seconds if its sign-in page is still open.")
        return 0
    if args.cmd == "revoke":
        try:
            dev = devices.revoke(db, args.device_id)
        except devices.DeviceError as exc:
            print(f"not revoked: {exc}", file=sys.stderr)
            return 1
        db.audit("console", "device.revoked", dev["label"], "console")
        db.add_event("warn", f"device removed: {dev['label']} — on the VM console", source="api")
        print(f"revoked: {dev['label']} (id {dev['id']}); its sessions have ended")
        return 0
    if args.cmd == "signout-all":
        devices.end_all_sessions(db)
        db.audit("console", "sessions.revoke_all", "", "console")
        db.add_event("warn", "every session was signed out on the VM console", source="api")
        print("every session has ended; approved devices sign in again with the PIN")
        return 0

    listing = devices.listing(db)
    print(f"approved ({len(listing['approved'])}):")
    for d in listing["approved"]:
        print(f"  {d['id']}  {d['label']:<28}  approved {_when(d['approved_ms'])} ({d['approved_via'] or '-'})"
              f"  last seen {_when(d['last_seen_ms'])} from {d['last_ip'] or '-'}")
    print(f"waiting ({len(listing['pending'])}):")
    for d in listing["pending"]:
        print(f"  {d['id']}  {d['label']:<28}  from {d['last_ip'] or '-'}  code expires in {d['expires_in'] // 60 + 1} min")
    if not listing["pending"]:
        print("  none — a device asks by entering the right PIN on the sign-in page")
    return 0


if __name__ == "__main__":
    sys.exit(main())
