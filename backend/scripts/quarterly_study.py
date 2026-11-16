#!/usr/bin/env python3
"""Print the quarterly study (app/quarterly.py). Read-only.

    python backend/scripts/quarterly_study.py                    the last complete quarter
    python backend/scripts/quarterly_study.py 2026-07-01 2026-09-30
    python backend/scripts/quarterly_study.py --paper            paper trades instead of real money
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import quarterly  # noqa: E402
from app.config import settings  # noqa: E402
from engine.clock import now_ist  # noqa: E402
from shared.db import Database  # noqa: E402


def main(argv: list[str]) -> int:
    mode = "paper" if "--paper" in argv else "live"
    days = [a for a in argv if not a.startswith("--")]
    if len(days) not in (0, 2):
        print(__doc__)
        return 2
    start, end = days if days else quarterly.last_quarter(now_ist().date())
    print(quarterly.study_text(Database(settings.db_path), start, end, mode))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
