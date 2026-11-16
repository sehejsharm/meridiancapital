"""Exit settings as data (programs from v14).

A v14 program keeps its seven exit figures built in, and reads
``gk50k_exit_settings.json`` beside itself **once, when it starts**. The file may
replace some of them. The program checks the file itself and ignores all of it if
anything is wrong; the checks here are the same ones, so the desk never writes a
file the program would refuse.

This is the one place the desk can change how a trade is exited, so it is narrow:

* it only writes a file; it cannot touch a running program (the route refuses
  while the program runs, so what the desk shows is what is in force);
* every value must be inside the range the program accepts;
* a change needs a typed phrase, is written to the audit log and the journal;
* removing the file restores the built-in, backtested values.

The built-in values are the only ones with a backtest behind them. The desk says
so wherever a different value is shown.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from app import programs

FILE_NAME = "gk50k_exit_settings.json"
PHRASE = "CHANGE EXITS"
BOUNDS: dict[str, tuple[float, float]] = {
    "STOP_FRAC": (0.20, 0.60), "BE_TRIGGER": (0.10, 0.50), "TRAIL_FRAC": (0.10, 0.50),
    "LATE_TP_AFTER_MIN": (60, 300), "LATE_TP_FRAC": (0.20, 1.00),
    "RUNNER_AT": (0.50, 2.00), "RUNNER_FRAC": (0.05, 0.30),
}
LABELS = {
    "STOP_FRAC": "Starting stop, below the entry price",
    "BE_TRIGGER": "Gain at which the stop moves to the entry price",
    "TRAIL_FRAC": "Trail: distance behind the best price",
    "LATE_TP_AFTER_MIN": "Late take-profit: minutes after entry",
    "LATE_TP_FRAC": "Late take-profit: gain it sells at",
    "RUNNER_AT": "Runner lock: gain at which it starts",
    "RUNNER_FRAC": "Runner lock: distance behind the best price",
}


def supported(db, algo: dict) -> bool:
    """Whether the algorithm's active version reads the settings file (decided from its source, once)."""
    vid = algo.get("active_version")
    ver = db.version_meta(int(vid)) if vid else None
    if not ver:
        return False
    return bool(programs.cached_fact(db, ver, "exit_settings_file",
                                     lambda src: FILE_NAME in src and "parse_exit_settings(" in src))


def path_for(algo_id: str) -> Path:
    return programs.program_path(algo_id).parent / FILE_NAME


def check(values: dict, built_in: dict) -> tuple[dict | None, str | None]:
    """(the full set the program would run with, None) or (None, why). The program's own rules."""
    if not isinstance(values, dict) or not values:
        return None, "no setting was given"
    missing = [k for k in BOUNDS if k not in built_in]
    if missing:
        return None, f"the program's built-in {', '.join(missing)} could not be read"
    out = {k: built_in[k] for k in BOUNDS}
    for k, v in values.items():
        if k not in BOUNDS:
            return None, f"{k} is not an exit setting"
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v:
            return None, f"{k} is not a number"
        lo, hi = BOUNDS[k]
        if not lo <= v <= hi:
            return None, f"{k} = {v} is outside {lo} to {hi}"
        if k == "LATE_TP_AFTER_MIN":
            if v != int(v):
                return None, f"{k} must be whole minutes"
            out[k] = int(v)
        else:
            out[k] = float(v)
    if not out["RUNNER_FRAC"] < out["TRAIL_FRAC"] < out["RUNNER_AT"]:
        return None, "the runner lock must be tighter than the trail and start above it"
    if out["BE_TRIGGER"] > out["RUNNER_AT"]:
        return None, "break-even must come before the runner lock"
    return out, None


def read(algo_id: str) -> dict | None:
    """The file's contents, or None when there is no file (or it is not a settings object)."""
    try:
        data = json.loads(path_for(algo_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data else None


def write(algo_id: str, values: dict, built_in: dict) -> dict:
    """Write only the settings that differ from the built-in ones. Raises ValueError with the reason."""
    full, why = check(values, built_in)
    if full is None:
        raise ValueError(why)
    changed = {k: full[k] for k in BOUNDS if full[k] != built_in[k]}
    if not changed:
        clear(algo_id)
        return {}
    path = path_for(algo_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(changed, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)
    return changed


def clear(algo_id: str) -> bool:
    try:
        path_for(algo_id).unlink()
        return True
    except OSError:
        return False


def view(db, algo: dict, running: bool, built_in: dict | None) -> dict:
    is_supported = supported(db, algo)
    file = read(algo["id"]) if is_supported else None
    problem = None
    in_force = None
    if built_in and all(k in built_in for k in BOUNDS):
        in_force = {k: built_in[k] for k in BOUNDS}
        if file:
            full, problem = check(file, built_in)
            in_force = full or in_force
    return {
        "supported": is_supported, "running": running, "phrase": PHRASE,
        "settings": [{"key": k, "label": LABELS[k], "low": BOUNDS[k][0], "high": BOUNDS[k][1],
                      "built_in": (built_in or {}).get(k), "next_start": (in_force or {}).get(k),
                      "minutes": k == "LATE_TP_AFTER_MIN"} for k in BOUNDS],
        "changed": sorted(file) if file and not problem else [],
        "problem": problem,
    }
