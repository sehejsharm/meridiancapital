"""A standalone program's own trade log, carried into the blotter and reports.

The built-in engine records each trade in the database as it closes. A program
writes its own CSV beside itself instead, so its trades — real ones, booked to
the paisa against Angel's trade book — never reached the blotter, the reports
or the equity-and-fills chart, which all read "0 trades" while it traded.

Every ``*trades*.csv`` in a program's folder is read; ``paper`` in the file name
marks paper trades. A trade already recorded (same algorithm, entry time,
side, strike and mode) is never added twice, so the file can be read as often
as it changes, whether or not the program is running.
"""

from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

SOURCE = "program trade log"


def _f(v) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if x == x else None


def _i(v) -> int | None:
    x = _f(v)
    return int(x) if x is not None else None


def tsym_for(strike: int | None, right: str, expiry: str) -> str | None:
    """Angel's symbol for a NIFTY option, e.g. NIFTY06OCT2622700CE."""
    if not strike or right not in ("CE", "PE"):
        return None
    try:
        d = date.fromisoformat(str(expiry)[:10])
    except ValueError:
        return f"NIFTY {strike} {right}"
    return f"NIFTY{d:%d}{d:%b}{d:%y}".upper() + f"{strike}{right}"


def read_trade_log(path: Path) -> list[dict]:
    """The trades in one CSV, in the dashboard's trade shape."""
    mode = "paper" if "paper" in path.name.lower() else "live"
    out = []
    try:
        with path.open(newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
    except (OSError, csv.Error, UnicodeDecodeError):
        return out
    for r in rows:
        entry = (r.get("entry_ts") or "").strip()
        if len(entry) < 10:
            continue
        right = (r.get("right") or r.get("side") or "").strip().upper()
        strike = _i(r.get("strike"))
        expiry = (r.get("expiry") or "").strip()
        out.append({
            "entry_ts": entry,
            "exit_ts": (r.get("exit_ts") or "").strip() or None,
            "session_date": entry[:10],
            "mode": mode,
            "view": (r.get("view") or "").strip() or None,
            "side": right or None,
            "strike": strike,
            "expiry": expiry or None,
            "tsym": (r.get("tsym") or "").strip() or tsym_for(strike, right, expiry),
            "lots": _i(r.get("lots")),
            "qty": _i(r.get("qty")),
            "entry_prem": _f(r.get("entry_prem")),
            "exit_prem": _f(r.get("exit_prem")),
            "spot_entry": _f(r.get("spot_entry")),
            "spot_exit": _f(r.get("spot_exit")),
            "peak_pct": _f(r.get("peak_pct")),
            "gross": _f(r.get("gross")),
            "charges": _f(r.get("charges")),
            "net": _f(r.get("net")),
            "reason": (r.get("reason") or "").strip() or None,
            "hold_min": _f(r.get("hold_min")),
            "equity": _f(r.get("equity")),
            "pnl_source": SOURCE,
        })
    return out


class TradeLogs:
    """Follows every program's trade logs into the trades table."""

    def __init__(self, db):
        self.db = db
        self._seen: dict[str, tuple[float, int]] = {}  # file -> (mtime, size) last read

    def ingest(self, algo_id: str, folder: Path) -> int:
        """Record any trades in the folder's logs not yet recorded. Returns how many."""
        added = 0
        try:
            files = sorted(p for p in folder.glob("*trades*.csv") if p.is_file())
        except OSError:
            return 0
        for path in files:
            try:
                st = path.stat()
            except OSError:
                continue
            stamp = (st.st_mtime, st.st_size)
            if self._seen.get(str(path)) == stamp:
                continue
            for trade in read_trade_log(path):
                if self.db.has_trade(algo_id, trade["entry_ts"], trade["side"], trade["strike"], trade["mode"]):
                    continue
                self.db.add_trade({**trade, "algo_id": algo_id})
                added += 1
            self._seen[str(path)] = stamp
        return added
