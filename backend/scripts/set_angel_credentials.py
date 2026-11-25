#!/usr/bin/env python3
"""Put the four Angel One values in the server's settings file, without editing it by hand.

    sudo /opt/meridian/backend/.venv/bin/python backend/scripts/set_angel_credentials.py
        asks for each value (nothing is shown as you type, nothing goes in shell history)

    sudo /opt/meridian/backend/.venv/bin/python backend/scripts/set_angel_credentials.py --from-program
        copies them from the newest uploaded program that has them written in
        (v13 and older, v15 onward); nothing to type.

Then restart the API (it does this for you if you answer yes):  sudo systemctl restart meridian-api

The values are written to /etc/meridian/meridian.env only. They are never printed: the
script shows each one as "set, N characters". The TOTP seed is checked by making a code
from it, so a typo is caught here rather than at the next login. Every other line of the
file is left as it was, and the file keeps its owner and permissions.
"""

from __future__ import annotations

import argparse
import getpass
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

ENV_PATH = Path("/etc/meridian/meridian.env")
DB_PATHS = (Path(os.environ.get("MERIDIAN_DB_PATH", "/var/lib/meridian/meridian.db")),)
KEYS = ("ANGEL_API_KEY", "ANGEL_CLIENT_ID", "ANGEL_PASSWORD", "ANGEL_TOTP_SECRET")
LABELS = {"ANGEL_API_KEY": "SmartAPI key", "ANGEL_CLIENT_ID": "Client code",
          "ANGEL_PASSWORD": "PIN", "ANGEL_TOTP_SECRET": "TOTP seed (the long code shown when you set up the authenticator)"}


def problem(key: str, value: str) -> str | None:
    """Why a value cannot be right, or None. Never includes the value."""
    v = value.strip()
    if not v:
        return "it is empty"
    if any(c.isspace() for c in v) or "'" in v or '"' in v:
        return "it contains a space or a quote"
    if key == "ANGEL_PASSWORD" and not v.isdigit():
        return "the Angel PIN is digits only"
    if key == "ANGEL_CLIENT_ID" and not re.fullmatch(r"[A-Za-z0-9]{4,20}", v):
        return "a client code is letters and digits"
    if key == "ANGEL_API_KEY" and not re.fullmatch(r"[A-Za-z0-9_-]{4,64}", v):
        return "an API key is letters and digits"
    if key == "ANGEL_TOTP_SECRET":
        try:
            import pyotp

            code = pyotp.TOTP(v.replace(" ", "").upper()).now()
            if not (len(code) == 6 and code.isdigit()):
                return "no code could be made from it"
        except Exception:
            return "it is not a valid authenticator seed (letters A-Z and digits 2-7)"
    return None


def from_source(source: str) -> dict[str, str]:
    """The four values written into a program's source, if all four are there and filled in."""
    out = {}
    for key in KEYS:
        m = re.search(r'os\.environ\["%s"\]\s*=\s*"([^"]+)"' % key, source)
        if m:
            out[key] = m.group(1).strip()
    return out if len(out) == len(KEYS) else {}


def from_database(db_path: Path) -> tuple[dict[str, str], str]:
    """(values, where they came from) from the newest uploaded version that has them."""
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = con.execute("SELECT algo_id, version, source FROM algo_versions ORDER BY id DESC").fetchall()
    finally:
        con.close()
    for algo_id, version, source in rows:
        found = from_source(source or "")
        if found:
            return found, f"{algo_id} version {version}"
    return {}, ""


def rewrite(path: Path, updates: dict[str, str]) -> None:
    """Replace the named lines in place (append the missing ones), keeping owner and mode."""
    existing = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    st = path.stat() if path.exists() else None
    out, seen = [], set()
    for line in existing:
        key = line.split("=", 1)[0].strip() if "=" in line and not line.lstrip().startswith("#") else ""
        if key in updates:
            out.append(f"{key}={updates[key]}")
            seen.add(key)
        else:
            out.append(line)
    out += [f"{k}={v}" for k, v in updates.items() if k not in seen]
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".meridian-env-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("\n".join(out).rstrip() + "\n")
        os.chmod(tmp, st.st_mode & 0o777 if st else 0o640)
        if st and os.geteuid() == 0:
            os.chown(tmp, st.st_uid, st.st_gid)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def ask() -> dict[str, str] | None:
    values = {}
    for key in KEYS:
        for _ in range(3):
            v = getpass.getpass(f"{LABELS[key]}: ").strip()
            if key == "ANGEL_TOTP_SECRET":
                v = v.replace(" ", "").upper()
            why = problem(key, v)
            if why is None:
                values[key] = v
                break
            print(f"  not accepted: {why}. Try again.", file=sys.stderr)
        else:
            print("Stopped: nothing was changed.", file=sys.stderr)
            return None
    return values


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Put the Angel One values in the server's settings file")
    ap.add_argument("--from-program", action="store_true", help="copy them from an uploaded program that has them")
    ap.add_argument("--env", default=str(ENV_PATH))
    ap.add_argument("--db", default=str(DB_PATHS[0]))
    ap.add_argument("--no-restart", action="store_true", help="do not offer to restart the API")
    args = ap.parse_args(argv)
    env = Path(args.env)

    if args.from_program:
        try:
            values, where = from_database(Path(args.db))
        except sqlite3.Error as e:
            print(f"Could not read the desk's database at {args.db} ({e}). Run with sudo.", file=sys.stderr)
            return 1
        if not values:
            print("No uploaded program has all four values written in. Run again without --from-program "
                  "to type them.", file=sys.stderr)
            return 1
        bad = {k: problem(k, v) for k, v in values.items() if problem(k, v)}
        if bad:
            print("The program's values do not look right: " + "; ".join(f"{LABELS[k]}: {w}" for k, w in bad.items())
                  + ". Nothing was changed.", file=sys.stderr)
            return 1
        print(f"Found all four in {where}.")
    else:
        print("Enter the four Angel One values. Nothing is shown as you type.")
        values = ask()
        if values is None:
            return 1

    try:
        rewrite(env, values)
    except PermissionError:
        print(f"Cannot write {env}: run it with sudo.", file=sys.stderr)
        return 1
    for k in KEYS:
        print(f"  {k:<18} set, {len(values[k])} characters")
    print(f"Written to {env}.")

    if args.no_restart:
        print("Restart the API to use them:  sudo systemctl restart meridian-api")
        return 0
    answer = input("Restart the API now so it uses them? A running program keeps running. [y/N] ").strip().lower()
    if answer == "y":
        r = subprocess.run(["systemctl", "restart", "meridian-api"], check=False)
        print("API restarted." if r.returncode == 0 else "The restart did not work: sudo systemctl restart meridian-api")
    else:
        print("Restart it later:  sudo systemctl restart meridian-api")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
