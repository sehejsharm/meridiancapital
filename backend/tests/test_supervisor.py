"""Supervisor integration tests — run with:  python -m tests.test_supervisor

Spawns a real child process and drives it through the paths that matter:
output parsing, event persistence, trade and session extraction, graceful
SIGTERM shutdown, startup failure, and the credential guard on the actual
algorithm module.
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

TMP = tempfile.mkdtemp(prefix="meridian-sup-")
os.environ.update({
    "DATA_DIR": TMP,
    "API_TOKEN": "test-token-123",
    "PAPER_MODE": "true",
    "TZ": "Asia/Kolkata",
    "AUTO_SCHEDULE": "false",
    "BOT_MODULE": "tests.stub_bot",
    "ANGEL_API_KEY": "x", "ANGEL_CLIENT_ID": "x",
    "ANGEL_PASSWORD": "x", "ANGEL_TOTP_SECRET": "x",
})

from app import db  # noqa: E402
from app.config import settings  # noqa: E402
from app.runner import supervisor  # noqa: E402

PASS, FAIL = 0, 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  \033[32mPASS\033[0m  {label}")
    else:
        FAIL += 1
        print(f"  \033[31mFAIL\033[0m  {label}  {detail}")


def wait_for(predicate, timeout: float = 25.0, interval: float = 0.2) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def kinds() -> set[str]:
    return {e["kind"] for e in supervisor.tail}


def test_normal_session() -> None:
    print("\nNormal session")
    os.environ["STUB_MODE"] = "normal"

    result = supervisor.start(trigger="test", force=True)
    check("start() reports success", result.get("ok") is True, str(result))
    check("process is running", supervisor.running)
    check("state is running", supervisor.state == "running", supervisor.state)

    check("boot event received", wait_for(lambda: "boot" in kinds()))
    check("entry event received", wait_for(lambda: "entry" in kinds()))
    check("exit event received", wait_for(lambda: "exit" in kinds()))
    check("eod event received", wait_for(lambda: "eod" in kinds()))

    # Plain stdout lines become log events, with ANSI stripped.
    logs = [e for e in supervisor.tail if e["kind"] == "log"]
    check(f"terminal lines captured ({len(logs)})", len(logs) >= 3)
    check("ANSI escapes stripped from logs",
          all("\x1b[" not in e["message"] for e in logs))
    check("banner line preserved verbatim",
          any("HAR HAR MAHADEV" in e["message"] for e in logs))

    # The high-frequency status event drives the UI but must not be stored.
    check("status snapshot captured live", bool(supervisor.snapshot.get("equity")))
    stored = db.query("SELECT COUNT(*) AS n FROM events WHERE kind = 'status'")
    check("status events are not persisted", stored[0]["n"] == 0, str(stored))

    stored_kinds = {r["kind"] for r in db.query("SELECT DISTINCT kind FROM events")}
    check("events persisted to sqlite",
          {"boot", "entry", "exit", "eod", "log"}.issubset(stored_kinds),
          str(sorted(stored_kinds)))

    # The exit event must have become a trade row.
    today = db.today_str()
    trades = db.trades_between(today, today)
    check(f"exit produced a trade row ({len(trades)})", len(trades) == 1)
    if trades:
        t = trades[0]
        check(f"trade net P&L stored ({t['net_pnl']})", abs(t["net_pnl"] - 2609.08) < 0.01)
        check("trade symbol stored", t["symbol"] == "NIFTY07AUG2624500CE")
        check("charge breakdown stored", abs((t["charges"] or 0) - 68.42) < 0.01)
        check("entry reason carried through", "trend stack" in (t["entry_reason"] or ""))

    # The eod event must have become a session row.
    sess = db.query_one("SELECT * FROM sessions WHERE session_date = ?", (today,))
    check("eod produced a session row", sess is not None)
    if sess:
        check(f"session day P&L stored ({sess['day_pnl']})",
              abs(sess["day_pnl"] - 2609.08) < 0.01)
        check("session win rate stored", sess["win_rate"] == 100.0)

    marks = db.equity_marks(today)
    check(f"minute event wrote an equity mark ({len(marks)})", len(marks) >= 1)

    run = db.query_one("SELECT * FROM runs ORDER BY id DESC LIMIT 1")
    check("run recorded with a pid", run is not None and run["pid"] is not None)


def test_graceful_stop() -> None:
    print("\nGraceful stop")
    result = supervisor.stop(reason="test stop", timeout=20)
    check("stop() reports success", result.get("ok") is True, str(result))
    check("process is gone", not supervisor.running)
    check("state is stopped", supervisor.state == "stopped", supervisor.state)

    check("child handled SIGTERM rather than being killed",
          any(e["kind"] == "stopping" for e in supervisor.tail))
    check("shutdown event received",
          any(e["kind"] == "shutdown" for e in supervisor.tail))

    run = db.query_one("SELECT * FROM runs ORDER BY id DESC LIMIT 1")
    check("run closed out", run is not None and run["stopped_at"] is not None)
    check("stop reason recorded", run is not None and run["stop_reason"] == "test stop",
          str(run.get("stop_reason") if run else None))

    check("stopping again is a no-op", supervisor.stop().get("ok") is False)


def test_startup_failure() -> None:
    print("\nStartup failure")
    os.environ["STUB_MODE"] = "fatal"
    supervisor.tail.clear()
    supervisor.restarts = 0

    supervisor.start(trigger="test", force=True)
    check("exit code 2 leaves the supervisor in error state",
          wait_for(lambda: supervisor.state == "error", timeout=20),
          supervisor.state)
    check("fatal event surfaced", any(e["kind"] == "fatal" for e in supervisor.tail))
    check("no restart loop after a startup failure", supervisor.restarts == 0)
    check("last_error is set for the app to show", bool(supervisor.last_error))


def test_missing_credentials() -> None:
    print("\nCredential guard")
    saved = settings.angel_api_key
    settings.angel_api_key = ""
    try:
        result = supervisor.start(trigger="test", force=True)
        check("start refused without credentials", result.get("ok") is False)
        check("reason names the missing variable",
              "ANGEL_API_KEY" in str(result.get("reason")), str(result))
        check("no process was spawned", not supervisor.running)
    finally:
        settings.angel_api_key = saved


def test_real_module_guard() -> None:
    """The actual algorithm must refuse to start with no credentials."""
    print("\nReal algorithm module")
    import subprocess

    env = {k: v for k, v in os.environ.items()
           if not k.startswith("ANGEL_")}
    env["DATA_DIR"] = TMP
    env["PYTHONUNBUFFERED"] = "1"

    proc = subprocess.run(
        [sys.executable, "-u", "-m", "app.bot.strategy"],
        cwd=str(Path(__file__).resolve().parent.parent),
        env=env, capture_output=True, text=True, timeout=120,
    )
    out = proc.stdout + proc.stderr
    check(f"exits 2 on missing credentials (got {proc.returncode})", proc.returncode == 2,
          out[-400:])
    check("names the missing variables", "ANGEL_API_KEY" in out, out[-400:])
    check("emits a fatal event the supervisor can read", "@@EVT@@" in out)
    check("fails cleanly rather than dumping a traceback",
          "Traceback (most recent call last)" not in out, out[-400:])


def main() -> int:
    print("=" * 60)
    print("  MERIDIAN CAPITAL — SUPERVISOR TESTS")
    print("=" * 60)
    db.init(settings.db_path)

    try:
        test_normal_session()
        test_graceful_stop()
        test_startup_failure()
        test_missing_credentials()
        test_real_module_guard()
    finally:
        if supervisor.running:
            supervisor.stop(reason="test teardown", timeout=10)

    print("\n" + "=" * 60)
    print(f"  {PASS} passed, {FAIL} failed")
    print("=" * 60)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
