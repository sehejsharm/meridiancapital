"""The environment handed to the processes the API starts.

The API holds secrets that are its own: the dashboard's signing key and PIN hash,
the relay secret, the backup passphrase and upload address. No program it starts
has any use for them, so none is given them: a program that prints its
environment, or a traceback that dumps it, cannot leak a way into the dashboard.

The acceptance gate imports uploaded code that has not been trusted yet, so it
gets less still: no broker credential, no alert channel, nothing secret at all.
It only checks a strategy against recorded data and needs none of it.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping

# Read only by the API itself.
API_ONLY = frozenset({
    "MERIDIAN_JWT_SECRET",
    "MERIDIAN_PASSWORD_HASH",
    "MERIDIAN_RELAY_SECRET",
    "MERIDIAN_SESSION_SECRET",
    "MERIDIAN_BACKUP_PASSPHRASE",
    "MERIDIAN_BACKUP_URL",
    "MERIDIAN_BACKUP_REMOTE",
})

# Anything named like a secret, whatever it is for.
_SECRET_NAME = re.compile(r"SECRET|TOKEN|PASSW|PASSPHRASE|PRIVATE|CREDENTIAL|API_?KEY|WEBHOOK|_URL$|_PIN$|TOTP",
                          re.IGNORECASE)


def for_program(base: Mapping[str, str] | None = None) -> dict[str, str]:
    """A trading program's environment: everything but the API's own secrets.
    It keeps the broker credentials it trades with."""
    env = dict(os.environ if base is None else base)
    for name in API_ONLY:
        env.pop(name, None)
    return env


def for_gate(base: Mapping[str, str] | None = None) -> dict[str, str]:
    """The acceptance gate's environment: nothing secret."""
    env = for_program(base)
    for name in list(env):
        if name.startswith(("ANGEL_", "MERIDIAN_TELEGRAM_")) or _SECRET_NAME.search(name):
            env.pop(name)
    return env
