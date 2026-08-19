"""Bot supervisor.

Runs the algorithm as a child process, reads everything it prints, turns that
into events the phone can render, and keeps it alive during market hours.

Why a subprocess and not a thread: the algorithm blocks, sleeps, and is
expected to be killable at a fixed wall-clock time. A separate process can be
signalled and, if it ever wedges, killed outright without taking the API down
with it. Its stdout is the whole interface.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

from . import db
from .bot.emitter import EVENT_PREFIX
from .config import settings
from .holidays import is_trading_day, why_not_trading

ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

# Overridable so the supervisor's plumbing can be exercised without a broker.
BOT_MODULE = os.getenv("BOT_MODULE", "app.bot.strategy")

# Events that are pure UI chatter — broadcast live, never stored. The chart
# is a full redraw every 15 seconds; persisting it would bloat the database
# with data the candles themselves can always reproduce.
EPHEMERAL_KINDS = {"status", "chart"}

# Restart policy when the process dies unexpectedly mid-session.
MAX_RESTARTS_PER_SESSION = 5
RESTART_BACKOFF_SECONDS = [10, 30, 60, 120, 300]


def _strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)


class BotSupervisor:
    """Owns one bot process and fans its output out to listeners.

    One instance per slot. Slot 0 is the original lane — every row written
    before slots existed belongs to it — and slots 1-4 start empty, waiting for
    an algorithm to be uploaded to them.
    """

    def __init__(self, slot: int = 0, name: str = "") -> None:
        self.slot = slot
        self.name = name or (f"Slot {slot + 1}" if slot else "Primary")
        self.proc: Optional[subprocess.Popen] = None
        self.run_id: Optional[int] = None
        self.state: str = "stopped"          # stopped|starting|running|stopping|error
        self.started_at: Optional[datetime] = None
        self.stop_reason: Optional[str] = None
        self.last_error: Optional[str] = None
        self.restarts: int = 0
        self.session_date: str = db.today_str()

        self.snapshot: dict[str, Any] = {}   # latest `status` payload
        self.chart: dict[str, Any] = {}      # latest `chart` payload
        self.last_event: dict[str, Any] = {}
        self.tail: list[dict] = []           # in-memory ring of recent lines

        self._subscribers: set[asyncio.Queue] = set()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._reader: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._manual_stop = False
        self._seq = 0

    # ------------------------------------------------------------ wiring

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)

    def _broadcast(self, event: dict) -> None:
        """Called from the reader thread — hop onto the event loop."""
        if self._loop is None:
            return
        try:
            self._loop.call_soon_threadsafe(self._fanout, event)
        except RuntimeError:
            pass

    def _fanout(self, event: dict) -> None:
        dead = []
        for q in self._subscribers:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                # A phone that stopped draining gets dropped rather than
                # backing pressure up into the reader thread.
                dead.append(q)
        for q in dead:
            self._subscribers.discard(q)

    # ------------------------------------------------------------ lifecycle

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def start(self, trigger: str = "manual", force: bool = False) -> dict:
        with self._lock:
            if self.running:
                return {"ok": False, "reason": "already_running", "state": self.state}

            today = date.today()
            if not force and not is_trading_day(today, settings.skip_holidays):
                reason = why_not_trading(today, settings.skip_holidays) or "not a trading day"
                self._emit_local("scheduler", f"Start skipped — {reason}", level="warn")
                return {"ok": False, "reason": reason, "state": self.state}

            missing = settings.missing_credentials()  # broker, not login
            if missing:
                msg = "Missing credentials: " + ", ".join(missing)
                self.state = "error"
                self.last_error = msg
                self._emit_local("fatal", msg, level="error")
                return {"ok": False, "reason": msg, "state": self.state}

            self.session_date = db.today_str()
            self._manual_stop = False
            self.stop_reason = None
            self.last_error = None
            self.state = "starting"

            env = os.environ.copy()
            env.update({
                "PYTHONUNBUFFERED": "1",
                "TZ": settings.tz,
                "DATA_DIR": str(settings.data_dir),
                "PAPER_MODE": "true" if settings.paper_mode else "false",
                "SEED_CAPITAL": str(settings.seed_capital),
                "PAPER_SLIPPAGE_PCT": str(settings.paper_slippage_pct),
                "ANGEL_API_KEY": settings.angel_api_key,
                "ANGEL_SECRET_KEY": settings.angel_secret_key,
                "ANGEL_CLIENT_ID": settings.angel_client_id,
                "ANGEL_PASSWORD": settings.angel_password,
                "ANGEL_TOTP_SECRET": settings.angel_totp_secret,
            })

            # Strategy parameters are resolved at launch, so a running bot
            # never has the rules changed underneath an open position.
            try:
                from . import strategy_config
                params = strategy_config.effective()
                problems = strategy_config.check_invariants(params)
                if problems:
                    msg = "Strategy parameters are inconsistent: " + " ".join(problems)
                    self.state = "error"
                    self.last_error = msg
                    self._emit_local("fatal", msg, level="error")
                    return {"ok": False, "reason": msg, "state": self.state}
                env.update(strategy_config.as_env())
                drift = strategy_config.drift_from_v11(params)
                if drift:
                    self._emit_local(
                        "strategy",
                        f"Starting with {len(drift)} parameter(s) changed from the v11 baseline",
                        level="warn", drift=drift)
            except Exception as exc:
                self._emit_local(
                    "strategy",
                    f"Could not resolve strategy overrides ({exc}); using v11 defaults",
                    level="warn")

            # Whichever algorithm is active — the built-in module, or an
            # uploaded file that passed validation.
            command = [sys.executable, "-u", "-m", BOT_MODULE]
            try:
                from . import algorithms
                kind, value = algorithms.active_target(self.slot)
                if kind == "none":
                    # Only slot 0 falls back to the built-in v11. An empty slot
                    # silently running the same algorithm as slot 0 would double
                    # the position without anyone asking for it.
                    msg = f"{self.name} has no algorithm — upload one before starting it."
                    self.state = "stopped"
                    return {"ok": False, "reason": msg, "state": self.state}
                if kind == "file":
                    command = [sys.executable, "-u", value]
                    self._emit_local(
                        "algorithm",
                        f"Running uploaded algorithm: {algorithms.active_description(self.slot)}",
                        level="warn", target=value)
            except Exception as exc:
                self._emit_local(
                    "algorithm",
                    f"Could not resolve the active algorithm ({exc}); using the built-in",
                    level="warn")

            backend_dir = Path(__file__).resolve().parent.parent
            try:
                self.proc = subprocess.Popen(
                    command,
                    cwd=str(backend_dir),
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    start_new_session=True,   # own process group, so we can signal the tree
                )
            except Exception as exc:
                self.state = "error"
                self.last_error = str(exc)
                self._emit_local("fatal", f"Could not launch bot: {exc}", level="error")
                return {"ok": False, "reason": str(exc), "state": self.state}

            self.started_at = datetime.now()
            self.state = "running"
            self.run_id = db.start_run(self.session_date, self.proc.pid, trigger,
                                       slot=self.slot)
            self._emit_local(
                "supervisor",
                f"Bot process started (pid {self.proc.pid}, trigger {trigger})",
                level="success", pid=self.proc.pid, trigger=trigger,
            )

            self._reader = threading.Thread(
                target=self._read_output, args=(self.proc,), daemon=True,
                name="bot-stdout-reader",
            )
            self._reader.start()
            return {"ok": True, "pid": self.proc.pid, "state": self.state}

    def stop(self, reason: str = "manual", timeout: float = 45.0) -> dict:
        """Ask the bot to stop, then insist.

        SIGTERM lets the algorithm flatten any open position and file its EOD
        report, which is why the grace period is generous. SIGKILL is the
        backstop for a wedged process.
        """
        with self._lock:
            if not self.running:
                self.state = "stopped"
                return {"ok": False, "reason": "not_running", "state": self.state}

            self._manual_stop = True
            self.state = "stopping"
            self.stop_reason = reason
            proc = self.proc
            self._emit_local("supervisor", f"Stopping bot — {reason}", level="warn", reason=reason)

        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                proc.terminate()
            except Exception:
                pass

        deadline = time.time() + timeout
        while time.time() < deadline and proc.poll() is None:
            time.sleep(0.5)

        if proc.poll() is None:
            self._emit_local("supervisor", "Graceful stop timed out — killing process",
                             level="error")
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                try:
                    proc.kill()
                except Exception:
                    pass

        self.state = "stopped"
        return {"ok": True, "state": self.state, "reason": reason}

    def restart(self, reason: str = "manual restart") -> dict:
        if self.running:
            self.stop(reason=reason)
        return self.start(trigger="restart")

    # ------------------------------------------------------------ output

    def _read_output(self, proc: subprocess.Popen) -> None:
        """Consume the child's stdout until it closes, then reap it."""
        try:
            assert proc.stdout is not None
            for raw in proc.stdout:
                line = raw.rstrip("\n")
                if not line.strip():
                    continue
                if line.startswith(EVENT_PREFIX):
                    self._handle_structured(line[len(EVENT_PREFIX):])
                else:
                    self._handle_log(line)
        except Exception as exc:  # pragma: no cover - reader must never die loudly
            self._emit_local("supervisor", f"Output reader error: {exc}", level="error")
        finally:
            code = proc.wait()
            self._on_exit(code)

    def _handle_structured(self, blob: str) -> None:
        try:
            rec = json.loads(blob)
        except json.JSONDecodeError:
            self._handle_log(EVENT_PREFIX + blob)
            return

        kind = rec.get("kind", "event")
        payload = rec.get("payload") or {}
        event = {
            "ts": rec.get("ts") or datetime.now().isoformat(timespec="milliseconds"),
            "session_date": self.session_date,
            "slot": self.slot, "slot_name": self.name,
            "kind": kind,
            "level": rec.get("level", "info"),
            "message": rec.get("message", ""),
            "payload": payload,
            "seq": rec.get("seq", 0),
        }

        if kind == "status":
            self.snapshot = payload
            self.snapshot["_ts"] = event["ts"]
        elif kind == "chart":
            self.chart = payload
            self.chart["_ts"] = event["ts"]
        else:
            self.last_event = event
            self._remember(event)

        self._persist(event)
        self._side_effects(kind, payload, event)
        self._broadcast(event)

    def _handle_log(self, line: str) -> None:
        clean = _strip_ansi(line).rstrip()
        if not clean.strip():
            return
        self._seq += 1
        event = {
            "ts": datetime.now().isoformat(timespec="milliseconds"),
            "session_date": self.session_date,
            "slot": self.slot, "slot_name": self.name,
            "kind": "log",
            "level": _guess_level(clean),
            "message": clean,
            "payload": None,
            "seq": self._seq,
        }
        self._remember(event)
        self._persist(event)
        self._broadcast(event)

    def _remember(self, event: dict) -> None:
        self.tail.append(event)
        if len(self.tail) > 500:
            del self.tail[:-500]

    def _persist(self, event: dict) -> None:
        if event["kind"] in EPHEMERAL_KINDS:
            return
        try:
            event_id = db.insert_event(
                ts=event["ts"], session_date=event["session_date"],
                kind=event["kind"], message=event["message"],
                level=event["level"], payload=event["payload"],
                seq=event.get("seq", 0), run_id=self.run_id, slot=self.slot,
            )
            event["id"] = event_id
        except Exception:
            pass

    def _side_effects(self, kind: str, payload: dict, event: dict) -> None:
        """Turn certain events into rows the Reports screen reads."""
        try:
            if kind == "exit" and payload.get("ledger"):
                db.upsert_trade({**_ledger_to_row(payload["ledger"]), "slot": self.slot})
            elif kind == "eod" and payload.get("report"):
                db.upsert_session({**_eod_to_row(payload), "slot": self.slot})
            elif kind == "minute":
                db.insert_equity_mark(
                    ts=event["ts"], session_date=event["session_date"],
                    equity=float(payload.get("equity") or 0),
                    day_pnl=float(payload.get("day_pnl") or 0),
                    open_position=bool(self.snapshot.get("position")),
                    unrealised=float(self.snapshot.get("unrealised") or 0),
                    slot=self.slot,
                )
        except Exception:
            pass

        from .push import notify_event  # local import avoids a cycle at import time
        try:
            notify_event(kind, payload, event)
        except Exception:
            pass

    # ------------------------------------------------------------ exit

    def _on_exit(self, code: int) -> None:
        was_manual = self._manual_stop
        self.state = "stopped"
        if self.run_id:
            db.end_run(self.run_id, self.stop_reason or ("exit" if code == 0 else "crash"), code)

        # A clean exit is a clean exit whether or not anybody pressed Stop, and
        # "exited (code 0) unexpectedly" told operators their algorithm had
        # crashed when it had done exactly what it was supposed to. SIGTERM
        # (-15/143) is how the supervisor asks it to stop, so that is clean too.
        clean = code in (0, -15, 143)
        level = "info" if clean else "error"
        if clean:
            message = ("Bot process stopped cleanly" if was_manual
                       else "Bot process finished and stopped cleanly")
        else:
            message = f"Bot process exited unexpectedly (code {code})"
        self._emit_local(
            "supervisor", message,
            level=level, exit_code=code, manual=was_manual, clean=clean,
        )

        if code == 2:
            self.state = "error"
            self.last_error = "Startup failed — check credentials and connectivity"

        if was_manual or code == 0 or code == 2:
            return

        # Unexpected death. If we are still inside the session window on a
        # trading day, bring it back up — an unattended crash at 10:00 should
        # not silently cost the rest of the day.
        if self._inside_window() and self.restarts < MAX_RESTARTS_PER_SESSION:
            delay = RESTART_BACKOFF_SECONDS[min(self.restarts, len(RESTART_BACKOFF_SECONDS) - 1)]
            self.restarts += 1
            self._emit_local(
                "supervisor",
                f"Restarting in {delay}s (attempt {self.restarts}/{MAX_RESTARTS_PER_SESSION})",
                level="warn", delay=delay, attempt=self.restarts,
            )
            threading.Timer(delay, lambda: self.start(trigger="restart")).start()
        elif self.restarts >= MAX_RESTARTS_PER_SESSION:
            self.state = "error"
            self.last_error = f"Gave up after {self.restarts} restarts"
            self._emit_local("supervisor", self.last_error, level="error")

    def _inside_window(self) -> bool:
        now = datetime.now()
        if not is_trading_day(now.date(), settings.skip_holidays):
            return False
        return settings.session_start <= now.time() < settings.session_stop

    # ------------------------------------------------------------ helpers

    def _emit_local(self, kind: str, message: str, level: str = "info", **payload) -> None:
        """Supervisor's own voice — same pipeline as the bot's output."""
        self._seq += 1
        event = {
            "ts": datetime.now().isoformat(timespec="milliseconds"),
            "session_date": self.session_date,
            "slot": self.slot, "slot_name": self.name,
            "kind": kind, "level": level, "message": message,
            "payload": payload or None, "seq": self._seq,
        }
        self.last_event = event
        self._remember(event)
        self._persist(event)
        self._broadcast(event)

    def status(self) -> dict:
        now = datetime.now()
        uptime = (now - self.started_at).total_seconds() if self.started_at and self.running else 0
        trading_day = is_trading_day(now.date(), settings.skip_holidays)
        return {
            "state": self.state,
            "running": self.running,
            "pid": self.proc.pid if self.proc and self.running else None,
            "started_at": self.started_at.isoformat(timespec="seconds") if self.started_at else None,
            "uptime_seconds": int(uptime),
            "session_date": self.session_date,
            "slot": self.slot, "slot_name": self.name,
            "restarts": self.restarts,
            "stop_reason": self.stop_reason,
            "last_error": self.last_error,
            "server_time": now.isoformat(timespec="seconds"),
            "timezone": settings.tz,
            "is_trading_day": trading_day,
            "not_trading_reason": why_not_trading(now.date(), settings.skip_holidays),
            "inside_window": self._inside_window(),
            "schedule": {
                "start": settings.session_start.strftime("%H:%M"),
                "stop": settings.session_stop.strftime("%H:%M"),
                "auto": settings.auto_schedule,
            },
            "snapshot": self.snapshot,
            "last_event": self.last_event,
        }


# A risk rail reporting that it has not been touched is the most reassuring
# line the bot prints, and it printed roughly every minute. Matching the words
# "daily kill" made all of it read as failure: the error counter ran to 166 on
# a healthy session, and the one genuine fault — the broker rate-limiting us —
# was buried in it. An operator who learns to ignore a red counter is worse off
# than one with no counter at all, so severity is decided by what a line means,
# not by whether an alarming word appears in it.

_EXPLICIT = (
    ("[FATAL]", "critical"), ("[CRITICAL]", "critical"),
    ("[ERROR]", "error"), ("[FAIL]", "error"),
    ("[WARN]", "warn"), ("[WARNING]", "warn"),
    ("[OK]", "success"), ("[DEBUG]", "debug"),
)

# Things that genuinely went wrong, whoever printed them.
_ERROR_SIGNS = (
    "TRACEBACK", "EXCEPTION", "ACCESS DENIED", "RATE LIMIT", "EXCEEDING ACCESS",
    "ORDER REJECTED", "REJECTED BY", "LOGIN FAILED", "AUTH FAILED",
    "CONNECTION REFUSED", "TIMED OUT", "COULDN'T PARSE", "COULD NOT PARSE",
)

# The kill switch is only news when it actually bites.
_KILL_BREACHED = ("KILL SWITCH HIT", "DAILY KILL HIT", "KILL SWITCH TRIGGERED",
                  "HALTED", "TRADING STOPPED")


def _guess_level(line: str) -> str:
    """Classify a plain stdout line: debug | info | success | warn | error | critical."""
    upper = line.upper()

    for tag, level in _EXPLICIT:
        if tag in upper:
            return level

    if any(sign in upper for sign in _ERROR_SIGNS):
        return "error"

    # "Daily kill : Rs 0 / Rs 3,000 used" is the rail reporting itself healthy.
    # Only a breach, or a rail that has actually been consumed, is worth a
    # colour — and even then it is a warning until the bot says it has halted.
    if "KILL" in upper:
        if any(sign in upper for sign in _KILL_BREACHED):
            return "error"
        used = _kill_used_fraction(line)
        if used is not None and used >= 0.6:
            return "warn"
        return "info"

    if any(k in upper for k in ("WARNING", "BLOCKED", "NO ENTRY", "CHOP SKIP")):
        return "warn"
    if any(k in upper for k in ("ORDER TAKEN", "LADDER", "POSITION CLOSED")):
        return "success"
    return "info"


_KILL_NUMS = re.compile(r"([\d,]+(?:\.\d+)?)\s*/\s*(?:RS\.?\s*)?([\d,]+(?:\.\d+)?)",
                        re.IGNORECASE)


def _kill_used_fraction(line: str) -> Optional[float]:
    """How much of the kill-switch budget a status line says has been spent."""
    m = _KILL_NUMS.search(line.replace("Rs", "").replace("₹", ""))
    if not m:
        return None
    try:
        used = float(m.group(1).replace(",", ""))
        limit = float(m.group(2).replace(",", ""))
    except ValueError:
        return None
    return used / limit if limit > 0 else None


def _ledger_to_row(led: dict) -> dict:
    """The algorithm's CSV record -> the trades table."""
    return {
        "session_date": led.get("Date"),
        "mode": led.get("Mode"),
        "entry_time": led.get("EntryTime"),
        "exit_time": led.get("ExitTime"),
        "hold_min": led.get("HoldMin"),
        "symbol": led.get("Symbol"),
        "opt_type": led.get("Type"),
        "strike": led.get("Strike"),
        "qty": led.get("Qty"),
        "avg_entry": led.get("AvgEntry"),
        "exit_fill": led.get("ExitFill"),
        "model_prem": led.get("ModelPrem"),
        "lot_cost": led.get("LotCost"),
        "real_margin": led.get("RealMargin"),
        "risk_rs": led.get("RiskRs"),
        "gross_pnl": led.get("GrossPnL"),
        "brokerage": led.get("BrokerageTotal"),
        "stt": led.get("STT"),
        "exch_txn": led.get("ExchTxn"),
        "sebi": led.get("SEBI"),
        "stamp": led.get("Stamp"),
        "gst": led.get("GST"),
        "charges": led.get("Charges"),
        "net_pnl": led.get("NetPnL"),
        "reason": led.get("Reason"),
        "stage": led.get("Stage"),
        "entry_reason": led.get("EntryReason"),
        "spot_at_entry": led.get("SpotAtEntry"),
        "garch_vol": led.get("GarchVol"),
        "entry_iv": led.get("EntryIV"),
        "day_pnl": led.get("DayPnL"),
        "equity_after": led.get("EquityAfter"),
        "latency_ms": led.get("LatencyMs"),
    }


def _eod_to_row(payload: dict) -> dict:
    r = payload.get("report", {})
    return {
        "session_date": r.get("Date"),
        "mode": r.get("Mode"),
        "trades": r.get("Trades"),
        "open_equity": r.get("OpenEquity"),
        "close_equity": r.get("CloseEquity"),
        "day_pnl": r.get("DayPnL"),
        "charges": r.get("Charges"),
        "killed": 1 if r.get("Killed") else 0,
        "chop_blocked": 1 if r.get("ChopBlocked") else 0,
        "chop_score": r.get("ChopScore"),
        "garch": r.get("GARCH"),
        "adx": r.get("ADX"),
        "vol_regime": r.get("VolRegime"),
        "trend": r.get("Trend"),
        "direction": r.get("Direction"),
        "efficiency": r.get("Efficiency"),
        "day_range_pts": r.get("DayRangePts"),
        "avg_latency_ms": r.get("AvgLatencyMs"),
        "peak_equity": r.get("PeakEquity"),
        "drawdown_pct": r.get("DrawdownPct"),
        "win_rate": payload.get("win_rate"),
        "profit_factor": payload.get("profit_factor"),
    }


# ---------------------------------------------------------------- the fleet

MAX_SLOTS = 5

fleet: list[BotSupervisor] = [
    BotSupervisor(slot=i, name="Primary" if i == 0 else f"Slot {i + 1}")
    for i in range(MAX_SLOTS)
]

# Everything written before slots existed talks to `supervisor`, and slot 0 is
# that same lane, so the old name keeps working rather than being churned
# through every call site.
supervisor = fleet[0]


def get_slot(slot: int) -> BotSupervisor:
    if not 0 <= slot < MAX_SLOTS:
        raise ValueError(f"Slot must be 0–{MAX_SLOTS - 1}")
    return fleet[slot]


def available_mb() -> Optional[int]:
    """Free memory including reclaimable cache, or None where unknowable."""
    try:
        with open("/proc/meminfo") as fh:
            for line in fh:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) // 1024
    except (OSError, ValueError, IndexError):
        pass
    return None


# Measured against the running v11: a Python process with pandas, numpy and
# scipy resident sits around this once the scrip master is cached to disk.
SLOT_FOOTPRINT_MB = 190


def capacity_warning() -> Optional[str]:
    """What starting one more slot would do to this box, in plain terms.

    The free Oracle shape is 1 GB. Four algorithms will not fit in it, and a
    trading process that is swapping is worse than one that never started —
    it misses fills rather than failing loudly.
    """
    free = available_mb()
    if free is None:
        return None
    if free < SLOT_FOOTPRINT_MB:
        return (f"{free} MB AVAILABLE — CAPACITY EXHAUSTED. An algorithm needs "
                f"about {SLOT_FOOTPRINT_MB} MB; starting another will force this "
                f"server into swap, which costs fills.")
    if free < SLOT_FOOTPRINT_MB * 2:
        return (f"{free} MB AVAILABLE — CAPACITY: +1 ALGORITHM. "
                f"Monitor memory utilisation before deploying further.")
    return None


def fleet_status() -> dict:
    running = [s for s in fleet if s.running]
    free = available_mb()
    return {
        "max_slots": MAX_SLOTS,
        "running_count": len(running),
        "memory_free_mb": free,
        "slot_footprint_mb": SLOT_FOOTPRINT_MB,
        "headroom_slots": None if free is None else max(0, free // SLOT_FOOTPRINT_MB),
        "capacity_warning": capacity_warning(),
        "slots": [
            {
                "slot": s.slot,
                "name": s.name,
                "state": s.state,
                "running": s.running,
                "uptime_seconds": int((datetime.now() - s.started_at).total_seconds())
                                  if s.started_at and s.running else 0,
                "last_error": s.last_error,
                "restarts": s.restarts,
                "day_pnl": (s.snapshot or {}).get("day_pnl"),
                "equity": (s.snapshot or {}).get("equity"),
                "position": bool((s.snapshot or {}).get("position")),
                "trades": (s.snapshot or {}).get("trades"),
                # The full snapshot rides along so the deck can render any slot
                # from this one call, rather than only ever showing slot 0
                # because that is whose status the websocket happened to carry.
                "snapshot": s.snapshot or {},
                "last_event_ts": (s.last_event or {}).get("ts"),
                # An algorithm that never emits @@EVT@@ runs perfectly well and
                # reports nothing, which on a dashboard is indistinguishable
                # from a flat, idle book. Saying so is the difference between
                # "no trades" and "no idea".
                "reporting": bool(s.snapshot),
            }
            for s in fleet
        ],
    }
