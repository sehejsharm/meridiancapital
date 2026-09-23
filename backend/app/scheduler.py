"""Automatic start/stop of every armed algorithm around the NSE session.

The scheduler is deliberately level-triggered rather than cron-driven: it asks
"what should be up right now?" every few seconds and reconciles. A missed tick,
a reboot mid-session, or an API redeploy therefore self-heals instead of
leaving things down until tomorrow's cron fires.

Starting an algorithm arms it. From then on the scheduler brings it up at the
open and takes it down at the close, every trading day, until someone stops it
— a stop is how you disarm it, and a stop mid-session keeps it down for the
rest of that day rather than being undone on the next tick.
"""

from __future__ import annotations

import asyncio

from app.config import settings
from app.supervisor import DEFAULT_ALGO, Supervisor
from engine.clock import now_ist
from shared.db import K_SCHEDULE, Database
from shared.market_calendar import (
    calendar_configured,
    is_trading_day,
    next_transition,
    should_be_running,
)

TICK_SECONDS = 15


class Scheduler:
    def __init__(self, db: Database, sup: Supervisor, fleet=None):
        self.db = db
        self.sup = sup
        # Set after construction: the fleet needs the built-in's supervisor,
        # which is this one, so the two cannot be built in one expression.
        self.fleet = fleet
        self.task: asyncio.Task | None = None
        self.last_decision = ""
        self.last_tick_ts: str | None = None
        self.fleet_decisions: dict[str, str] = {}

    # ── settings ─────────────────────────────────────────────────────────────
    def enabled(self) -> bool:
        return bool(self.db.kv_get(K_SCHEDULE, settings.autostart_enabled))

    def set_enabled(self, value: bool) -> None:
        self.db.kv_set(K_SCHEDULE, bool(value))

    def holiday_set(self) -> set[str]:
        return {h["day"] for h in self.db.holidays()}

    # ── loop ─────────────────────────────────────────────────────────────────
    async def start(self) -> None:
        self.task = asyncio.create_task(self._loop(), name="meridian-scheduler")

    async def stop(self) -> None:
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass

    async def _loop(self) -> None:
        while True:
            try:
                await asyncio.to_thread(self.tick)
            except Exception as e:
                self.db.add_event("error", f"scheduler tick failed: {e}", source="scheduler")
            await asyncio.sleep(TICK_SECONDS)

    def tick(self) -> None:
        now = now_ist()
        self.last_tick_ts = now.isoformat(timespec="seconds")
        self.sup.refresh()
        holidays = self.holiday_set()
        want = should_be_running(now, holidays)
        if self.enabled():
            # A failure here must not stop the built-in from being reconciled.
            try:
                self.tick_fleet(want)
            except Exception as e:
                self.db.add_event("error", f"fleet reconcile failed: {e}", source="scheduler")

        if not want:
            # Outside the session window the operator's manual stop is forgotten,
            # so tomorrow starts clean.
            self.sup.state.manual_override = False

        if not self.enabled():
            self.last_decision = "automation disarmed"
            return

        if want and not self.sup.state.running:
            if self.sup.state.manual_override:
                self.last_decision = "in session window but stopped by operator"
                return
            crashed = self.sup.state.last_exit_code not in (0, None)
            if crashed:
                if self.sup.state.restarts_this_session >= settings.max_restarts_per_session:
                    self.last_decision = (
                        f"engine crashed {self.sup.state.restarts_this_session}x — "
                        f"not restarting again today, operator action required"
                    )
                    return
                self.sup.note_crash()
                self.db.add_event(
                    "warn",
                    f"engine is down mid-session (exit {self.sup.state.last_exit_code}) — "
                    f"restarting (attempt {self.sup.state.restarts_this_session})",
                    source="scheduler",
                )
            res = self.sup.start(trigger="schedule")
            self.last_decision = f"start: {res.get('detail') or 'started'}"
            return

        if not want and self.sup.state.running:
            res = self.sup.stop(reason="session ended", force=True, trigger="schedule")
            self.sup.state.manual_override = False
            self.last_decision = f"stop: {res.get('detail')}"
            return

        self.last_decision = "engine state matches schedule"

    # ── the rest of the fleet ────────────────────────────────────────────────
    def tick_fleet(self, want: bool) -> None:
        """Bring every armed algorithm in line with the session window.

        Armed means the operator started it at least once and has not stopped
        it since; the registry's ``enabled`` flag carries that. The built-in is
        skipped because ``tick`` above already owns its supervisor.
        """
        if self.fleet is None:
            return
        self.fleet.sync()
        for algo in self.db.algos():
            algo_id = algo["id"]
            if algo_id == DEFAULT_ALGO:
                continue
            sup = self.fleet.get(algo_id)
            if sup is None:
                continue
            sup.refresh()

            if not want:
                # Yesterday's manual stop must not keep it down tomorrow.
                sup.state.manual_override = False
                if sup.state.running:
                    res = self.fleet.stop(
                        algo_id, reason="session ended", force=True, manual=False
                    )
                    self.fleet_decisions[algo_id] = f"stop: {res.get('detail')}"
                continue

            if not algo.get("enabled"):
                self.fleet_decisions[algo_id] = "not armed"
                continue
            if sup.state.running:
                self.fleet_decisions[algo_id] = "running"
                continue
            if sup.state.manual_override:
                self.fleet_decisions[algo_id] = "stopped by operator"
                continue
            if sup.state.last_exit_code not in (0, None):
                if sup.state.restarts_this_session >= settings.max_restarts_per_session:
                    self.fleet_decisions[algo_id] = (
                        f"crashed {sup.state.restarts_this_session}x — not restarting again today"
                    )
                    continue
                sup.note_crash()
                self.db.add_event(
                    "warn",
                    f"'{algo.get('name') or algo_id}' is down mid-session "
                    f"(exit {sup.state.last_exit_code}) — restarting "
                    f"(attempt {sup.state.restarts_this_session})",
                    source="scheduler", algo_id=algo_id,
                )
            res = self.fleet.start(algo_id, trigger="schedule")
            self.fleet_decisions[algo_id] = f"start: {res.get('detail') or 'started'}"

    # ── status for the dashboard ─────────────────────────────────────────────
    def status(self) -> dict:
        now = now_ist()
        holidays = self.holiday_set()
        return {
            "enabled": self.enabled(),
            "now_ist": now.isoformat(timespec="seconds"),
            "is_trading_day": is_trading_day(now.date(), holidays),
            "in_session_window": should_be_running(now, holidays),
            "next": next_transition(now, holidays),
            "holiday_count": len(holidays),
            "calendar_configured": calendar_configured(holidays, now.year),
            "last_tick": self.last_tick_ts,
            "last_decision": self.last_decision,
            "manual_override": self.sup.state.manual_override,
            "fleet_decisions": dict(self.fleet_decisions),
            "max_restarts_per_session": settings.max_restarts_per_session,
        }
