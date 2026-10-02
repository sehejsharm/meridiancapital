"""Which commit of this repository the server is running.

The installer copies the code without its git history, so it writes the
commit into a BUILD file beside it. A checkout run directly (development)
asks git instead. The deck compares this with the dashboard's own commit, so
"is the server updated?" is answered on screen rather than by guesswork.
"""

from __future__ import annotations

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


@lru_cache(maxsize=1)
def build_info() -> dict:
    """{"commit": "e032604", "committed": ISO time or None}; commit is None if unknown."""
    stamp = ROOT / "BUILD"
    try:
        parts = stamp.read_text(encoding="utf-8").split()
    except OSError:
        parts = []
    if parts:
        return {"commit": parts[0][:7], "committed": parts[1] if len(parts) > 1 else None}
    commit = _git("rev-parse", "--short=7", "HEAD")
    return {"commit": commit[:7] if commit else None, "committed": _git("log", "-1", "--format=%cI")}
