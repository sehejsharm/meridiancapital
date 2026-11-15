"""What the backup scripts last did, read for the Reliability page and the alert.

``deploy/backup.sh`` (nightly) and ``deploy/restore-test.sh`` (monthly) each
leave a small JSON file in the backup folder. Neither file holds the
off-machine address or the passphrase. A backup that stopped running, an
off-machine copy that failed, and a restore test that failed or has not run
are each a reason to tell the operator: they are the failures nobody notices
until the day the backup is needed.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

BACKUP_STALE_HOURS = 36       # nightly: one missed night
RESTORE_STALE_DAYS = 45       # monthly, with slack


def folder(db) -> Path:
    return Path(db.path).parent / "backups"


def _read(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _age_hours(ts: str | None, now: datetime) -> float | None:
    try:
        t = datetime.strptime(str(ts), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return (now - t).total_seconds() / 3600.0


def status(db, now: datetime | None = None) -> dict:
    """The last backup, the last restore test, and anything wrong with either."""
    now = now or datetime.now(timezone.utc)
    base = folder(db)
    backup = _read(base / "last-backup.json")
    restore = _read(base / "last-restore-test.json")
    problems: list[dict] = []
    if backup:
        age = _age_hours(backup.get("ts"), now)
        backup["age_hours"] = round(age, 1) if age is not None else None
        if age is None or age > BACKUP_STALE_HOURS:
            problems.append({"kind": "backup_stale", "level": "critical",
                             "text": f"the last backup is {age / 24:.1f} days old — the nightly backup has stopped"
                             if age is not None else "the last backup's time cannot be read"})
        if backup.get("off_machine") == "failed":
            problems.append({"kind": "off_machine_failed", "level": "critical",
                             "text": "the last backup could not be sent off the machine — it exists only on the VM"})
        elif backup.get("off_machine") != "ok":
            problems.append({"kind": "off_machine_missing", "level": "warning",
                             "text": "backups are kept on the VM only — set MERIDIAN_BACKUP_URL or "
                                     "MERIDIAN_BACKUP_REMOTE so a copy leaves the machine"})
    if restore:
        age = _age_hours(restore.get("ts"), now)
        restore["age_days"] = round(age / 24, 1) if age is not None else None
        if not restore.get("ok"):
            problems.append({"kind": "restore_failed", "level": "critical",
                             "text": f"the last restore test failed: {restore.get('detail') or 'no detail'}"})
        elif age is None or age > RESTORE_STALE_DAYS * 24:
            problems.append({"kind": "restore_stale", "level": "warning",
                             "text": f"the last restore test was {age / 24:.0f} days ago — it should run monthly"
                             if age is not None else "the last restore test's time cannot be read"})
    return {"backup": backup, "restore_test": restore, "problems": problems,
            "recorded": bool(backup or restore)}


def alerts(db, now: datetime | None = None) -> list[dict]:
    """The problems worth a message: everything but 'no off-machine target set', which is
    a standing condition shown on the page rather than news."""
    return [p for p in status(db, now)["problems"] if p["kind"] != "off_machine_missing"]
