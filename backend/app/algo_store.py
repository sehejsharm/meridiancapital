"""Uploaded algorithm source: where it lives and how it is gated.

Source is held in the database rather than on disk so that a version, its
acceptance report move together and survive a redeploy
of the VM. It is written to a file only when an engine is about to run it, into
a directory the engine user can read and nothing else can.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

from engine.config import DATA_DIR

GATE_TIMEOUT_SEC = 90
MAX_SOURCE_BYTES = 512 * 1024

ALGO_DIR = DATA_DIR / "algos"
SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(name: str) -> str:
    slug = SLUG_RE.sub("-", name.strip().lower()).strip("-")
    return slug[:40] or "algo"


def sha256(source: str) -> str:
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def run_gate(source: str, backend_dir: Path | None = None) -> dict:
    """Run the acceptance gate in a separate process and return its report.

    Never import the candidate in this process: a module body executes on
    import, and this process holds the broker session.
    """
    cwd = backend_dir or Path(__file__).resolve().parent.parent
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "engine.gate_runner"],
            input=source,
            capture_output=True,
            text=True,
            cwd=str(cwd),
            timeout=GATE_TIMEOUT_SEC,
        )
    except subprocess.TimeoutExpired:
        return {
            "passed": False,
            "error": (
                f"the gate did not finish within {GATE_TIMEOUT_SEC}s — the strategy is "
                f"most likely looping or blocking at import"
            ),
            "total": 0, "failed": 0, "checks": [],
        }

    if not proc.stdout.strip():
        detail = (proc.stderr or "").strip()[-400:]
        return {
            "passed": False,
            "error": (
                "the gate process was killed before it could report — this usually means "
                "the strategy exhausted the CPU or memory limit"
                + (f": {detail}" if detail else "")
            ),
            "total": 0, "failed": 0, "checks": [],
        }
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {
            "passed": False,
            "error": "the gate produced unreadable output",
            "total": 0, "failed": 0, "checks": [],
        }


def materialise(algo_id: str, version: int, source: str) -> Path:
    """Write a gated version to disk so an engine process can import it."""
    ALGO_DIR.mkdir(parents=True, exist_ok=True)
    path = ALGO_DIR / f"{algo_id}_v{version}.py"
    # The file is left read-only, so a second start of the same version cannot
    # rewrite it in place — as a non-root service user that is a permission
    # error. Identical content is left alone; anything else is replaced.
    if path.exists() and path.read_text(encoding="utf-8") == source:
        return path
    path.unlink(missing_ok=True)
    path.write_text(source, encoding="utf-8")
    path.chmod(0o440)
    return path
