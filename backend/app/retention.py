"""Journal retention: lines older than a year are archived to a file, then removed.

The journal (the ``events`` table) grows by a few thousand lines every trading
day and, before this module, nothing ever removed any. Reports, tax figures and tear sheets read the trades table, which
is never touched here; only the journal's old lines move.

Nothing is deleted until it is safely on disk: each batch is appended to
``<data dir>/archive/journal-YYYY-MM.jsonl.gz`` (one JSON object per line,
grouped by the month the line was written), flushed and synced, and only then
removed from the database. A failure at any point leaves the journal as it was.
"""

from __future__ import annotations

import gzip
import json
import os
from datetime import date, timedelta
from pathlib import Path

DEFAULT_KEEP_DAYS = 365
MIN_KEEP_DAYS = 90        # a typo in the setting must not empty the journal
BATCH = 5000
MAX_BATCHES = 40          # 200,000 lines a night at most; the rest waits a day


def keep_days() -> int:
    try:
        n = int(os.environ.get("MERIDIAN_JOURNAL_KEEP_DAYS", DEFAULT_KEEP_DAYS))
    except ValueError:
        n = DEFAULT_KEEP_DAYS
    return max(n, MIN_KEEP_DAYS)


def archive_dir(db) -> Path:
    return Path(db.path).parent / "archive"


def _append(path: Path, rows: list[dict]) -> None:
    """Append rows to a gzip file (a new gzip member each time; readers see one stream)."""
    with open(path, "ab") as raw:
        with gzip.GzipFile(fileobj=raw, mode="ab") as gz:
            for r in rows:
                gz.write((json.dumps(r, ensure_ascii=False, default=str) + "\n").encode("utf-8"))
        raw.flush()
        os.fsync(raw.fileno())


def run(db, today: date, days: int | None = None, folder: Path | None = None) -> dict:
    """Archive and remove journal lines older than ``days``. Returns what was done."""
    days = max(days if days is not None else keep_days(), MIN_KEEP_DAYS)
    cutoff = (today - timedelta(days=days)).isoformat()
    folder = folder or archive_dir(db)
    archived, files = 0, set()
    for _ in range(MAX_BATCHES):
        rows = db.events_before(cutoff, limit=BATCH)
        if not rows:
            break
        folder.mkdir(parents=True, exist_ok=True)
        by_month: dict[str, list[dict]] = {}
        for r in rows:
            by_month.setdefault(str(r.get("ts") or "")[:7] or "undated", []).append(r)
        for month, chunk in by_month.items():
            path = folder / f"journal-{month}.jsonl.gz"
            _append(path, chunk)
            files.add(path.name)
        removed = db.delete_events([int(r["id"]) for r in rows])
        archived += removed
        if len(rows) < BATCH:
            break
    return {"cutoff": cutoff, "keep_days": days, "archived": archived, "files": sorted(files)}


def read_archive(path: Path) -> list[dict]:
    """Every line of one archive file (for a restore, or a look at an old session)."""
    out = []
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out
