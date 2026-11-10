"""Standalone trading programs, run exactly as written.

The dashboard runs two kinds of algorithm. A *strategy module* answers the
engine's questions (signal, strike, stop, size, guards) and the engine does the
trading. A *program* is a complete trader — it logs in to Angel, reads the
market and places its own orders — and is started as its own process, the same
way it would be run by hand, with the operator's paper/live choice passed in.

What the dashboard still owns for a program: starting and stopping it, starting
it at the open and stopping it after the close, restarting it if it crashes,
refusing a second process on real money, and showing its output in the journal.
What it cannot do is reach inside it: its orders, positions and P&L are its
own, so the emergency stop can interrupt a program but not square off for it.
"""

from __future__ import annotations

import ast
import importlib.util
import os
import sys
from pathlib import Path

from engine.config import DATA_DIR
from engine.contract import missing_members

PROGRAM_DIR = DATA_DIR / "programs"

# Import name -> the package pip installs it from, where the two differ.
PIP_NAMES = {
    "SmartApi": "smartapi-python",
    "yaml": "pyyaml",
    "sklearn": "scikit-learn",
    "cv2": "opencv-python",
    "PIL": "pillow",
    "dateutil": "python-dateutil",
    "talib": "TA-Lib",
    "dotenv": "python-dotenv",
}


def _tree(source: str) -> ast.Module | None:
    try:
        return ast.parse(source)
    except SyntaxError:
        return None


def has_main_guard(source: str) -> bool:
    """True if the file runs itself: a top-level `if __name__ == "__main__":`."""
    tree = _tree(source)
    if tree is None:
        return False
    for node in tree.body:
        if not isinstance(node, ast.If):
            continue
        test = node.test
        if (
            isinstance(test, ast.Compare)
            and isinstance(test.left, ast.Name)
            and test.left.id == "__name__"
            and any(isinstance(c, ast.Constant) and c.value == "__main__" for c in test.comparators)
        ):
            return True
    return False


# Facts read out of a stored version's source, kept per (database, version id).
# Parsing a 4,000-line program takes 60-130 ms, and the pages that list algorithms
# ask every few seconds: on the VM that also runs the live trader that was the same
# file parsed some 500 times an hour. A stored version's source never changes and
# its id is never reused, so the id is a sound key.
_FACTS: dict[tuple[str, int, str], object] = {}
_FACTS_MAX = 256


def cached_fact(db, version: dict | None, name: str, compute):
    """``compute(source)`` for this stored version, computed once.

    ``version`` is a row from ``db.version_meta`` (no source) or ``db.version``;
    the source is read from the database only when the fact is not known yet.
    """
    if not version or version.get("id") is None:
        return None
    key = (str(getattr(db, "path", "")), int(version["id"]), name)
    if key in _FACTS:
        return _FACTS[key]
    source = version.get("source")
    if source is None:
        full = db.version(int(version["id"]))
        source = (full or {}).get("source") or ""
    value = compute(source)
    if len(_FACTS) >= _FACTS_MAX:
        _FACTS.pop(next(iter(_FACTS)))
    _FACTS[key] = value
    return value


def runtime_of_version(db, version: dict | None) -> str:
    """``runtime_of`` for a stored version, without re-reading or re-parsing its source."""
    return cached_fact(db, version, "runtime", runtime_of) or "invalid"


def runtime_of(source: str) -> str:
    """"strategy", "program", or "invalid" — decided from the text, never by running it."""
    if not missing_members(source):
        return "strategy"
    if has_main_guard(source):
        return "program"
    return "invalid"


def mode_arguments(source: str) -> dict[str, list[str]] | None:
    """How to tell this program paper from live.

    `--paper` / `--live` flags when it defines them; otherwise the
    MERIDIAN_TRADING_MODE environment variable, if it reads it. A program with
    neither cannot be told which money to use — started "on paper" it might
    trade real money — so it is not started at all.
    """
    if '"--paper"' in source and '"--live"' in source or "'--paper'" in source and "'--live'" in source:
        return {"paper": ["--paper"], "live": ["--live"]}
    if "MERIDIAN_TRADING_MODE" in source:
        return {"paper": [], "live": []}
    return None


def missing_packages(source: str) -> list[str]:
    """Third-party modules the program imports that are not installed here."""
    tree = _tree(source)
    if tree is None:
        return []
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module.split(".")[0])
    third_party = sorted(n for n in names if n not in sys.stdlib_module_names and n != "__future__")
    missing = []
    for name in third_party:
        try:
            found = importlib.util.find_spec(name) is not None
        except (ImportError, ValueError):
            found = False
        if not found:
            missing.append(name)
    return missing


def install_hint(missing: list[str], python_bin: str) -> str:
    pip = Path(python_bin).parent / "pip"
    pkgs = " ".join(PIP_NAMES.get(m, m) for m in missing)
    return f"sudo -u meridian {pip} install {pkgs}"


def program_path(algo_id: str) -> Path:
    """One stable home per algorithm, whatever the version.

    Programs keep their own state beside themselves — an open position, today's
    trade count — so every version of one algorithm must run from the same
    folder, or a new upload would forget a live position.
    """
    return PROGRAM_DIR / algo_id / f"{algo_id}.py"


def materialise_program(algo_id: str, source: str) -> Path:
    path = program_path(algo_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    if path.exists() and path.read_text(encoding="utf-8") == source:
        return path
    path.unlink(missing_ok=True)
    path.write_text(source, encoding="utf-8")
    path.chmod(0o600)
    return path


def is_program_cmdline(cmdline: str) -> bool:
    return str(PROGRAM_DIR) in cmdline
