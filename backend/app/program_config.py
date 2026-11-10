"""What a standalone program is actually running, beside what the desk shows.

The Strategy page renders the built-in engine's parameters (engine/config.py,
Config #5 v3: take-profit 90 index points, break-even at +25%, trail from +35%)
and tells the operator every value there was fixed by the backtest. The program
placing the real-money trades is an uploaded file that carries its own
constants and never reads that form. On 6 Nov 2026 it logged, at startup,
break-even at +20%, a trail riding 20% behind the peak and no take-profit — the
page disagreed with the live program on three numbers and said nothing about it.

This reads the two things that describe a program's configuration:

* **running** — the parameter block the program prints at every startup
  ("PROFIT LADDER (per trade): ..."), read back out of the journal. That is what
  the process in memory is actually using.
* **code** — the same constants read out of the algorithm's active uploaded
  version (parsed, never imported or executed).

and compares them. They disagree when the process was started from an older
upload, or a value was changed in the code since — the live process and the
desk's idea of it have drifted, which the deck flags loudly.
"""

from __future__ import annotations

import ast
import re
from typing import Any

from engine import tuning

# Keys a program's configuration is described by, with how to show each.
KINDS: dict[str, str] = {p.key: p.kind for p in tuning.PARAMS}
KINDS.update({"LATE_TP_AFTER_MIN": "int", "LATE_TP_FRAC": "pct", "RUNNER_AT": "pct", "RUNNER_FRAC": "pct"})
LABELS: dict[str, str] = {p.key: p.label for p in tuning.PARAMS}
LABELS.update({"LATE_TP_AFTER_MIN": "Late take-profit after (min)", "LATE_TP_FRAC": "Late take-profit level",
               "RUNNER_AT": "Runner lock from", "RUNNER_FRAC": "Runner lock trail"})
# A key the program is known to print at startup; the rest come from its code only.
LOGGED = (
    "TARGET_PTS", "BE_TRIGGER", "TRAIL_FRAC", "LATE_TP_AFTER_MIN", "LATE_TP_FRAC", "RUNNER_AT", "RUNNER_FRAC", "STOP_FRAC",
    "PER_TRADE_RISK_RS", "DAILY_LOSS_LIMIT_RS", "WEEKLY_LOSS_LIMIT_RS", "CONSEC_LOSS_HALT",
    "DAILY_PROFIT_LOCK_RS", "MAX_TRADES_DAY", "MAX_DRAWDOWN_STOP", "DEPLOY_FRACTION",
    "PER_TRADE_EQUITY_CAP", "MAX_LOTS", "ENTRY_START", "ENTRY_CUTOFF", "FORCE_CLOSE",
    "MIN_DTE", "MAX_DTE",
)
_TIMES = ("ENTRY_START", "ENTRY_CUTOFF", "FORCE_CLOSE")

_PREFIX = re.compile(r"^\[\d{2}:\d{2}(?::\d{2})?\]\s*")
BLOCK_START = "PROFIT LADDER"
MAX_BLOCK_LINES = 30


def _rs(s: str) -> float:
    return float(s.replace(",", ""))


_LOG_RULES: list[tuple[re.Pattern, Any]] = [
    (re.compile(r"take-profit:\s*FIXED \+(\d+)"), lambda m: {"TARGET_PTS": int(m.group(1))}),
    (re.compile(r"take-profit:\s*(none|no all-day target)"), lambda m: {"TARGET_PTS": None}),
    (re.compile(r"break-even\s*:\s*at \+(\d+(?:\.\d+)?)%"), lambda m: {"BE_TRIGGER": float(m.group(1)) / 100}),
    (re.compile(r"trail\s*:\s*past \+(\d+(?:\.\d+)?)% gain"), lambda m: {"TRAIL_FRAC": float(m.group(1)) / 100}),
    (re.compile(r"late TP\s*:\s*from (\d+) min after entry, sell at \+(\d+(?:\.\d+)?)%"),
     lambda m: {"LATE_TP_AFTER_MIN": int(m.group(1)), "LATE_TP_FRAC": float(m.group(2)) / 100}),
    # v10: "runner lock: past +90% gain, stop rides 10% behind peak"
    (re.compile(r"runner lock\s*:\s*past \+(\d+(?:\.\d+)?)% gain, stop rides (\d+(?:\.\d+)?)%"),
     lambda m: {"RUNNER_AT": float(m.group(1)) / 100, "RUNNER_FRAC": float(m.group(2)) / 100}),
    (re.compile(r"hard stop\s*:\s*-(\d+(?:\.\d+)?)%"), lambda m: {"STOP_FRAC": float(m.group(1)) / 100}),
    (re.compile(r"rupee cap\s*:.*?Rs ([\d,]+(?:\.\d+)?)"), lambda m: {"PER_TRADE_RISK_RS": _rs(m.group(1))}),
    (re.compile(r"daily kill\s*:.*?Rs ([\d,]+(?:\.\d+)?)"), lambda m: {"DAILY_LOSS_LIMIT_RS": _rs(m.group(1))}),
    (re.compile(r"weekly kill\s*:.*?Rs ([\d,]+(?:\.\d+)?)"), lambda m: {"WEEKLY_LOSS_LIMIT_RS": _rs(m.group(1))}),
    (re.compile(r"streak halt\s*:.*?after (\d+)"), lambda m: {"CONSEC_LOSS_HALT": int(m.group(1))}),
    (re.compile(r"profit lock\s*:.*?Rs ([\d,]+(?:\.\d+)?)"), lambda m: {"DAILY_PROFIT_LOCK_RS": _rs(m.group(1))}),
    (re.compile(r"trades/day\s*:\s*fixed (\d+)"), lambda m: {"MAX_TRADES_DAY": int(m.group(1))}),
    (re.compile(r"drawdown halt -(\d+(?:\.\d+)?)% from peak \| deploy (\d+(?:\.\d+)?)%/trade \| "
                r"position cap (\d+(?:\.\d+)?)% \| max (\d+) lots"),
     lambda m: {"MAX_DRAWDOWN_STOP": float(m.group(1)) / 100, "DEPLOY_FRACTION": float(m.group(2)) / 100,
                "PER_TRADE_EQUITY_CAP": float(m.group(3)) / 100, "MAX_LOTS": int(m.group(4))}),
    (re.compile(r"entries (\d{2}:\d{2})\s*[–-]\s*(\d{2}:\d{2}), flat by (\d{2}:\d{2})"),
     lambda m: {"ENTRY_START": m.group(1), "ENTRY_CUTOFF": m.group(2), "FORCE_CLOSE": m.group(3)}),
    (re.compile(r"expiry\s*:\s*weekly contract (\d+)\s*-\s*(\d+) days"),
     lambda m: {"MIN_DTE": int(m.group(1)), "MAX_DTE": int(m.group(2))}),
]


def parse_startup_block(lines: list[str]) -> dict[str, Any]:
    """The parameters in a program's startup block (lines after its first header)."""
    out: dict[str, Any] = {}
    for raw in lines[:MAX_BLOCK_LINES]:
        line = _PREFIX.sub("", (raw or "").strip())
        for rx, fn in _LOG_RULES:
            m = rx.search(line)
            if m:
                out.update(fn(m))
        if line.startswith("entries "):
            break
    return {k: round(v, 6) if isinstance(v, float) else v for k, v in out.items()}


def _literal(node) -> Any:
    try:
        return ast.literal_eval(node)
    except (ValueError, SyntaxError, TypeError):
        return _MISSING


_MISSING = object()


def _norm(key: str, v: Any) -> Any:
    if key in _TIMES:
        if isinstance(v, (tuple, list)) and len(v) == 2:
            return f"{int(v[0]):02d}:{int(v[1]):02d}"
        return _MISSING
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return _MISSING
    return round(float(v), 6) if KINDS.get(key) in ("pct", "money", "float") else int(v)


def parse_source_constants(source: str) -> dict[str, Any]:
    """The program's module-level constants, read from its text. Never executed."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return {}
    out: dict[str, Any] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if isinstance(target, ast.Name) and target.id in KINDS:
            pairs = [(target.id, node.value)]
        elif isinstance(target, ast.Tuple) and isinstance(node.value, ast.Tuple) \
                and len(target.elts) == len(node.value.elts):
            pairs = [(t.id, v) for t, v in zip(target.elts, node.value.elts)
                     if isinstance(t, ast.Name) and t.id in KINDS]
        else:
            continue
        for key, value in pairs:
            v = _norm(key, _literal(value))
            if v is not _MISSING:
                out[key] = v
    return out


def _same(key: str, a: Any, b: Any) -> bool:
    if a is None or b is None:
        return a is b
    if isinstance(a, float) or isinstance(b, float):
        return abs(float(a) - float(b)) < 1e-6
    return a == b


def running_config(db, algo_id: str) -> dict | None:
    """What the program printed at its latest startup, or None if it never has."""
    hits = db.find_events(algo_id, BLOCK_START, limit=1)
    if not hits:
        return None
    header = hits[0]
    following = db.events_following(algo_id, header["id"], limit=MAX_BLOCK_LINES)
    values = parse_startup_block([e.get("message") or "" for e in following])
    if not values:
        return None
    return {"logged_ts": header.get("ts"), "event_id": header.get("id"), "values": values}


def code_config(db, algo: dict) -> dict | None:
    """The constants in the algorithm's active uploaded version."""
    from app.programs import cached_fact

    vid = algo.get("active_version")
    ver = db.version_meta(int(vid)) if vid else None
    if not ver:
        return None
    # Parsed once per distinct source (see programs.cached_fact): this is asked every minute.
    values = cached_fact(db, ver, "constants", parse_source_constants)
    if not values:
        return None
    return {"version": ver.get("version"), "sha256": (ver.get("sha256") or "")[:12], "values": values}


def config_report(db, algo: dict) -> dict:
    """Running vs code for one uploaded program, with every disagreement named."""
    running = running_config(db, algo["id"])
    code = code_config(db, algo)
    builtin = tuning.defaults()
    rows = []
    keys = [k for k in KINDS if (running and k in running["values"]) or (code and k in code["values"])]
    drift = []
    for k in keys:
        r = running["values"].get(k, _MISSING) if running else _MISSING
        c = code["values"].get(k, _MISSING) if code else _MISSING
        row = {
            "key": k, "label": LABELS.get(k, k), "kind": KINDS.get(k, "float"),
            "running": None if r is _MISSING else r, "running_known": r is not _MISSING,
            "code": None if c is _MISSING else c, "code_known": c is not _MISSING,
            "builtin": builtin.get(k), "builtin_known": k in builtin,
        }
        row["drift"] = row["running_known"] and row["code_known"] and not _same(k, r, c)
        effective = r if r is not _MISSING else c
        row["differs_from_builtin"] = row["builtin_known"] and effective is not _MISSING \
            and not _same(k, effective, builtin[k])
        if row["drift"]:
            drift.append(k)
        rows.append(row)
    return {
        "algo_id": algo["id"],
        "name": algo.get("name") or algo["id"],
        "mode": algo.get("mode"),
        "running": running and {k: v for k, v in running.items() if k != "values"},
        "code": code and {k: v for k, v in code.items() if k != "values"},
        "rows": rows,
        "drift": drift,
        "differs_from_builtin": [r["key"] for r in rows if r["differs_from_builtin"]],
    }


def all_reports(db) -> list[dict]:
    return [config_report(db, a) for a in db.algos() if a.get("kind") != "builtin"]
