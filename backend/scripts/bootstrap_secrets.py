#!/usr/bin/env python3
"""Generate the dashboard password hash and JWT signing key for .env.

    python scripts/bootstrap_secrets.py

Prompts for a password (never echoed, never stored in shell history) and prints
the two lines to paste into /etc/meridian/meridian.env.
"""

from __future__ import annotations

import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import generate_secret  # noqa: E402
from app.security import hash_password  # noqa: E402


def main() -> int:
    pw = getpass.getpass("Dashboard password: ")
    if len(pw) < 12:
        print("Refusing: use at least 12 characters.", file=sys.stderr)
        return 1
    if pw != getpass.getpass("Confirm: "):
        print("Passwords did not match.", file=sys.stderr)
        return 1

    print("\n# ---- paste into /etc/meridian/meridian.env ----")
    print(f"MERIDIAN_PASSWORD_HASH='{hash_password(pw)}'")
    print(f"MERIDIAN_JWT_SECRET='{generate_secret()}'")
    print("# -----------------------------------------------")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
