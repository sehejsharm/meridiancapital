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


def outfile_for(algo_id: str) -> Path:
    """Where this algorithm's engine writes stdout and stderr."""
    return DATA_DIR / f"engine-{algo_id}.out"


PIDFILE = pidfile_for(DEFAULT_ALGO)
STOP_GRACE_SEC = 25.0
# A program squares off on Ctrl-C before it exits: cancel its resting exit
# orders, confirm, sell, book. On a slow broker day that takes longer than an
# engine's shutdown, and cutting it short with SIGTERM would leave the booking
# half done.
PROGRAM_STOP_GRACE_SEC = 75.0
OUTPUT_TAIL_LINES = 20
OUTPUT_TAIL_BYTES = 8192


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _tail(path: Path, max_bytes: int = OUTPUT_TAIL_BYTES, max_lines: int = OUTPUT_TAIL_LINES) -> list[str]:
    """The last few lines the engine wrote, for a crash that never reached the
    event log. Reads from the end so a long-running engine's output costs the
    same as a short one's."""
    try:
        with path.open("rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - max_bytes))
            raw = fh.read()
    except OSError:
        return []
    text = raw.decode("utf-8", "replace")
    if size > max_bytes:
        # The read started mid-line; that fragment is not a line.
        _, _, text = text.partition("\n")
    return [line for line in text.splitlines() if line.strip()][-max_lines:]


def _cmdline(pid: int) -> str:
    try:
        return Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode()
    except OSError:
        return ""


def _is_engine(pid: int) -> bool:
    """Guard against a recycled PID belonging to some unrelated process.

    A standalone program counts too: without this, an API restart would not
    recognise a running program, and the scheduler would start a second copy
    of it trading the same account.
    """
    from app.programs import is_program_cmdline

    cmdline = _cmdline(pid)
    return "engine.runner" in cmdline or is_program_cmdline(cmdline)


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
        self.outfile = outfile_for(algo_id)
        # How this algorithm's paper/live mode is decided. The built-in reads
        # the operator setting in kv; uploaded algorithms carry their own.
        self._mode_provider = mode_provider
        self.strategy_path = strategy_path
        # Set when this algorithm is a standalone program rather than a
        # strategy module: its path, and the arguments that select each mode.
        self.program_path: Path | None = None
        self.program_args: dict[str, list[str]] = {}
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
        return self._stored_mode()

    def _stored_mode(self) -> str:
        """The algorithm's own record is the one source of truth for its mode.

        The built-in used to read a separate global setting while the run
        dialog wrote the record, so choosing real money for it started the
        engine on paper. The global setting now only mirrors the built-in's
        record, and is read only where no record exists yet.
        """
        algo = self.db.algo(self.algo_id)
        mode = (algo or {}).get("mode") or self.db.kv_get(K_MODE, settings.default_mode)
        return "live" if mode == "live" else "paper"

    def set_mode(self, mode: str) -> None:
        if mode not in ("paper", "live"):
            raise ValueError("mode must be 'paper' or 'live'")
        if self.db.algo(self.algo_id):
            self.db.set_algo_fields(self.algo_id, mode=mode)
        if self.algo_id == DEFAULT_ALGO:
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
                "info", f"API adopted running engine pid {pid}", source="supervisor",
                algo_id=self.algo_id,
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
        self.db.expire_stale_commands(self.algo_id)
        self.state.running = False
        self.state.pid = None
        self.state.last_exit_code = code
        self.state.last_stop_reason = reason
        self.state.run_id = None
        self.proc = None
        self.pidfile.unlink(missing_ok=True)
        crashed = code not in (0, None)
        self.state.stdout_tail = _tail(self.outfile) if crashed else []
        # The last line of a traceback is the exception itself, which is the one
        # line an operator needs to see without opening a shell on the box.
        cause = f" — last output: {self.state.stdout_tail[-1][:300]}" if self.state.stdout_tail else ""
        level = "error" if crashed else "info"
        self.db.add_event(
            level, f"engine stopped (pid {pid}, exit {code}): {reason}{cause}",
            source="supervisor", algo_id=self.algo_id,
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
        cwd = str(settings.backend_dir)
        if self.program_path is not None:
            # A complete program, run as it would be by hand, told its mode.
            cmd = [python, "-u", str(self.program_path), *self.program_args.get(mode, [])]
            cwd = str(self.program_path.parent)
        else:
            cmd = [python, "-m", "engine.runner", "--mode", mode, "--algo", self.algo_id]
            if self.strategy_path is not None:
                cmd += ["--strategy", str(self.strategy_path)]
        env["MERIDIAN_ALGO_ID"] = self.algo_id
        # An engine that dies before telemetry is up — a bad credential, a
        # failed import — writes its only explanation to stderr. Discarding it
        # leaves a crash loop with no cause anywhere, so it goes to a file that
        # is truncated per start and read back when the process exits.
        try:
            out = self.outfile.open("wb")
        except OSError as e:
            self.db.add_event(
                "warn", f"cannot write {self.outfile} ({e}) — engine output will be discarded",
                source="supervisor", algo_id=self.algo_id,
            )
            out = None

        try:
            proc = subprocess.Popen(
                cmd,
                cwd=cwd,
                env=env,
                stdout=out or subprocess.DEVNULL,
                stderr=subprocess.STDOUT if out else subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError as e:
            self.state.last_start_error = str(e)
            self.db.add_event("error", f"engine start failed: {e}", source="supervisor", algo_id=self.algo_id)
            return {"ok": False, "detail": f"spawn failed: {e}"}
        finally:
            # The child holds its own duplicate of the descriptor; this one has
            # to go or every restart leaks a file handle.
            if out is not None:
                out.close()

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
        grace = STOP_GRACE_SEC
        if self.is_program(pid):
            grace = PROGRAM_STOP_GRACE_SEC
            # A program never reads the engine's command queue. SIGINT is the
            # Ctrl-C a program is written to handle — it saves its state and
            # reports before exiting — so it gets that first.
            self.db.add_event(
                "info", f"stopping program (pid {pid}) with SIGINT — {reason}",
                source="supervisor", algo_id=self.algo_id,
            )
            try:
                os.kill(pid, signal.SIGINT)
            except ProcessLookupError:
                self._mark_stopped(None, "already gone")
                return {"ok": True, "detail": "program already gone"}
        else:
            self.db.enqueue_command("stop", {"reason": reason, "force": force}, issued_by=trigger)
        deadline = time.time() + grace
        while time.time() < deadline:
            time.sleep(0.5)
            self.refresh()
            if not self.state.running:
                return {"ok": True, "detail": "engine stopped gracefully"}

        self.db.add_event(
            "warn", f"engine did not stop within {grace:.0f}s — sending SIGTERM",
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

    def is_program(self, pid: int | None = None) -> bool:
        if self.program_path is not None:
            return True
        from app.programs import is_program_cmdline

        return bool(pid) and is_program_cmdline(_cmdline(pid))

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
            "last_output": list(s.stdout_tail),
            "kind": "program" if self.is_program(s.pid) else "engine",
        }
