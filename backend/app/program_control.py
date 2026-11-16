"""The control file a program reads every loop (programs from v13).

Until v13 the desk could only reach a running program by stopping it (SIGINT),
which sells whatever it holds. So "halt new entries" was refused while a trade
was open, and there was no way to sell the trade and leave the program running.

A v13 program reads ``gk50k_control.json`` beside itself on every loop:

    {"entries": "halt" | "allow", "exit_now": {"id": "...", "ts": <epoch seconds>}}

* ``entries: halt`` — take no new trade; an open trade is still managed.
* ``exit_now`` — sell the open trade now through the program's own exit path
  (its checks against Angel included). Honoured once, and only for two minutes,
  so an old instruction can never sell a later trade.

The desk only writes the file. A program that does not read it (v12 and older)
is unaffected, and the desk says so instead of pretending.
"""

from __future__ import annotations

import json
import os
import secrets
import time
from pathlib import Path

from app import programs

FILE_NAME = "gk50k_control.json"


def supported(db, algo: dict) -> bool:
    """Whether the algorithm's active version reads the control file (decided from its source, once)."""
    vid = algo.get("active_version")
    ver = db.version_meta(int(vid)) if vid else None
    if not ver:
        return False
    return bool(programs.cached_fact(db, ver, "control_file", lambda src: FILE_NAME in src and "control_state(" in src))


def path_for(algo_id: str) -> Path:
    return programs.program_path(algo_id).parent / FILE_NAME


def read(algo_id: str) -> dict:
    try:
        data = json.loads(path_for(algo_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write(algo_id: str, data: dict) -> None:
    path = path_for(algo_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    os.replace(tmp, path)


def set_entries(algo_id: str, halted: bool) -> dict:
    data = read(algo_id)
    data["entries"] = "halt" if halted else "allow"
    data["entries_ts"] = time.time()
    _write(algo_id, data)
    return data


def exit_now(algo_id: str) -> dict:
    data = read(algo_id)
    data["exit_now"] = {"id": secrets.token_hex(6), "ts": time.time()}
    _write(algo_id, data)
    return data


def start_of_run(algo_id: str) -> None:
    """Before a program starts: yesterday's halt is dropped, today's survives a restart
    (a crash restart must not undo the operator's halt), and a sell-now never carries over."""
    from datetime import datetime, timedelta, timezone

    data = read(algo_id)
    if not data:
        return
    ist = timezone(timedelta(hours=5, minutes=30))
    try:
        same_day = datetime.fromtimestamp(float(data.get("entries_ts")), ist).date() == datetime.now(ist).date()
    except (TypeError, ValueError):
        same_day = False
    if data.get("entries") == "halt" and same_day:
        _write(algo_id, {"entries": "halt", "entries_ts": data["entries_ts"]})
    else:
        clear(algo_id)


def clear(algo_id: str) -> None:
    """A fresh start begins with no instruction: yesterday's halt must not carry over."""
    try:
        path_for(algo_id).unlink()
    except OSError:
        pass
