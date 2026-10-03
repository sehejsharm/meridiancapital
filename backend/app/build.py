"""Which code the server is running.

The installer copies the code without its git history, so it writes the
commit into a BUILD file beside it. A checkout run directly (development)
asks git instead.

The deck asks "is the server updated?" by comparing a fingerprint of the
server's code with the same fingerprint taken when the dashboard was built
(frontend/backend-fingerprint.mjs computes it identically). Comparing commits
instead said "update it" after every dashboard-only change, when the server
had nothing new to take.
"""

from __future__ import annotations

import hashlib
import subprocess
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _git(*args: str) -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", str(ROOT), *args], capture_output=True, text=True, timeout=5, check=True
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return out or None


# What counts as the server's code: every module it runs, and its pins.
CODE_DIRS = ("app", "engine", "shared")
CODE_FILES = ("requirements.txt",)


def code_fingerprint(backend: Path | None = None) -> str | None:
    """sha256 over the server's code (path, then content, in path order); 12 hex.

    Must match frontend/backend-fingerprint.mjs byte for byte.
    """
    backend = backend or ROOT / "backend"
    files: list[str] = []
    for d in CODE_DIRS:
        base = backend / d
        if base.is_dir():
            files += [
                p.relative_to(backend).as_posix()
                for p in base.rglob("*.py")
                if p.is_file() and "__pycache__" not in p.parts
            ]
    files += [f for f in CODE_FILES if (backend / f).is_file()]
    if not files:
        return None
    h = hashlib.sha256()
    for rel in sorted(files):
        h.update(rel.encode("utf-8") + b"\0")
        h.update((backend / rel).read_bytes() + b"\0")
    return h.hexdigest()[:12]


@lru_cache(maxsize=1)
def build_info() -> dict:
    """{"commit", "committed", "fingerprint"}; any of them None if unknown."""
    return {**_commit(), "fingerprint": code_fingerprint()}


def _commit() -> dict:
    stamp = ROOT / "BUILD"
    try:
        parts = stamp.read_text(encoding="utf-8").split()
    except OSError:
        parts = []
    if parts:
        return {"commit": parts[0][:7], "committed": parts[1] if len(parts) > 1 else None}
    commit = _git("rev-parse", "--short=7", "HEAD")
    return {"commit": commit[:7] if commit else None, "committed": _git("log", "-1", "--format=%cI")}
