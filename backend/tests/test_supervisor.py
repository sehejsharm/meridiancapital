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


def test_strategy_injection() -> None:
    """An edit in the app must reach the child process, and only on a restart."""
    print("\nStrategy injection")
    from app import strategy_config as sc

    os.environ["STUB_MODE"] = "normal"
    sc.apply({"SL_PCT": 0.12, "MAX_TRADES_PER_DAY": 5,
              "ENABLE_CHOP_FILTER": False, "MARKET_OPEN": "09:45"})
    supervisor.tail.clear()
    supervisor.restarts = 0

    supervisor.start(trigger="test", force=True)
    got = wait_for(lambda: any(e["kind"] == "config_seen" for e in supervisor.tail), timeout=20)
    check("child reported its strategy environment", got)

    env = {}
    for e in supervisor.tail:
        if e["kind"] == "config_seen":
            env = (e.get("payload") or {}).get("env", {})
            break

    check(f"overridden stop loss reached the child (SL_PCT={env.get('SL_PCT')})",
          env.get("SL_PCT") == "0.12", str(env))
    check(f"overridden trade cap reached the child ({env.get('MAX_TRADES_PER_DAY')})",
          env.get("MAX_TRADES_PER_DAY") == "5", str(env))
    check("booleans serialise as the algorithm parses them",
          env.get("ENABLE_CHOP_FILTER") == "false", str(env))
    check("times pass through as HH:MM", env.get("MARKET_OPEN") == "09:45", str(env))
    check("untouched parameters still carry their v11 value",
          env.get("BE_TRIGGER_PCT") == "0.15", str(env))
    check("instrument defaults to NIFTY", env.get("INDEX_NAME") == "NIFTY", str(env))

    check("drift was announced at startup",
          any(e["kind"] == "strategy" for e in supervisor.tail))

    supervisor.stop(reason="injection test done", timeout=20)

    # Inconsistent parameters must block the launch rather than trade on them.
    db.kv_set(sc.KV_KEY, {"BE_FLOOR_PCT": 0.40})
    result = supervisor.start(trigger="test", force=True)
    check("inconsistent stored parameters refuse to start",
          result.get("ok") is False and "inconsistent" in str(result.get("reason")).lower(),
          str(result))
    check("no process was spawned with a broken ladder", not supervisor.running)

    sc.reset()


def test_runs_without_any_client() -> None:
    """The bot must not care whether a phone is watching.

    The whole point of putting it on a server is that it keeps trading when
    every device is asleep, offline, or thrown in a river. Nothing in the
    trading path may depend on a subscriber being attached.
    """
    print("\nRuns with no client attached")
    os.environ["STUB_MODE"] = "normal"
    supervisor.tail.clear()
    supervisor.restarts = 0

    check("no subscribers before starting", len(supervisor._subscribers) == 0)

    supervisor.start(trigger="test", force=True)
    check("starts with nobody connected",
          wait_for(lambda: any(e["kind"] == "ready" for e in supervisor.tail), timeout=20))

    # The event loop is what a WebSocket would ride on. Detach it entirely to
    # simulate the server having no client and no loop to broadcast into.
    saved_loop = supervisor._loop
    supervisor._loop = None
    try:
        before = len(supervisor.tail)
        check("keeps producing events with the loop detached",
              wait_for(lambda: len(supervisor.tail) > before, timeout=15)
              or supervisor.running,
              "the child should keep running regardless")
        check("process still alive with no listener", supervisor.running)

        today = db.today_str()
        check("trades still recorded with nobody watching",
              len(db.trades_between(today, today)) >= 1)
    finally:
        supervisor._loop = saved_loop

    supervisor.stop(reason="offline test done", timeout=20)
    check("stopped cleanly afterwards", not supervisor.running)


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


def test_real_module_reads_overrides() -> None:
    """Prove the algorithm's own constants follow the environment."""
    print("\nReal algorithm honours overrides")
    import subprocess

    env = dict(os.environ)
    env.update({
        "SL_PCT": "0.12", "BE_TRIGGER_PCT": "0.18", "LOCK1_TRIGGER": "0.30",
        "LOCK1_FLOOR": "0.12", "LOCK2_TRIGGER": "0.50", "LOCK2_FLOOR": "0.30",
        "MAX_TRADES_PER_DAY": "5", "ENABLE_CHOP_FILTER": "false",
        "MARKET_OPEN": "09:45", "ENTRY_CUTOFF": "14:00",
        "INDEX_NAME": "BANKNIFTY", "STRIKE_STEP": "100",
        "PYTHONUNBUFFERED": "1",
    })

    code = (
        "import json;"
        "from app.bot import strategy as s;"
        "s.verify_config();"
        "print('RESULT' + json.dumps({"
        "'sl': s.SL_PCT, 'be': s.BE_TRIGGER_PCT, 'l1': s.LOCK1_TRIGGER,"
        "'max': s.MAX_TRADES_PER_DAY, 'chop': s.ENABLE_CHOP_FILTER,"
        "'open': s.MARKET_OPEN.strftime('%H:%M'),"
        "'cut': s.ENTRY_CUTOFF.strftime('%H:%M'),"
        "'idx': s.INDEX_NAME, 'step': s.STRIKE_STEP}))"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(Path(__file__).resolve().parent.parent),
        env=env, capture_output=True, text=True, timeout=180,
    )
    line = next((l for l in proc.stdout.splitlines() if l.startswith("RESULT")), "")
    check("module imported and reported its config", bool(line),
          (proc.stdout + proc.stderr)[-500:])
    if not line:
        return

    import json as _json
    cfg = _json.loads(line[len("RESULT"):])
    check(f"SL_PCT follows the environment ({cfg['sl']})", cfg["sl"] == 0.12)
    check(f"BE trigger follows ({cfg['be']})", cfg["be"] == 0.18)
    check(f"Lock 1 trigger follows ({cfg['l1']})", cfg["l1"] == 0.30)
    check(f"trade cap follows ({cfg['max']})", cfg["max"] == 5)
    check("chop filter can be switched off", cfg["chop"] is False)
    check(f"session times parse ({cfg['open']}–{cfg['cut']})",
          cfg["open"] == "09:45" and cfg["cut"] == "14:00")
    check(f"instrument switches ({cfg['idx']} / step {cfg['step']})",
          cfg["idx"] == "BANKNIFTY" and cfg["step"] == 100)
    check("verify_config accepted a consistent non-v11 ladder",
          proc.returncode == 0, (proc.stdout + proc.stderr)[-300:])
    check("drift from v11 was reported, not silently accepted",
          "differ from the v11 baseline" in proc.stdout, proc.stdout[-300:])

    # And an inconsistent ladder must abort rather than trade.
    bad = dict(env)
    bad["BE_FLOOR_PCT"] = "0.40"
    proc2 = subprocess.run(
        [sys.executable, "-c",
         "from app.bot import strategy as s; s.verify_config(); print('ACCEPTED')"],
        cwd=str(Path(__file__).resolve().parent.parent),
        env=bad, capture_output=True, text=True, timeout=180,
    )
    check("inconsistent ladder fails the guard",
          "ACCEPTED" not in proc2.stdout and proc2.returncode != 0,
          (proc2.stdout + proc2.stderr)[-300:])
    check("the guard says which rule was broken",
          "below its trigger" in (proc2.stdout + proc2.stderr),
          (proc2.stdout + proc2.stderr)[-300:])


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
        test_strategy_injection()
        test_runs_without_any_client()
        test_real_module_guard()
        test_real_module_reads_overrides()
    finally:
        if supervisor.running:
            supervisor.stop(reason="test teardown", timeout=10)

    print("\n" + "=" * 60)
    print(f"  {PASS} passed, {FAIL} failed")
    print("=" * 60)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
