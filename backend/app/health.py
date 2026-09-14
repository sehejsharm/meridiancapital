"""What the deck shows about the machine underneath the algorithms.

The deployment plan asks for process liveness, memory, disk and database
reachability, with named thresholds. Those thresholds live here rather than in
the dashboard so that the alert and the display can never disagree about what
counts as unhealthy.
"""

from __future__ import annotations

import os
import shutil
import time

# Thresholds from QUICK_CHECKLIST: alert above warn, stop trading above critical.
MEM_WARN_MB = 1024
MEM_CRITICAL_MB = 2048
DISK_WARN_MB = 1024
DISK_CRITICAL_MB = 256

_STARTED = time.time()


def _rss_mb() -> float | None:
    """Resident set size without taking a psutil dependency."""
    try:
        with open("/proc/self/statm", encoding="utf-8") as f:
            pages = int(f.read().split()[1])
        return pages * os.sysconf("SC_PAGE_SIZE") / (1024 * 1024)
    except (OSError, IndexError, ValueError):
        return None


def _load() -> list[float] | None:
    try:
        return [round(x, 2) for x in os.getloadavg()]
    except OSError:
        return None


def _grade(value: float | None, warn: float, critical: float, higher_is_worse: bool) -> str:
    if value is None:
        return "unknown"
    if higher_is_worse:
        if value >= critical:
            return "critical"
        return "warning" if value >= warn else "ok"
    if value <= critical:
        return "critical"
    return "warning" if value <= warn else "ok"


def collect(db, fleet) -> dict:
    """One health payload for the deck. Never raises; degrades to unknown."""
    checks: list[dict] = []

    mem = _rss_mb()
    checks.append({
        "key": "memory",
        "label": "API memory",
        "value": round(mem, 1) if mem is not None else None,
        "unit": "MB",
        "state": _grade(mem, MEM_WARN_MB, MEM_CRITICAL_MB, higher_is_worse=True),
        "detail": f"alert above {MEM_WARN_MB} MB, stop above {MEM_CRITICAL_MB} MB",
    })

    free_mb = None
    try:
        free_mb = shutil.disk_usage(str(getattr(db, "path", "/"))).free / (1024 * 1024)
    except OSError:
        pass
    checks.append({
        "key": "disk",
        "label": "Free disk",
        "value": round(free_mb, 0) if free_mb is not None else None,
        "unit": "MB",
        "state": _grade(free_mb, DISK_WARN_MB, DISK_CRITICAL_MB, higher_is_worse=False),
        "detail": f"warn below {DISK_WARN_MB} MB, critical below {DISK_CRITICAL_MB} MB",
    })

    db_ok, db_detail, trade_count = True, "reachable", None
    started = time.perf_counter()
    try:
        with db.conn() as c:
            trade_count = int(c.execute("SELECT COUNT(*) FROM trades").fetchone()[0])
    except Exception as exc:
        db_ok, db_detail = False, f"{type(exc).__name__}: {exc}"
    latency_ms = round((time.perf_counter() - started) * 1000, 1)
    checks.append({
        "key": "database",
        "label": "Database",
        "value": latency_ms,
        "unit": "ms",
        "state": "ok" if db_ok else "critical",
        "detail": f"{db_detail}" + (f", {trade_count:,} trades" if trade_count is not None else ""),
    })

    overview = fleet.overview()
    checks.append({
        "key": "engines",
        "label": "Engines running",
        "value": overview["running"],
        "unit": f"of {overview['total']}",
        "state": "ok",
        "detail": (
            f"{overview['live_running']} trading real money"
            if overview["live_running"]
            else "none on real money"
        ),
    })

    worst = "ok"
    for c in checks:
        if c["state"] == "critical":
            worst = "critical"
            break
        if c["state"] in ("warning", "unknown") and worst == "ok":
            worst = c["state"]

    return {
        "state": worst,
        "uptime_seconds": round(time.time() - _STARTED, 1),
        "load_average": _load(),
        "checks": checks,
    }
