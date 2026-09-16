#!/usr/bin/env python3
"""Set the dashboard credential and JWT signing key.

    python3 scripts/bootstrap_secrets.py                    # print the lines
    sudo python3 scripts/bootstrap_secrets.py --write       # update the env file
    sudo python3 scripts/bootstrap_secrets.py --write --keep-jwt

Prompts for the credential, never echoing it and never putting it in shell
history. With --write it rewrites MERIDIAN_PASSWORD_HASH in
/etc/meridian/meridian.env in place, leaving every other line untouched, so
Angel One credentials already in that file survive.

Only the hash is ever stored. The credential itself is not written anywhere,
which is also why there is no way to recover it — losing it means running this
again.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import generate_secret  # noqa: E402
from app.security import hash_password  # noqa: E402

ENV_PATH = Path("/etc/meridian/meridian.env")
MIN_PIN_DIGITS = 4
MIN_PASSPHRASE = 12


def prompt() -> str | None:
    pw = getpass.getpass("Dashboard password or PIN: ")
    numeric = pw.isdigit()

    if numeric and len(pw) < MIN_PIN_DIGITS:
        print(f"Refusing: a PIN needs at least {MIN_PIN_DIGITS} digits.", file=sys.stderr)
        return None
    if not numeric and len(pw) < MIN_PASSPHRASE:
        print(
            f"Refusing: a passphrase needs at least {MIN_PASSPHRASE} characters.",
            file=sys.stderr,
        )
        return None
    if pw != getpass.getpass("Confirm: "):
        print("Entries did not match.", file=sys.stderr)
        return None

    if numeric:
        print(
            f"\nNote: a {len(pw)}-digit PIN is {10 ** len(pw):,} combinations. The login\n"
            f"limiter (5 tries, then a doubling lockout up to an hour) is what keeps\n"
            f"that from being walked. Enrol Face ID from Controls so the PIN is a\n"
            f"fallback rather than the only thing guarding a live trading account.\n",
            file=sys.stderr,
        )
    return pw


def rewrite(path: Path, updates: dict[str, str]) -> None:
    """Replace the named keys in place, appending any that are absent.

    Written to a temporary file in the same directory and moved over the
    original, so an interrupted run cannot leave a half-written env file that
    would stop the API from starting.
    """
    existing = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    seen: set[str] = set()
    out: list[str] = []

    for line in existing:
        key = line.split("=", 1)[0].strip() if "=" in line else ""
        if key in updates:
            out.append(f"{key}='{updates[key]}'")
            seen.add(key)
        else:
            out.append(line)

    for key, value in updates.items():
        if key not in seen:
            out.append(f"{key}='{value}'")

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".meridian-env-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("\n".join(out).rstrip() + "\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def main() -> int:
    ap = argparse.ArgumentParser(description="Set the dashboard credential")
    ap.add_argument("--write", action="store_true", help=f"update {ENV_PATH} in place")
    ap.add_argument("--env", default=str(ENV_PATH), help="env file to update")
    ap.add_argument(
        "--keep-jwt",
        action="store_true",
        help="leave MERIDIAN_JWT_SECRET alone (rotating it signs everyone out)",
    )
    args = ap.parse_args()

    pw = prompt()
    if pw is None:
        return 1

    updates = {"MERIDIAN_PASSWORD_HASH": hash_password(pw)}
    if not args.keep_jwt:
        updates["MERIDIAN_JWT_SECRET"] = generate_secret()

    if not args.write:
        print("\n# ---- paste into /etc/meridian/meridian.env ----")
        for key, value in updates.items():
            print(f"{key}='{value}'")
        print("# -----------------------------------------------")
        print("\nThen: sudo systemctl restart meridian-api", file=sys.stderr)
        return 0

    path = Path(args.env)
    try:
        rewrite(path, updates)
    except PermissionError:
        print(f"Cannot write {path} — re-run with sudo.", file=sys.stderr)
        return 1

    print(f"Updated {path}.")
    print("Restart the API for it to take effect:  sudo systemctl restart meridian-api")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
