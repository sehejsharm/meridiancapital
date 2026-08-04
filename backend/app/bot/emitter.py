"""Structured event channel from the bot subprocess to the supervisor.

The bot writes two kinds of lines to stdout:

    @@EVT@@{"kind": "entry", ...}   <- structured, machine readable
    anything else                   <- the human terminal feed

The supervisor reads stdout line by line, splits on that prefix, stores both
and pushes both to every connected phone. Nothing about the algorithm's own
printing changes, so the feed on the phone is the terminal feed verbatim.
"""
from __future__ import annotations

import json
import sys
import threading
from datetime import datetime
from typing import Any, Optional

EVENT_PREFIX = "@@EVT@@"

_seq_lock = threading.Lock()
_seq = 0


def _next_seq() -> int:
    global _seq
    with _seq_lock:
        _seq += 1
        return _seq


def emit(kind: str, message: str = "", level: str = "info", **payload: Any) -> None:
    """Write one structured event. Safe to call from anywhere in the bot."""
    record = {
        "kind": kind,
        "level": level,
        "message": message,
        "ts": datetime.now().isoformat(timespec="milliseconds"),
        "seq": _next_seq(),
        "payload": payload or None,
    }
    try:
        sys.stdout.write(EVENT_PREFIX + json.dumps(record, default=_fallback) + "\n")
        sys.stdout.flush()
    except (BrokenPipeError, ValueError):
        pass


def _fallback(obj: Any) -> Any:
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    return str(obj)


_IS_TTY: Optional[bool] = None


def is_tty() -> bool:
    global _IS_TTY
    if _IS_TTY is None:
        try:
            _IS_TTY = sys.stdout.isatty()
        except Exception:
            _IS_TTY = False
    return _IS_TTY


def tty_status(text: str) -> None:
    """Carriage-return status lines are for a real terminal only.

    Under the supervisor stdout is a pipe, and a line with no newline would
    stall the reader, so these are dropped there — the same information goes
    out as a structured `status` event instead.
    """
    if is_tty():
        sys.stdout.write("\r" + text)
        sys.stdout.flush()
