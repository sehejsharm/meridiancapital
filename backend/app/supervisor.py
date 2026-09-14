"""Engine process lifecycle.

The engine runs as a detached child in its own session, so restarting or
redeploying the API never kills a process that is holding a live position. A new
API process re-adopts a running engine through the pidfile instead of starting a
second one, and refuses to start an engine while another is alive.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from app.config import settings
from engine.config import DATA_DIR
from shared.db import K_MODE, Database

DEFAULT_ALGO = "gk50k"


def pidfile_for(algo_id: str) -> Path:
    """The built-in keeps the original path so an engine that was already
    running when this upgrade landed is still adopted rather than orphaned."""
    if algo_id == DEFAULT_ALGO:
        return DATA_DIR / "engine.pid"
    return DATA_DIR / f"engine-{algo_id}.pid"


PIDFILE = pidfile_for(DEFAULT_ALGO)
STOP_GRACE_SEC = 25.0


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _is_engine(pid: int) -> bool:
    """Guard against a recycled PID belonging to some unrelated process."""
    try:
        cmdline = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode()
    except OSError:
        return False
    return "engine.runner" in cmdline


@dataclass
class SupervisorState:
    running: bool = False
    pid: int | None = None
    mode: str = "paper"
    started_ts: float | None = None
    run_id: int | None = None
    last_exit_code: int | None = None
    last_stop_reason: str = ""
    restarts_this_session: int = 0
    adopted: bool = False
    last_start_error: str = ""
    blocked_until: float = 0.0
    manual_override: bool = False  # operator stopped it; scheduler must not restart
    stdout_tail: list[str] = field(default_factory=list)


class Supervisor:
    def __init__(
        self,
        db: Database,
        algo_id: str = DEFAULT_ALGO,
        mode_provider=None,
        strategy_path: Path | None = None,
    ):
        self.algo_id = algo_id
        self.pidfile = pidfile_for(algo_id)
        # How this algorithm's paper/live mode is decided. The built-in reads
        # the operator setting in kv; uploaded algorithms carry their own.
        self._mode_provider = mode_provider
        self.strategy_path = strategy_path
        self._init(db)

    def _init(self, db: Database):
        self.db = db
        self.proc: subprocess.Popen | None = None
        self.state = SupervisorState(mode=self.desired_mode())
        self.adopt()

    # ── mode ─────────────────────────────────────────────────────────────────
    def desired_mode(self) -> str:
        if self._mode_provider is not None:
            return "live" if self._mode_provider() == "live" else "paper"
        return self._desired_mode_from_kv()

    def _desired_mode_from_kv(self) -> str:
        mode = self.db.kv_get(K_MODE, settings.default_mode)
        return "live" if mode == "live" else "paper"

    def set_mode(self, mode: str) -> None:
        if mode not in ("paper", "live"):
            raise ValueError("mode must be 'paper' or 'live'")
        self.db.kv_set(K_MODE, mode)
        self.state.mode = mode

    # ── discovery ────────────────────────────────────────────────────────────
    def adopt(self) -> None:
        if not self.pidfile.exists():
            return
        try:
            pid = int(self.pidfile.read_text().strip())
        except (ValueError, OSError):
            self.pidfile.unlink(missing_ok=True)
            return
        if _alive(pid) and _is_engine(pid):
            self.state.running = True
            self.state.pid = pid
            self.state.adopted = True
            self.state.started_ts = self.pidfile.stat().st_mtime
            self.db.add_event(
                "info", f"API adopted running engine pid {pid}", source="supervisor"
            )
        else:
            self.pidfile.unlink(missing_ok=True)

    def refresh(self) -> None:
        """Reap the child or notice an adopted engine has gone."""
        if not self.state.running:
            return
        if self.proc is not None:
            code = self.proc.poll()
            if code is None:
                return
            self._mark_stopped(code, "process exited")
            return
        if self.state.pid and not (_alive(self.state.pid) and _is_engine(self.state.pid)):
            self._mark_stopped(None, "adopted process disappeared")

    def _mark_stopped(self, code: int | None, reason: str) -> None:
        pid = self.state.pid
        if self.state.run_id:
            self.db.end_run(self.state.run_id, code, reason)
        self.db.expire_stale_commands()
        self.state.running = False
        self.state.pid = None
        self.state.last_exit_code = code
        self.state.last_stop_reason = reason
        self.state.run_id = None
        self.proc = None
        self.pidfile.unlink(missing_ok=True)
        level = "info" if code in (0, None) else "error"
        self.db.add_event(
            level, f"engine stopped (pid {pid}, exit {code}): {reason}", source="supervisor", algo_id=self.algo_id
        )

    # ── start / stop ─────────────────────────────────────────────────────────
    def start(self, trigger: str = "manual") -> dict:
        self.refresh()
        if self.state.running:
            return {"ok": False, "detail": f"engine already running (pid {self.state.pid})"}
        if time.time() < self.state.blocked_until:
            wait = int(self.state.blocked_until - time.time())
            return {"ok": False, "detail": f"restart backoff active, {wait}s remaining"}

        mode = self.desired_mode()
        env = os.environ.copy()
        env["MERIDIAN_TRADING_MODE"] = mode
        env["PYTHONUNBUFFERED"] = "1"
        python = settings.python_bin or sys.executable
        cmd = [python, "-m", "engine.runner", "--mode", mode, "--algo", self.algo_id]
        if self.strategy_path is not None:
            cmd += ["--strategy", str(self.strategy_path)]
        env["MERIDIAN_ALGO_ID"] = self.algo_id
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=str(settings.backend_dir),
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError as e:
            self.state.last_start_error = str(e)
            self.db.add_event("error", f"engine start failed: {e}", source="supervisor", algo_id=self.algo_id)
            return {"ok": False, "detail": f"spawn failed: {e}"}

        self.proc = proc
        self.state.running = True
        self.state.pid = proc.pid
        self.state.mode = mode
        self.state.adopted = False
        self.state.started_ts = time.time()
        self.state.last_start_error = ""
        self.state.manual_override = False
        self.state.run_id = self.db.start_run(proc.pid, mode, trigger, algo_id=self.algo_id)
        self.pidfile.write_text(str(proc.pid))
        self.db.add_event(
            "ok", f"engine started in {mode.upper()} mode (pid {proc.pid}, trigger {trigger})",
            source="supervisor", algo_id=self.algo_id,
        )
        return {"ok": True, "pid": proc.pid, "mode": mode}

    def stop(self, reason: str = "manual", force: bool = False, trigger: str = "manual") -> dict:
        self.refresh()
        if not self.state.running or not self.state.pid:
            return {"ok": True, "detail": "engine was not running"}

        pid = self.state.pid
        self.db.enqueue_command("stop", {"reason": reason, "force": force}, issued_by=trigger)
        deadline = time.time() + STOP_GRACE_SEC
        while time.time() < deadline:
            time.sleep(0.5)
            self.refresh()
            if not self.state.running:
                return {"ok": True, "detail": "engine stopped gracefully"}

        self.db.add_event(
            "warn", f"engine did not stop within {STOP_GRACE_SEC:.0f}s — sending SIGTERM",
            source="supervisor", algo_id=self.algo_id,
        )
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            self._mark_stopped(None, "already gone")
            return {"ok": True, "detail": "engine already gone"}

        deadline = time.time() + 15
        while time.time() < deadline:
            time.sleep(0.5)
            self.refresh()
            if not self.state.running:
                return {"ok": True, "detail": "engine stopped after SIGTERM"}

        if force:
            self.db.add_event("error", f"engine unresponsive — SIGKILL pid {pid}", source="supervisor", algo_id=self.algo_id)
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            time.sleep(1.0)
            self.refresh()
            return {"ok": True, "detail": "engine killed"}
        return {"ok": False, "detail": "engine did not stop; retry with force=true"}

    def restart(self, trigger: str = "manual") -> dict:
        self.stop(reason="restart", force=True, trigger=trigger)
        return self.start(trigger=trigger)

    def note_crash(self) -> None:
        self.state.restarts_this_session += 1
        self.state.blocked_until = time.time() + settings.restart_backoff_sec

    def snapshot(self) -> dict:
        self.refresh()
        s = self.state
        return {
            "running": s.running,
            "pid": s.pid,
            "mode": s.mode,
            "adopted": s.adopted,
            "uptime_sec": round(time.time() - s.started_ts, 1) if (s.running and s.started_ts) else None,
            "last_exit_code": s.last_exit_code,
            "last_stop_reason": s.last_stop_reason,
            "restarts_this_session": s.restarts_this_session,
            "restart_backoff_remaining": max(0, round(s.blocked_until - time.time())),
            "manual_override": s.manual_override,
            "last_start_error": s.last_start_error,
        }
