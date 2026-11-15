"""Paper first: how many full paper sessions each version has run before real money.

The rule on this desk is three full sessions on paper before a new version
trades real money. Nothing counted them: ``algo_versions.paper_sessions`` was
created with the table and never written. The count is worked out from the run
history instead, so it cannot drift from what actually happened:

* a run belongs to the version that was active when it started (recorded on the
  run from now on; for older runs, the newest version uploaded before the run);
* a day is a *full paper session* for a version when its paper runs covered at
  least ``FULL_SESSION_MIN`` of the 375 market minutes (09:15–15:30).

The desk warns before real money when the count is short. It does not refuse:
the decision stays the operator's, and the warning is written to the journal.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from app.reliability import _now, _ts, _window

REQUIRED = 3
FULL_SESSION_MIN = 300


def _version_of(run: dict, versions: list[dict]) -> int | None:
    """The version a run belongs to: recorded on it, else the newest uploaded before it."""
    if run.get("version_id"):
        return int(run["version_id"])
    started = str(run.get("started_ts") or "")
    best = None
    for v in versions:                       # oldest first
        if str(v.get("created_ts") or "") <= started:
            best = v["id"]
    return best


def _minutes(spans: list[tuple[datetime, datetime]]) -> float:
    merged: list[list[datetime]] = []
    for a, b in sorted(spans):
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return sum((b - a).total_seconds() for a, b in merged) / 60.0


def by_version(db, algo_id: str, now: datetime | None = None) -> dict[int, dict]:
    """For each version of an algorithm: its full paper sessions and its real-money days."""
    now = now or _now()
    with db.conn() as c:
        versions = [dict(r) for r in c.execute(
            "SELECT id, version, created_ts FROM algo_versions WHERE algo_id = ? ORDER BY created_ts, id",
            (algo_id,))]
        runs = [dict(r) for r in c.execute("SELECT * FROM engine_runs WHERE algo_id = ? ORDER BY id", (algo_id,))]
    spans: dict[tuple[int, str, str], list[tuple[datetime, datetime]]] = {}
    for r in runs:
        vid = _version_of(r, versions)
        start = _ts(r.get("started_ts"))
        if vid is None or start is None:
            continue
        stop = _ts(r.get("stopped_ts")) or now
        mode = "live" if r.get("mode") == "live" else "paper"
        day = start.date()
        while day <= stop.date():
            lo, hi = _window(day)
            a, b = max(start, lo), min(stop, hi, now)
            if b > a:
                spans.setdefault((vid, mode, day.isoformat()), []).append((a, b))
            day += timedelta(days=1)
    out = {v["id"]: {"paper_sessions": 0, "paper_days": [], "partial_paper_days": 0, "live_days": 0,
                     "first_live": None} for v in versions}
    for (vid, mode, day), ss in sorted(spans.items()):
        rec = out.setdefault(vid, {"paper_sessions": 0, "paper_days": [], "partial_paper_days": 0, "live_days": 0,
                                   "first_live": None})
        if mode == "live":
            rec["live_days"] += 1
            rec["first_live"] = rec["first_live"] or day
        elif _minutes(ss) >= FULL_SESSION_MIN:
            rec["paper_sessions"] += 1
            rec["paper_days"].append(day)
        else:
            rec["partial_paper_days"] += 1
    return out


def status(db, algo: dict, counts: dict[int, dict] | None = None) -> dict | None:
    """What the desk says about the active version before it trades real money."""
    vid = algo.get("active_version")
    if not vid:
        return None
    counts = counts if counts is not None else by_version(db, algo["id"])
    rec = counts.get(int(vid)) or {"paper_sessions": 0, "live_days": 0, "partial_paper_days": 0, "first_live": None}
    n = rec["paper_sessions"]
    return {
        "required": REQUIRED, "paper_sessions": n, "partial_paper_days": rec["partial_paper_days"],
        "live_days": rec["live_days"], "first_live": rec["first_live"],
        "ok": n >= REQUIRED,
        "warning": None if n >= REQUIRED else warning(n, rec["live_days"]),
    }


def warning(paper: int, live_days: int = 0) -> str:
    had = "no full paper session" if paper == 0 else f"{paper} full paper session{'s' if paper != 1 else ''}"
    tail = (f" It has already traded real money on {live_days} day{'s' if live_days != 1 else ''}."
            if live_days else "")
    return (f"This version has run {had}; the rule is {REQUIRED} before real money "
            f"(a full session is {FULL_SESSION_MIN} of the 375 market minutes on paper).{tail}")
