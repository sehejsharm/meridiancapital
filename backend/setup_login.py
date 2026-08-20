#!/usr/bin/env python3
"""Create or reset the account you sign in with.

The recovery path when nobody can get in. Editing `.env` and restarting also
works, but only for the one super admin named by ADMIN_USER, and only if the
server can be restarted — this touches the database directly and needs neither.

    python3 setup_login.py                     # interactive, prompts for both
    python3 setup_login.py --user Sehej        # prompts for the password only
    python3 setup_login.py --user Sehej --password 'correct horse battery'
    python3 setup_login.py --hash 'my passcode'   # print a hash for .env, write nothing
    python3 setup_login.py --list              # who can sign in right now

Run it from `backend/`, with the same DATA_DIR the server uses — otherwise it
writes to a different database and the server never sees the account.
"""
from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app import db, users                      # noqa: E402
from app.auth import hash_password             # noqa: E402
from app.config import settings                # noqa: E402


def _fail(message: str) -> None:
    print(f"\033[31m✗\033[0m {message}", file=sys.stderr)
    raise SystemExit(1)


def _ok(message: str) -> None:
    print(f"\033[32m✓\033[0m {message}")


def _list_accounts() -> None:
    rows = users.list_all()
    if not rows:
        print("No accounts exist. Nobody can sign in.")
        print("Create one:  python3 setup_login.py --user <name>")
        return
    print(f"{len(rows)} account(s) in {settings.db_path}:\n")
    width = max(len(r["username"]) for r in rows)
    for r in rows:
        state = "disabled" if r["disabled"] else "active"
        seen = r["last_login"] or "never signed in"
        print(f"  {r['username']:<{width}}  {r['role']:<13} {state:<9} {seen}")


def _prompt_password() -> str:
    first = getpass.getpass("New passcode: ")
    if first != getpass.getpass("Confirm passcode: "):
        _fail("The two passcodes do not match.")
    return first


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Create or reset a Meridian Capital login.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("\n\n", 1)[1],
    )
    ap.add_argument("--user", help="Operator name to create or reset.")
    ap.add_argument("--password", help="Passcode. Omit to be prompted, which "
                                       "keeps it out of your shell history.")
    ap.add_argument("--role", default="super_admin", choices=list(users.ROLES),
                    help="Role for a newly created account (default: super_admin). "
                         "An existing account keeps the role it has.")
    ap.add_argument("--hash", metavar="PASSCODE",
                    help="Print the scrypt hash of PASSCODE for ADMIN_PASSWORD_HASH "
                         "in .env, and change nothing.")
    ap.add_argument("--list", action="store_true",
                    help="Show every account and exit.")
    args = ap.parse_args()

    # --hash touches no state, so it runs before the database is opened.
    if args.hash is not None:
        print(hash_password(args.hash))
        return

    db.init(settings.db_path)
    users.init()

    if args.list:
        _list_accounts()
        return

    username = args.user or input("Operator name: ").strip()
    if not username:
        _fail("An operator name is required.")
    try:
        username = users.validate_username(username)
    except users.UserError as exc:
        _fail(str(exc))

    password = args.password or _prompt_password()
    try:
        users.validate_password(password)
    except users.UserError as exc:
        _fail(str(exc))

    existing = users.get(username)
    if existing:
        users.set_password(username, password)
        if existing["disabled"]:
            users.set_disabled(existing["id"], False)
            _ok(f"{username} re-enabled.")
        _ok(f"Passcode reset for {username} ({existing['role']}).")
    else:
        users.create(username, password, role=args.role, created_by="setup_login")
        _ok(f"Created {username} as {args.role}.")

    print(f"\nDatabase: {settings.db_path}")
    print("Sign in with that name and passcode. If the server is already "
          "running it will pick this up on the next request — no restart needed.")
    # An ADMIN_PASSWORD still sitting in .env is re-applied on every boot and
    # would silently undo this the next time the server starts.
    import os
    if (os.getenv("ADMIN_PASSWORD") or os.getenv("ADMIN_PASSWORD_HASH")) \
            and username.lower() == (os.getenv("ADMIN_USER") or "Sehej").strip().lower():
        print("\n\033[33m!\033[0m .env sets a password for this same account. "
              "It is re-applied at every boot and will overwrite what you just "
              "set — update or clear it in .env too.")


if __name__ == "__main__":
    main()
