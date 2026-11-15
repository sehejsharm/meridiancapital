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

from app.build import build_info

# Thresholds from QUICK_CHECKLIST: alert above warn, stop trading above critical.
MEM_WARN_MB = 1024
MEM_CRITICAL_MB = 2048
DISK_WARN_MB = 1024
DISK_CRITICAL_MB = 256

# System memory thresholds, as a share of total RAM. The engine holds pandas
# frames and an option chain; an OOM kill with a position open is the scenario
# these exist for.
SYS_MEM_WARN = 0.80
SYS_MEM_CRITICAL = 0.92
SWAP_WARN = 0.25
# Free disk as a share of the volume. The megabyte thresholds above catch a
# nearly full disk; this one warns while there is still time to do something.
DISK_FREE_WARN_PCT = 20.0
DISK_FREE_CRITICAL_PCT = 8.0
# Five-minute load per processor core. Above 1 the machine has more to do than
# it has processors; the trading loop is then waiting its turn.
LOAD_WARN = 1.0
LOAD_CRITICAL = 2.0

_STARTED = time.time()


def _meminfo() -> dict[str, float]:
    """Machine-wide memory in MB, from /proc. Empty if unreadable."""
    try:
        values: dict[str, float] = {}
        with open("/proc/meminfo", encoding="utf-8") as f:
            for line in f:
                key, _, rest = line.partition(":")
                parts = rest.split()
                if parts:
                    values[key] = float(parts[0]) / 1024.0  # kB -> MB
        total = values.get("MemTotal", 0.0)
        available = values.get("MemAvailable", values.get("MemFree", 0.0))
        swap_total = values.get("SwapTotal", 0.0)
        swap_free = values.get("SwapFree", 0.0)
        if total <= 0:
            return {}
        return {
            "total_mb": total,
            "available_mb": available,
            "used_mb": total - available,
            "used_fraction": (total - available) / total,
            "swap_total_mb": swap_total,
            "swap_used_mb": swap_total - swap_free,
            "swap_used_fraction": (swap_total - swap_free) / swap_total if swap_total else 0.0,
        }
    except (OSError, ValueError):
        return {}


def _position_open(db) -> bool:
    """Is any engine currently holding a position?

    This changes what a memory warning means. Spare RAM with everything flat is
    housekeeping; the same reading with capital in the market is the failure the
    deployment plan calls out, because an OOM kill there leaves a live position
    with nothing managing its stop.
    """
    try:
        from shared.db import snapshot_key

        for algo in db.algos():
            snap = db.kv_get(snapshot_key(algo["id"]), None)
            if snap and snap.get("position"):
                return True
    except Exception:
        pass
    return False


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


_ORDER = {"ok": 0, "unknown": 1, "warning": 2, "critical": 3}


def _worst(*states: str) -> str:
    return max(states, key=lambda s: _ORDER.get(s, 0))


def _volume(db):
    """A path on the volume the database lives on (the file itself may not exist yet)."""
    path = getattr(db, "path", None)
    if path is None:
        return "/"
    return path if os.path.exists(str(path)) else os.path.dirname(str(path)) or "/"


def _dir_mb(path) -> float | None:
    """Megabytes under a folder, or None if it is not there."""
    try:
        total = 0
        for root, _dirs, files in os.walk(str(path)):
            for name in files:
                try:
                    total += os.path.getsize(os.path.join(root, name))
                except OSError:
                    pass
        return round(total / (1024 * 1024), 1) if os.path.isdir(str(path)) else None
    except OSError:
        return None


def host(db) -> dict:
    """The machine for the Reliability page: processor, memory, disk, and what is using the disk.

    The same thresholds as the deck's health strip, so the two never disagree.
    """
    cores = os.cpu_count() or 1
    load = _load()
    mem = _meminfo()
    disk = None
    try:
        u = shutil.disk_usage(str(_volume(db)))
        disk = {"total_gb": round(u.total / 1024 ** 3, 1), "free_gb": round(u.free / 1024 ** 3, 1),
                "free_pct": round(100.0 * u.free / u.total, 1) if u.total else None}
    except OSError:
        pass
    data_dir = os.path.dirname(str(getattr(db, "path", "")))
    db_mb = None
    try:
        db_mb = round(os.path.getsize(str(db.path)) / (1024 * 1024), 1)
    except (OSError, AttributeError):
        pass
    warnings = []
    if disk and disk["free_pct"] is not None and disk["free_pct"] < DISK_FREE_WARN_PCT:
        level = "critical" if disk["free_pct"] < DISK_FREE_CRITICAL_PCT else "warning"
        warnings.append({"kind": "disk", "level": level,
                         "text": f"{disk['free_pct']:.0f}% of the disk is free ({disk['free_gb']} GB) — "
                                 f"below {DISK_FREE_WARN_PCT:.0f}%"})
    if mem and mem["used_fraction"] >= SYS_MEM_WARN:
        level = "critical" if mem["used_fraction"] >= SYS_MEM_CRITICAL else "warning"
        warnings.append({"kind": "memory", "level": level,
                         "text": f"{mem['used_fraction'] * 100:.0f}% of memory in use "
                                 f"({mem['used_mb']:,.0f} of {mem['total_mb']:,.0f} MB)"})
    if load and load[1] / cores >= LOAD_WARN:
        level = "critical" if load[1] / cores >= LOAD_CRITICAL else "warning"
        warnings.append({"kind": "cpu", "level": level,
                         "text": f"5-minute load {load[1]:.2f} on {cores} core{'s' if cores != 1 else ''}"})
    return {
        "cpu": {"cores": cores,
                "load_1m": round(load[0], 2) if load else None,
                "load_5m": round(load[1], 2) if load else None,
                "load_15m": round(load[2], 2) if load else None,
                "load_pct": round(100 * load[1] / cores, 0) if load else None},
        "memory": {"total_mb": round(mem["total_mb"], 0), "used_mb": round(mem["used_mb"], 0),
                   "used_pct": round(mem["used_fraction"] * 100, 1)} if mem else None,
        "disk": disk,
        "storage": {"database_mb": db_mb,
                    "backups_mb": _dir_mb(os.path.join(data_dir, "backups")) if data_dir else None,
                    "archive_mb": _dir_mb(os.path.join(data_dir, "archive")) if data_dir else None,
                    "research_mb": _dir_mb(os.path.join(data_dir, "research")) if data_dir else None},
        "api_memory_mb": round(_rss_mb() or 0.0, 1) or None,
        "uptime_hours": _machine_uptime_hours(),
        "api_uptime_hours": round((time.time() - _STARTED) / 3600, 1),
        "warnings": warnings,
        "thresholds": {"disk_free_warn_pct": DISK_FREE_WARN_PCT, "memory_warn_pct": SYS_MEM_WARN * 100,
                       "load_warn_per_core": LOAD_WARN},
    }


def _machine_uptime_hours() -> float | None:
    try:
        with open("/proc/uptime", encoding="utf-8") as f:
            return round(float(f.read().split()[0]) / 3600, 1)
    except (OSError, ValueError, IndexError):
        return None


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

    free_mb, free_pct = None, None
    try:
        usage = shutil.disk_usage(str(_volume(db)))
        free_mb = usage.free / (1024 * 1024)
        free_pct = 100.0 * usage.free / usage.total if usage.total else None
    except OSError:
        pass
    checks.append({
        "key": "disk",
        "label": "Free disk",
        "value": round(free_mb, 0) if free_mb is not None else None,
        "unit": "MB",
        "state": _worst(_grade(free_mb, DISK_WARN_MB, DISK_CRITICAL_MB, higher_is_worse=False),
                        _grade(free_pct, DISK_FREE_WARN_PCT, DISK_FREE_CRITICAL_PCT, higher_is_worse=False)
                        if free_pct is not None else "ok"),
        "detail": (f"{free_pct:.0f}% of the volume free — " if free_pct is not None else "")
        + f"warn below {DISK_FREE_WARN_PCT:.0f}% or {DISK_WARN_MB} MB, critical below {DISK_CRITICAL_MB} MB",
    })

    cores = os.cpu_count() or 1
    load = _load()
    per_core = (load[1] / cores) if load else None
    checks.append({
        "key": "cpu",
        "label": "Processor load",
        "value": round(per_core * 100, 0) if per_core is not None else None,
        "unit": "% of cores",
        "state": _grade(per_core, LOAD_WARN, LOAD_CRITICAL, higher_is_worse=True),
        "detail": (f"5-minute load {load[1]:.2f} on {cores} core{'s' if cores != 1 else ''}"
                   if load else "load average unavailable"),
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

    mem = _meminfo()
    exposed = _position_open(db)
    if mem:
        used = mem["used_fraction"]
        state = _grade(used, SYS_MEM_WARN, SYS_MEM_CRITICAL, higher_is_worse=True)
        # An OOM kill with capital in the market is the scenario worth shouting
        # about, so exposure promotes a warning to critical.
        if exposed and state == "warning":
            state = "critical"
        checks.append({
            "key": "system_memory",
            "label": "Machine memory",
            "value": round(used * 100, 1),
            "unit": "% used",
            "state": state,
            "detail": (
                f"{mem['used_mb']:,.0f} of {mem['total_mb']:,.0f} MB"
                + (" — A POSITION IS OPEN" if exposed else "")
            ),
        })
        if mem["swap_total_mb"] > 0:
            checks.append({
                "key": "swap",
                "label": "Swap in use",
                "value": round(mem["swap_used_fraction"] * 100, 1),
                "unit": "%",
                "state": _grade(mem["swap_used_fraction"], SWAP_WARN, 0.75, higher_is_worse=True),
                "detail": (
                    f"{mem['swap_used_mb']:,.0f} of {mem['swap_total_mb']:,.0f} MB — "
                    f"swapping means the loop is already running slow"
                ),
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
        "position_open": exposed,
        "checks": checks,
        "build": build_info(),
    }
