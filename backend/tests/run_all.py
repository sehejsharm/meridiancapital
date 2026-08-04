"""Run every test suite.  From backend/:  python -m tests.run_all"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
SUITES = ["tests.test_smoke", "tests.test_supervisor"]


def main() -> int:
    failures = []
    for suite in SUITES:
        print(f"\n\033[1m▸ {suite}\033[0m")
        proc = subprocess.run([sys.executable, "-m", suite], cwd=str(BACKEND))
        if proc.returncode != 0:
            failures.append(suite)

    print("\n" + "=" * 60)
    if failures:
        print(f"  \033[31mFAILED:\033[0m {', '.join(failures)}")
    else:
        print("  \033[32mAll suites passed.\033[0m")
    print("=" * 60)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
