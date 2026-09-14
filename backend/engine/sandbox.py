"""Static screening of uploaded strategy code.

This runs BEFORE the module is ever imported, because importing is already
execution: a module body runs at import time, so a malicious upload does not
need its functions called to do damage.

The policy is an allowlist, not a blocklist. A blocklist of scary names is
trivially defeated (``getattr(__builtins__, 'ev' + 'al')``), so instead: only
named-safe modules may be imported, and a short list of escape-hatch builtins
and dunder attributes is refused outright.

This is a screen, not a jail. It raises the cost of a hostile upload and it
catches honest mistakes, but a determined attacker with the ability to upload
Python to a machine that holds brokerage credentials is a serious problem that
static analysis alone does not solve. The real containment is that the gate
runs in a separate process with no network and hard resource limits, and that
the operator is the only one who can upload at all.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field

# Modules a strategy has any legitimate need for: maths, data, time, structure.
# Nothing here can touch the filesystem, the network, or another process.
ALLOWED_MODULES = frozenset(
    {
        "math", "statistics", "decimal", "fractions", "random",
        "datetime", "time", "calendar", "zoneinfo",
        "itertools", "functools", "operator", "collections", "heapq", "bisect",
        "dataclasses", "enum", "typing", "types", "abc", "numbers",
        "re", "json", "string", "textwrap", "copy", "uuid",
        "pandas", "numpy", "scipy", "ta",
        "__future__",
    }
)

# Names that hand back arbitrary execution or the import machinery.
BANNED_NAMES = frozenset(
    {
        "eval", "exec", "compile", "__import__", "open", "input", "breakpoint",
        "globals", "locals", "vars", "memoryview",
        "exit", "quit", "help", "license", "credits",
    }
)

# Attribute paths that walk out of the sandbox through the object graph.
BANNED_ATTRS = frozenset(
    {
        "__subclasses__", "__globals__", "__builtins__", "__code__", "__closure__",
        "__mro__", "__bases__", "__dict__", "__getattribute__", "__reduce__",
        "__reduce_ex__", "__class__", "__self__", "__func__", "__loader__",
        "__spec__", "__module__", "f_globals", "f_locals", "f_back", "gi_frame",
    }
)


@dataclass
class ScanResult:
    ok: bool
    errors: list[str] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"ok": self.ok, "errors": self.errors, "imports": sorted(set(self.imports))}


def _root(module: str) -> str:
    return module.split(".")[0]


class _Visitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.imports: list[str] = []

    def _at(self, node: ast.AST) -> str:
        return f"line {getattr(node, 'lineno', '?')}"

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.imports.append(alias.name)
            if _root(alias.name) not in ALLOWED_MODULES:
                self.errors.append(
                    f"{self._at(node)}: import of '{alias.name}' is not permitted "
                    f"— a strategy may only use maths, data and time modules"
                )
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        mod = node.module or ""
        self.imports.append(mod)
        if node.level:
            self.errors.append(f"{self._at(node)}: relative imports are not permitted")
        elif _root(mod) not in ALLOWED_MODULES:
            self.errors.append(
                f"{self._at(node)}: import from '{mod}' is not permitted"
            )
        for alias in node.names:
            if alias.name == "*":
                self.errors.append(f"{self._at(node)}: star imports are not permitted")
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Load) and node.id in BANNED_NAMES:
            self.errors.append(f"{self._at(node)}: use of '{node.id}' is not permitted")
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr in BANNED_ATTRS:
            self.errors.append(
                f"{self._at(node)}: attribute '{node.attr}' is not permitted"
            )
        self.generic_visit(node)

    # A strategy declares behaviour; it has no reason to rebind the interpreter.
    def visit_Global(self, node: ast.Global) -> None:
        self.errors.append(f"{self._at(node)}: 'global' is not permitted")
        self.generic_visit(node)

    def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
        self.errors.append(f"{self._at(node)}: 'nonlocal' is not permitted")
        self.generic_visit(node)


def scan(source: str) -> ScanResult:
    """Screen source for anything a strategy has no business doing."""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return ScanResult(ok=False, errors=[f"line {exc.lineno}: syntax error — {exc.msg}"])

    v = _Visitor()
    v.visit(tree)
    return ScanResult(ok=not v.errors, errors=v.errors, imports=v.imports)
