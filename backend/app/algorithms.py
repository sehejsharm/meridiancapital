"""Uploading and validating a replacement algorithm.

The bot runs whatever module the supervisor points at. This lets that module
be replaced from the app — but never blindly. An upload is stored, checked,
and only becomes runnable if the checks pass. The built-in v11 is always
present as a version you can fall back to.

What the checks can and cannot tell you
---------------------------------------
They verify that the file is valid Python, that everything it imports is
actually installed, that it has an entry point the supervisor can invoke, and
that it survives being imported in a subprocess. That catches the failures
that would otherwise surface at 09:15 with real money on the line.

They cannot tell you the strategy is *sound*. Nothing here backtests it,
checks its risk maths, or knows whether it will lose money. A file can pass
every check and still be a bad idea.
"""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from .config import settings

BUILTIN_ID = "builtin-v11"
BUILTIN_MODULE = "app.bot.strategy"

MAX_UPLOAD_BYTES = 2 * 1024 * 1024        # 2 MB; the reference algorithm is ~60 KB
IMPORT_TIMEOUT_SECONDS = 120              # pandas/scipy import is slow on a small box

# Modules the standard library provides — never flagged as missing.
_STDLIB = set(getattr(sys, "stdlib_module_names", set()))

# Patterns worth pointing out. These are warnings, not refusals: it is the
# user's own server and their own code, and a legitimate algorithm may well
# shell out or write files. The point is that they see it before it runs.
RISKY_CALLS = {
    "eval": "evaluates arbitrary strings at runtime",
    "exec": "executes arbitrary strings at runtime",
    "compile": "compiles code at runtime",
    "__import__": "imports by name at runtime, which the import check cannot see",
}
RISKY_ATTRS = {
    "os.system": "runs a shell command",
    "os.remove": "deletes files",
    "os.rmdir": "removes directories",
    "os.unlink": "deletes files",
    "shutil.rmtree": "deletes a directory tree",
    "subprocess.call": "runs an external process",
    "subprocess.run": "runs an external process",
    "subprocess.Popen": "runs an external process",
    "socket.socket": "opens a raw network socket",
}


@dataclass
class Check:
    name: str
    passed: bool
    fatal: bool                    # a failed fatal check blocks activation
    detail: str = ""
    items: list[str] = field(default_factory=list)
    # Where in the file, when it can be pinned down. The editor puts a marker
    # on this line — "line 214: unexpected indent" beside the line beats the
    # same sentence in a panel underneath a 600-line file.
    line: Optional[int] = None

    def as_dict(self) -> dict:
        return {
            "name": self.name, "passed": self.passed, "fatal": self.fatal,
            "detail": self.detail, "items": self.items, "line": self.line,
        }


def _dir() -> Path:
    d = settings.data_dir / "algorithms"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _index_path() -> Path:
    return _dir() / "index.json"


def _load_index() -> dict:
    try:
        with open(_index_path()) as f:
            idx = json.load(f)
    except Exception:
        idx = {"active": BUILTIN_ID, "versions": []}
    idx.setdefault("versions", [])
    # Slot assignments arrived after the single-algorithm index. An older file
    # has only "active", which described what is now slot 0, so it is adopted
    # as such rather than resetting a live bot to the built-in.
    if "slots" not in idx:
        idx["slots"] = {"0": idx.get("active", BUILTIN_ID)}
    return idx


def slot_active(slot: int = 0) -> Optional[str]:
    """The version id assigned to a slot, or None when the slot is empty.

    Slot 0 defaults to the built-in so the original bot keeps running with no
    configuration. Slots 1-4 default to empty — inheriting slot 0's algorithm
    would silently double the position size.
    """
    idx = _load_index()
    val = (idx.get("slots") or {}).get(str(slot))
    if val is None and slot == 0:
        return BUILTIN_ID
    return val


def _save_index(idx: dict) -> None:
    tmp = _index_path().with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(idx, f, indent=2)
    tmp.replace(_index_path())


# ---------------------------------------------------------------- validation


def _top_level_imports(tree: ast.AST) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level:          # relative — handled by _relative_imports
                continue
            if node.module:
                found.add(node.module.split(".")[0])
    return found


def _relative_imports(tree: ast.AST) -> list[str]:
    """Relative imports cannot work in an uploaded algorithm.

    The built-in runs as `python -m app.bot.strategy`, so `from .emitter
    import emit` resolves inside the package. An upload runs as a standalone
    file with no parent package, where the same line raises ImportError. Worth
    saying plainly rather than letting the smoke test report it cryptically.
    """
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level:
            dots = "." * node.level
            out.append(f"line {node.lineno}: from {dots}{node.module or ''} import ...")
    return out


def _dotted(node: ast.AST) -> str:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value           # type: ignore[assignment]
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def _scan_risky(tree: ast.AST) -> list[str]:
    hits: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name) and node.func.id in RISKY_CALLS:
            hits.append(f"line {node.lineno}: {node.func.id}() — {RISKY_CALLS[node.func.id]}")
        elif isinstance(node.func, ast.Attribute):
            dotted = _dotted(node.func)
            if dotted in RISKY_ATTRS:
                hits.append(f"line {node.lineno}: {dotted}() — {RISKY_ATTRS[dotted]}")
    return sorted(set(hits))


def _has_entry_point(tree: ast.AST) -> tuple[bool, str]:
    """The supervisor runs the file as a script, so it needs something to run."""
    has_main_func = any(
        isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "main"
        for n in ast.iter_child_nodes(tree)
    )
    has_main_guard = False
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.If):
            test = ast.dump(node.test)
            if "__name__" in test and "__main__" in test:
                has_main_guard = True
    if has_main_guard:
        return True, "runs under `if __name__ == \"__main__\"`"
    if has_main_func:
        return True, "defines main(), which will be called"
    return False, "no `if __name__ == \"__main__\"` block and no main() function"


def _missing_dependencies(imports: set[str]) -> list[str]:
    missing = []
    for name in sorted(imports):
        if name in _STDLIB or name in ("app", "__future__"):
            continue
        try:
            if importlib.util.find_spec(name) is None:
                missing.append(name)
        except (ImportError, ValueError, ModuleNotFoundError):
            missing.append(name)
    return missing


def _import_smoke_test(path: Path) -> Check:
    """Import the file in a clean subprocess and see whether it survives.

    Importing runs module-level code — the constants, the imports, any setup —
    but not `main()`, because `__name__` is not `"__main__"`. So this catches
    a broken import or a crash at module scope without ever letting the
    strategy place an order or touch the broker.
    """
    script = (
        "import importlib.util, sys\n"
        "spec = importlib.util.spec_from_file_location('candidate_algo', sys.argv[1])\n"
        "mod = importlib.util.module_from_spec(spec)\n"
        "sys.modules['candidate_algo'] = mod\n"
        "spec.loader.exec_module(mod)\n"
        "print('IMPORT_OK')\n"
    )
    env = {k: v for k, v in os.environ.items() if not k.startswith("ANGEL_")}
    env["MERIDIAN_VALIDATING"] = "1"
    env["PYTHONUNBUFFERED"] = "1"

    try:
        proc = subprocess.run(
            [sys.executable, "-c", script, str(path)],
            cwd=str(Path(__file__).resolve().parent.parent),
            env=env, capture_output=True, text=True,
            timeout=IMPORT_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return Check(
            "Imports without hanging", False, True,
            f"Still running after {IMPORT_TIMEOUT_SECONDS}s. Module-level code "
            "should not block — put long work inside main().",
        )

    if "IMPORT_OK" in proc.stdout:
        return Check("Imports without crashing", True, True,
                     "Module-level code ran cleanly in a sandbox.")

    tail = (proc.stderr or proc.stdout or "").strip().splitlines()
    reason = tail[-1] if tail else f"exit code {proc.returncode}"
    return Check("Imports without crashing", False, True, reason,
                 items=[l for l in tail[-12:] if l.strip()])


def validate(source: str, filename: str = "algorithm.py") -> dict:
    """Run every check and report. Never raises for bad user code."""
    checks: list[Check] = []

    size_ok = len(source.encode()) <= MAX_UPLOAD_BYTES
    checks.append(Check(
        "Within size limit", size_ok, True,
        f"{len(source.encode())/1024:.0f} KB"
        + ("" if size_ok else f" — limit is {MAX_UPLOAD_BYTES//1024} KB"),
    ))
    if not size_ok:
        return _report(checks, source, filename)

    # ---- syntax ----
    try:
        tree = ast.parse(source, filename=filename)
        checks.append(Check("Valid Python syntax", True, True,
                            f"Parsed {len(source.splitlines())} lines."))
    except SyntaxError as exc:
        checks.append(Check(
            "Valid Python syntax", False, True,
            f"Line {exc.lineno}: {exc.msg}",
            items=[exc.text.rstrip()] if exc.text else [],
            line=exc.lineno,
        ))
        return _report(checks, source, filename)

    # ---- entry point ----
    ok, detail = _has_entry_point(tree)
    checks.append(Check("Has an entry point", ok, True, detail))

    # ---- relative imports ----
    relative = _relative_imports(tree)
    checks.append(Check(
        "No relative imports", not relative, True,
        "None found." if not relative else
        "An uploaded algorithm runs as a standalone file with no parent "
        "package, so relative imports raise ImportError. Inline what you need, "
        "or import an installed package by its absolute name.",
        items=relative,
    ))

    # ---- dependencies ----
    imports = _top_level_imports(tree)
    missing = _missing_dependencies(imports)
    third_party = sorted(i for i in imports if i not in _STDLIB and i != "app")
    checks.append(Check(
        "Dependencies installed", not missing, True,
        "All imports resolve." if not missing
        else f"{len(missing)} module(s) not installed on the server.",
        items=missing or third_party,
    ))

    # ---- event protocol (advisory) ----
    emits = "@@EVT@@" in source or "from .emitter import" in source or "emitter" in source
    checks.append(Check(
        "Emits dashboard events", emits, False,
        "Structured events found — the dashboard will show positions and trades."
        if emits else
        "No @@EVT@@ output. The bot will still run and the Live feed will show "
        "its printed output, but the dashboard cards, Trades and Reports stay "
        "empty because nothing tells them what happened.",
    ))

    # ---- risky operations (advisory) ----
    risky = _scan_risky(tree)
    checks.append(Check(
        "No risky operations", not risky, False,
        "Nothing unusual found." if not risky
        else f"{len(risky)} call(s) worth a look before you run this.",
        items=risky,
    ))

    # ---- smoke test, only if everything fatal so far passed ----
    if all(c.passed for c in checks if c.fatal):
        tmp = _dir() / f".validate-{hashlib.sha256(source.encode()).hexdigest()[:12]}.py"
        try:
            tmp.write_text(source)
            checks.append(_import_smoke_test(tmp))
        finally:
            tmp.unlink(missing_ok=True)
    else:
        checks.append(Check("Imports without crashing", False, True,
                            "Skipped — an earlier check failed."))

    return _report(checks, source, filename)


def _report(checks: list[Check], source: str, filename: str) -> dict:
    fatal_failed = [c for c in checks if c.fatal and not c.passed]
    warnings = [c for c in checks if not c.fatal and not c.passed]
    return {
        "ok": not fatal_failed,
        "filename": filename,
        "sha256": hashlib.sha256(source.encode()).hexdigest(),
        "lines": len(source.splitlines()),
        "bytes": len(source.encode()),
        "checks": [c.as_dict() for c in checks],
        "blocking": [c.name for c in fatal_failed],
        "warnings": [c.name for c in warnings],
        "summary": (
            "Ready to activate."
            + (f" {len(warnings)} warning(s) — worth reading first."
               if warnings else "")
        ) if not fatal_failed else
        f"Cannot activate: {', '.join(c.name for c in fatal_failed)}.",
    }


# ---------------------------------------------------------------- versions


def list_versions(max_slots: int = 5) -> dict:
    idx = _load_index()
    slots = idx.get("slots") or {}
    assigned = {str(k): v for k, v in slots.items()}

    def slots_using(vid: str) -> list[int]:
        return sorted(int(k) for k, v in assigned.items() if v == vid)

    versions = [{
        "id": BUILTIN_ID,
        "name": "Built-in v11 (original)",
        "uploaded_at": None,
        "uploaded_by": "shipped",
        "lines": None,
        "slots": slots_using(BUILTIN_ID),
        "builtin": True,
        "warnings": [],
    }]
    for v in idx.get("versions", []):
        versions.append({**v, "slots": slots_using(v["id"]), "builtin": False})

    return {
        "versions": sorted(versions, key=lambda v: v.get("uploaded_at") or "", reverse=True),
        "slots": [
            {
                "slot": i,
                "algorithm": slot_active(i),
                "description": active_description(i),
                "empty": slot_active(i) is None,
            }
            for i in range(max_slots)
        ],
    }


def save_version(source: str, name: str, uploaded_by: str, report: dict) -> dict:
    if not report.get("ok"):
        raise ValueError("Refusing to store an algorithm that failed validation.")

    idx = _load_index()
    version_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = _dir() / f"{version_id}.py"
    path.write_text(source)

    entry = {
        "id": version_id,
        "name": (name or "Uploaded algorithm").strip()[:80],
        "uploaded_at": datetime.now().isoformat(timespec="seconds"),
        "uploaded_by": uploaded_by,
        "lines": report["lines"],
        "sha256": report["sha256"],
        "warnings": report.get("warnings", []),
        "file": path.name,
    }
    idx.setdefault("versions", []).append(entry)
    # Keep the last 20 uploads; the built-in is always available regardless.
    if len(idx["versions"]) > 20:
        for old in idx["versions"][:-20]:
            (_dir() / old["file"]).unlink(missing_ok=True)
        idx["versions"] = idx["versions"][-20:]
    _save_index(idx)
    return entry


def get_source(version_id: str) -> Optional[str]:
    if version_id == BUILTIN_ID:
        builtin = Path(__file__).resolve().parent / "bot" / "strategy.py"
        return builtin.read_text() if builtin.exists() else None
    for v in _load_index().get("versions", []):
        if v["id"] == version_id:
            p = _dir() / v["file"]
            return p.read_text() if p.exists() else None
    return None


def activate(version_id: Optional[str], slot: int = 0) -> dict:
    """Assign a version to a slot. `version_id=None` empties the slot."""
    idx = _load_index()
    idx.setdefault("slots", {})

    if version_id is None:
        if slot == 0:
            raise ValueError(
                "Slot 1 always runs something — assign the built-in rather than "
                "emptying it."
            )
        idx["slots"].pop(str(slot), None)
    else:
        if version_id != BUILTIN_ID:
            known = {v["id"] for v in idx.get("versions", [])}
            if version_id not in known:
                raise ValueError(f"No such version: {version_id}")
            if not (_dir() / f"{version_id}.py").exists():
                raise ValueError("That version's file is missing from disk.")
        idx["slots"][str(slot)] = version_id

    if slot == 0:                       # keep the legacy key honest
        idx["active"] = idx["slots"].get("0", BUILTIN_ID)
    _save_index(idx)
    return list_versions()


def delete_version(version_id: str) -> dict:
    if version_id == BUILTIN_ID:
        raise ValueError("The built-in algorithm cannot be deleted.")
    idx = _load_index()
    # Checking only the legacy "active" key would happily delete the file that
    # slot 4 is running, so every slot assignment is consulted.
    using = sorted(int(k) for k, v in (idx.get("slots") or {}).items() if v == version_id)
    if using:
        where = ", ".join(f"slot {s + 1}" for s in using)
        raise ValueError(f"That version is assigned to {where}. Change it there first.")
    remaining = []
    for v in idx.get("versions", []):
        if v["id"] == version_id:
            (_dir() / v["file"]).unlink(missing_ok=True)
        else:
            remaining.append(v)
    idx["versions"] = remaining
    _save_index(idx)
    return list_versions()


def active_target(slot: int = 0) -> tuple[str, Optional[str]]:
    """What the supervisor should launch for a slot.

    Returns `(kind, value)` — `("module", "app.bot.strategy")` for the built-in,
    `("file", "/path/to/version.py")` for an upload, or `("none", None)` when
    the slot has nothing assigned and must not start.
    """
    active = slot_active(slot)
    if active is None:
        return "none", None
    if active == BUILTIN_ID:
        return "module", BUILTIN_MODULE
    path = _dir() / f"{active}.py"
    if not path.exists():
        # The assigned upload vanished from disk. Slot 0 falls back to the
        # built-in rather than refusing to trade; an empty slot stays empty.
        return ("module", BUILTIN_MODULE) if slot == 0 else ("none", None)
    return "file", str(path)


# The starter file and the authoring brief live next to the bot as real files
# rather than as strings in here: the starter has to stay a runnable, testable
# .py, and the brief is long-form prose that reads badly escaped inside Python.
_BOT_DIR = Path(__file__).resolve().parent / "bot"
TEMPLATE_PATH = _BOT_DIR / "template_starter.py"
BRIEF_PATH = _BOT_DIR / "CONTRACT.md"


def template() -> str:
    """The runnable starter algorithm."""
    return TEMPLATE_PATH.read_text(encoding="utf-8")


def brief() -> str:
    """The authoring brief — everything Claude needs to write a working file."""
    return BRIEF_PATH.read_text(encoding="utf-8")


def active_description(slot: int = 0) -> str:
    active = slot_active(slot)
    if active is None:
        return "Empty — no algorithm assigned"
    if active == BUILTIN_ID:
        return "Built-in v11 (original)"
    for v in _load_index().get("versions", []):
        if v["id"] == active:
            return f"{v['name']} ({v['id']})"
    return "Built-in v11 (original)" if slot == 0 else "Empty — no algorithm assigned"
