"""A day's event log, as a file you can keep.

The database is the live record, but it prunes and it lives on one VM. A daily
export is what you actually reach for after a bad session: something you can
open offline, diff against the broker's statement, or send to someone.

Two formats because they get used differently. CSV opens in a spreadsheet and
sorts. The text form is one line per event, which greps and tails like any
other log and pastes into a message without turning into a table.
"""

from __future__ import annotations

import csv
import io
import json

from engine.clock import now_ist

MAX_EVENTS = 100_000


def day_events(db, day: str, algo_id: str | None = None) -> list[dict]:
    """Every event stamped with this IST date, oldest first.

    Read oldest-first because a log is read forwards — you want to see what
    happened, in order, not the newest thing at the top.
    """
    rows = db.events(limit=MAX_EVENTS, algo_id=algo_id)
    same_day = [e for e in rows if (e.get("ts") or "").startswith(day)]
    return sorted(same_day, key=lambda e: e.get("id") or 0)


def summarise(events: list[dict]) -> dict:
    counts: dict[str, int] = {}
    for e in events:
        counts[e.get("level") or "info"] = counts.get(e.get("level") or "info", 0) + 1
    return {
        "events": len(events),
        "by_level": counts,
        "errors": counts.get("error", 0) + counts.get("critical", 0),
        "first_ts": events[0].get("ts") if events else None,
        "last_ts": events[-1].get("ts") if events else None,
    }


def to_csv(day: str, events: list[dict], algo_id: str | None) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    summary = summarise(events)

    w.writerow([f"Meridian Capital — event log for {day}"])
    w.writerow(["Algorithm", algo_id or "all"])
    w.writerow(["Exported", now_ist().isoformat(timespec="seconds")])
    w.writerow(["Events", summary["events"], "Errors", summary["errors"]])
    w.writerow([])
    w.writerow(["Timestamp", "Level", "Source", "Algorithm", "Message", "Detail"])
    for e in events:
        extra = e.get("extra")
        w.writerow([
            e.get("ts"),
            e.get("level"),
            e.get("source"),
            e.get("algo_id"),
            e.get("message"),
            json.dumps(extra, default=str) if extra else "",
        ])
    return buf.getvalue()


def to_text(day: str, events: list[dict], algo_id: str | None) -> str:
    summary = summarise(events)
    lines = [
        f"# Meridian Capital — event log for {day}",
        f"# algorithm: {algo_id or 'all'}",
        f"# exported:  {now_ist().isoformat(timespec='seconds')} IST",
        f"# events:    {summary['events']}  errors: {summary['errors']}",
        "#" + "-" * 78,
    ]
    for e in events:
        ts = (e.get("ts") or "")[11:19] or "--:--:--"
        level = (e.get("level") or "info").upper()
        source = e.get("source") or "-"
        algo = e.get("algo_id") or "-"
        lines.append(f"{ts}  {level:<8} {source:<10} {algo:<16} {e.get('message', '')}")
        extra = e.get("extra")
        if extra:
            lines.append(f"{'':>10}{'':<8} {json.dumps(extra, default=str)[:600]}")
    if not events:
        lines.append("(no events recorded on this date)")
    return "\n".join(lines) + "\n"


def filename(day: str, algo_id: str | None, ext: str) -> str:
    scope = algo_id or "all"
    return f"meridian-log-{scope}-{day}.{ext}"
