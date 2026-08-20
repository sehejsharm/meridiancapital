"""User accounts and roles.

One super admin is bootstrapped from `.env` so there is always a way in even
if the database is empty. Everyone else is created from the app.

Roles are sets of capabilities, not a ladder. That distinction is the whole
point of the newer three: a risk manager must be able to hit the kill switch
without being able to edit the strategy, and a quant needs to upload code
without being able to point it at real money. Neither of those is expressible
as "more powerful than an operator" — they are sideways, not upward.

    viewer        read the dashboard, trades and reports
    compliance    the above, plus the audit trail; read-only, permanently
    quant_dev     the above, plus uploading and assigning algorithms — but
                  never switching to real money
    operator      view, start/stop, and tune strategy parameters
    risk_manager  view everything and stop anything, including the kill
                  switch; cannot change strategy or upload code
    super_admin   everything, including people and real money

`has_at_least` survives for the two guards that genuinely are a ladder
(operate, manage users); everything else asks for the capability it needs.
"""
from __future__ import annotations

import os
import re
from datetime import datetime
from typing import Literal, Optional

from . import db

Role = Literal["super_admin", "risk_manager", "operator", "quant_dev",
               "compliance", "viewer"]

ROLES: tuple[Role, ...] = ("super_admin", "risk_manager", "operator",
                           "quant_dev", "compliance", "viewer")

# Kept for the two checks that really are a ladder. A role absent from here
# ranks as a viewer for those, and is gated by capability everywhere else.
ROLE_RANK: dict[str, int] = {
    "viewer": 0, "compliance": 0, "quant_dev": 0,
    "operator": 1, "risk_manager": 1,
    "super_admin": 2,
}

ROLE_LABEL: dict[str, str] = {
    "viewer": "Viewer — read only",
    "compliance": "Compliance — read-only, including the audit trail",
    "quant_dev": "Quant developer — upload and assign algorithms, paper only",
    "operator": "Operator — run the bot and tune the strategy",
    "risk_manager": "Risk manager — stop anything, change nothing",
    "super_admin": "Super admin — full control, people and real money",
}

# ---------------------------------------------------------------- capabilities

CAPABILITIES: dict[str, str] = {
    "view": "See the dashboard, trades and reports",
    "export": "Download reports",
    "view_audit": "Read the audit trail",
    "operate": "Start and stop a session",
    "kill": "Stop a running session immediately",
    "tune_strategy": "Change strategy parameters",
    "upload_algorithm": "Upload and validate algorithm code",
    "activate_algorithm": "Assign an algorithm to a slot",
    "manage_users": "Create, change and remove operators",
    "arm_live": "Request or approve trading with real money",
    "configure_alerts": "Change alert rules and destinations",
}

ROLE_CAPS: dict[str, frozenset[str]] = {
    "viewer": frozenset({"view", "export"}),
    # Read-only by design: compliance that can change what it audits is not
    # compliance.
    "compliance": frozenset({"view", "export", "view_audit"}),
    "quant_dev": frozenset({
        "view", "export", "view_audit",
        "upload_algorithm", "activate_algorithm", "tune_strategy",
    }),
    "operator": frozenset({"view", "export", "operate", "kill", "tune_strategy"}),
    # Can always stop, can never change what runs.
    "risk_manager": frozenset({"view", "export", "view_audit", "operate", "kill"}),
    "super_admin": frozenset(CAPABILITIES),
}


def capabilities(role: Optional[str]) -> frozenset[str]:
    return ROLE_CAPS.get(role or "", frozenset())


def can(role: Optional[str], capability: str) -> bool:
    return capability in capabilities(role)


def permission_matrix() -> dict:
    """Every role against every capability — rendered on the Admin page.

    Written out rather than described in prose because "what exactly can a
    quant do" is a question that gets asked in procurement, and a table
    generated from the same constants the guards use cannot drift from them.
    """
    return {
        "capabilities": [{"key": k, "label": v} for k, v in CAPABILITIES.items()],
        "roles": [
            {
                "value": r,
                "label": ROLE_LABEL[r],
                "grants": sorted(ROLE_CAPS.get(r, frozenset())),
            }
            for r in ROLES
        ],
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


def env_password_hash() -> Optional[str]:
    """The super admin's password from `.env`, as a stored hash.

    Two spellings, because a deployment platform's secret store is not always
    somewhere you want a plaintext password to sit:

      ADMIN_PASSWORD       the password itself, hashed here at boot
      ADMIN_PASSWORD_HASH  a hash computed in advance, used as-is

    The hash wins when both are set, since supplying one is the deliberate act.
    A malformed hash is ignored rather than stored: writing it verbatim would
    create an account no password on earth can open, and the operator would see
    only "incorrect passcode" with no hint as to why.
    """
    from .auth import hash_password, is_password_hash

    pre = (os.getenv("ADMIN_PASSWORD_HASH") or "").strip()
    if pre:
        if is_password_hash(pre):
            return pre
        # Falls through to ADMIN_PASSWORD, so a typo in one variable does not
        # take the whole login with it.
    plain = (os.getenv("ADMIN_PASSWORD") or "").strip()
    if plain and len(plain) >= MIN_PASSWORD_LENGTH:
        return hash_password(plain)
    return None


def bootstrap() -> Optional[str]:
    """Make sure the env-configured super admin exists and is usable.

    Runs on every boot. If the password in `.env` changes, the stored hash is
    updated to match — that is the recovery path when a password is forgotten,
    since `.env` is reachable over SSH and the database is not.
    """
    from .auth import verify_password

    init()

    env_user = (os.getenv("ADMIN_USER") or "Sehej").strip()
    try:
        name = validate_username(env_user)
    except UserError:
        return None

    desired = env_password_hash()
    if desired is None:
        # Nothing usable in `.env` — a password too short to accept, or none at
        # all. An account created earlier still signs in; it just cannot be
        # reset from here.
        existing = get(name)
        return existing["username"] if existing else None

    existing = get(name)
    if existing is None:
        db.execute(
            """INSERT INTO users (username, password_hash, role, created_at, created_by)
               VALUES (?,?,?,?,?)""",
            (name, desired, "super_admin", _now(), "bootstrap"),
        )
        return name

    changed = False
    # A fresh scrypt hash of the same password differs every time (new salt),
    # so the plaintext is compared by verification and a pre-computed hash by
    # identity. Either way the stored row only moves when `.env` really changed.
    plain = (os.getenv("ADMIN_PASSWORD") or "").strip()
    pre = (os.getenv("ADMIN_PASSWORD_HASH") or "").strip()
    if pre and desired == pre:
        stale = existing["password_hash"] != desired
    else:
        stale = not verify_password(plain, existing["password_hash"])
    if stale:
        db.execute("UPDATE users SET password_hash = ? WHERE id = ?",
                   (desired, existing["id"]))
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
