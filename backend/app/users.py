"""User accounts and roles.

One super admin is bootstrapped from `.env` so there is always a way in even
if the database is empty. Everyone else is created from the app.

Roles, narrowest first:

    viewer      read the dashboard, trades and reports
    operator    the above, plus start/stop and editing strategy parameters
    super_admin the above, plus managing users and replacing the algorithm

The split matters because "can look at the P&L" and "can replace the code that
trades my money" should not be the same permission.
"""
from __future__ import annotations

import os
import re
from datetime import datetime
from typing import Literal, Optional

from . import db

Role = Literal["super_admin", "operator", "viewer"]

ROLES: tuple[Role, ...] = ("super_admin", "operator", "viewer")

ROLE_RANK: dict[str, int] = {"viewer": 0, "operator": 1, "super_admin": 2}

ROLE_LABEL: dict[str, str] = {
    "viewer": "Viewer — read only",
    "operator": "Operator — can run the bot and tune the strategy",
    "super_admin": "Super admin — full control, users and algorithm",
}

USERNAME_RE = re.compile(r"^[A-Za-z0-9._-]{2,32}$")
MIN_PASSWORD_LENGTH = 8


class UserError(ValueError):
    """Raised for anything the caller could reasonably fix."""


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL DEFAULT 'viewer',
    disabled      INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL,
    created_by    TEXT,
    last_login    TEXT,
    must_change   INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_users_name ON users(username);
"""


def init() -> None:
    with db.connect() as conn:
        conn.executescript(SCHEMA)


# ---------------------------------------------------------------- helpers


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _row_to_public(row: dict) -> dict:
    """Never let a password hash leave this module."""
    return {
        "id": row["id"],
        "username": row["username"],
        "role": row["role"],
        "disabled": bool(row["disabled"]),
        "created_at": row["created_at"],
        "created_by": row["created_by"],
        "last_login": row["last_login"],
        "must_change": bool(row["must_change"]),
    }


def validate_username(username: str) -> str:
    name = (username or "").strip()
    if not USERNAME_RE.match(name):
        raise UserError(
            "Username must be 2-32 characters, letters, numbers, dot, dash or "
            "underscore only."
        )
    return name


def validate_password(password: str) -> str:
    if len(password or "") < MIN_PASSWORD_LENGTH:
        raise UserError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
    return password


# ---------------------------------------------------------------- queries


def get(username: str) -> Optional[dict]:
    return db.query_one(
        "SELECT * FROM users WHERE username = ? COLLATE NOCASE", (username.strip(),)
    )


def get_by_id(user_id: int) -> Optional[dict]:
    return db.query_one("SELECT * FROM users WHERE id = ?", (user_id,))


def list_all() -> list[dict]:
    rows = db.query("SELECT * FROM users ORDER BY id")
    return [_row_to_public(r) for r in rows]


def count() -> int:
    row = db.query_one("SELECT COUNT(*) AS n FROM users")
    return int(row["n"]) if row else 0


def count_active_super_admins(excluding_id: Optional[int] = None) -> int:
    sql = "SELECT COUNT(*) AS n FROM users WHERE role = 'super_admin' AND disabled = 0"
    params: list = []
    if excluding_id is not None:
        sql += " AND id != ?"
        params.append(excluding_id)
    row = db.query_one(sql, params)
    return int(row["n"]) if row else 0


# ---------------------------------------------------------------- mutations


def create(
    username: str, password: str, role: Role = "viewer",
    created_by: str = "system", must_change: bool = False,
) -> dict:
    from .auth import hash_password       # imported here to avoid a cycle

    name = validate_username(username)
    validate_password(password)
    if role not in ROLES:
        raise UserError(f"Unknown role: {role}")
    if get(name):
        raise UserError(f"A user called {name!r} already exists.")

    db.execute(
        """INSERT INTO users (username, password_hash, role, created_at, created_by,
                              must_change)
           VALUES (?,?,?,?,?,?)""",
        (name, hash_password(password), role, _now(), created_by, 1 if must_change else 0),
    )
    return _row_to_public(get(name))       # type: ignore[arg-type]


def set_password(username: str, password: str, clear_must_change: bool = True) -> None:
    from .auth import hash_password

    validate_password(password)
    row = get(username)
    if not row:
        raise UserError(f"No user called {username!r}.")
    db.execute(
        "UPDATE users SET password_hash = ?, must_change = ? WHERE id = ?",
        (hash_password(password), 0 if clear_must_change else row["must_change"], row["id"]),
    )


def set_role(user_id: int, role: Role) -> dict:
    if role not in ROLES:
        raise UserError(f"Unknown role: {role}")
    row = get_by_id(user_id)
    if not row:
        raise UserError("No such user.")
    if (row["role"] == "super_admin" and role != "super_admin"
            and count_active_super_admins(excluding_id=user_id) == 0):
        raise UserError(
            "This is the only super admin. Promote someone else first, "
            "otherwise nobody could manage users or the algorithm."
        )
    db.execute("UPDATE users SET role = ? WHERE id = ?", (role, user_id))
    return _row_to_public(get_by_id(user_id))   # type: ignore[arg-type]


def set_disabled(user_id: int, disabled: bool) -> dict:
    row = get_by_id(user_id)
    if not row:
        raise UserError("No such user.")
    if disabled and row["role"] == "super_admin" \
            and count_active_super_admins(excluding_id=user_id) == 0:
        raise UserError("Cannot disable the only super admin.")
    db.execute("UPDATE users SET disabled = ? WHERE id = ?", (1 if disabled else 0, user_id))
    return _row_to_public(get_by_id(user_id))   # type: ignore[arg-type]


def delete(user_id: int) -> None:
    row = get_by_id(user_id)
    if not row:
        raise UserError("No such user.")
    if row["role"] == "super_admin" and count_active_super_admins(excluding_id=user_id) == 0:
        raise UserError(
            "Cannot delete the only super admin — you would lock yourself out."
        )
    db.execute("DELETE FROM users WHERE id = ?", (user_id,))


def record_login(username: str) -> None:
    db.execute(
        "UPDATE users SET last_login = ? WHERE username = ? COLLATE NOCASE",
        (_now(), username),
    )


# ---------------------------------------------------------------- bootstrap


def bootstrap() -> Optional[str]:
    """Make sure the env-configured super admin exists and is usable.

    Runs on every boot. If the password in `.env` changes, the stored hash is
    updated to match — that is the recovery path when a password is forgotten,
    since `.env` is reachable over SSH and the database is not.
    """
    from .auth import hash_password, verify_password

    init()

    env_user = (os.getenv("ADMIN_USER") or "Sehej").strip()
    env_pass = (os.getenv("ADMIN_PASSWORD") or "").strip()
    if not env_pass:
        return None

    try:
        name = validate_username(env_user)
    except UserError:
        return None
    if len(env_pass) < MIN_PASSWORD_LENGTH:
        # Too short to accept as a new account, but an existing one still works.
        existing = get(env_user)
        return existing["username"] if existing else None

    existing = get(name)
    if existing is None:
        db.execute(
            """INSERT INTO users (username, password_hash, role, created_at, created_by)
               VALUES (?,?,?,?,?)""",
            (name, hash_password(env_pass), "super_admin", _now(), "bootstrap"),
        )
        return name

    changed = False
    if not verify_password(env_pass, existing["password_hash"]):
        db.execute("UPDATE users SET password_hash = ? WHERE id = ?",
                   (hash_password(env_pass), existing["id"]))
        changed = True
    if existing["role"] != "super_admin":
        db.execute("UPDATE users SET role = 'super_admin' WHERE id = ?", (existing["id"],))
        changed = True
    if existing["disabled"]:
        db.execute("UPDATE users SET disabled = 0 WHERE id = ?", (existing["id"],))
        changed = True
    return name if changed else name


# ---------------------------------------------------------------- authz


def has_at_least(role: Optional[str], required: Role) -> bool:
    return ROLE_RANK.get(role or "", -1) >= ROLE_RANK[required]
