"""The desk's housekeeping, run on a clock beside the scheduler.

* **Outside heartbeat** — every minute the API fetches ``MERIDIAN_HEARTBEAT_URL``.
  A monitor outside the VM (healthchecks.io, Better Stack, UptimeRobot: any
  "tell me when the pings stop" service) raises the alarm when they stop, which
  is the one failure the desk cannot report about itself: the VM or the API
  being down. Nothing is sent unless the address is set.
* **After-close check** — once, from 15:35 on a trading day (``close_check``).
* **Weekly summary** — after the close on the week's last trading day.
* **Journal retention** — once a day after the session (``retention``).
* **Machine warnings** — disk, memory or load over its threshold, once a day.

Each job is independent: one failing is written to the journal and the others
still run. None of them can start, stop or change an algorithm.
"""

from __future__ import annotations

import asyncio
import os
import time
import urllib.request

from app import backups, close_check, health, notify, retention, weekly
from engine.clock import now_ist
from shared.market_calendar import is_trading_day

TICK_SECONDS = 20.0
HEARTBEAT_EVERY_SEC = 60.0
HEARTBEAT_TIMEOUT_SEC = 8.0
RETENTION_AFTER = (16, 0)
MACHINE_EVERY_SEC = 600.0
K_DONE = "upkeep:done"


def heartbeat_url() -> str:
    return os.environ.get("MERIDIAN_HEARTBEAT_URL", "").strip()


def _fetch(url: str) -> None:
    if not url.lower().startswith("https://"):
        raise ValueError("the heartbeat address must start with https://")
    req = urllib.request.Request(url, headers={"User-Agent": "meridian-heartbeat"})
    with urllib.request.urlopen(req, timeout=HEARTBEAT_TIMEOUT_SEC) as resp:  # noqa: S310 - https only, operator's own
        resp.read(64)


class Upkeep:
    def __init__(self, db, fleet=None, sender=notify.send, fetch=_fetch, angel=close_check.ask_angel,
                 dashboard: str = ""):
        self.db = db
        self.fleet = fleet
        self.sender = sender
        self.fetch = fetch
        self.angel = angel
        self.dashboard = dashboard
        self._task: asyncio.Task | None = None
        self.heartbeat = {"configured": bool(heartbeat_url()), "last_ok": None, "last_try": None,
                          "last_error": None, "sent": 0, "failed": 0}
        self._beat_at = 0.0
        self._machine_at = 0.0

    # ── what has been done today ─────────────────────────────────────────────
    def _done(self) -> dict:
        return dict(self.db.kv_get(K_DONE, None) or {})

    def _mark(self, job: str, value: str) -> None:
        done = self._done()
        done[job] = value
        self.db.kv_set(K_DONE, done)

    def _holidays(self) -> set[str]:
        return {h["day"] for h in self.db.holidays()}

    def _running(self) -> bool:
        if self.fleet is None:
            return False
        try:
            self.fleet.refresh()
            return any(s.state.running for s in self.fleet.all().values())
        except Exception:
            return True   # unknown is treated as running: Angel is not asked

    # ── jobs ─────────────────────────────────────────────────────────────────
    def beat(self, now=None) -> bool | None:
        """Ping the outside monitor. None when no address is set."""
        url = heartbeat_url()
        self.heartbeat["configured"] = bool(url)
        if not url:
            return None
        now = now or now_ist()
        stamp = now.isoformat(timespec="seconds")
        self.heartbeat["last_try"] = stamp
        try:
            self.fetch(url)
        except Exception as e:  # the address is never logged: it is the monitor's secret
            was_ok = self.heartbeat["last_error"] is None
            self.heartbeat["last_error"] = type(e).__name__
            self.heartbeat["failed"] += 1
            if was_ok:
                self.db.add_event("warn", f"the outside heartbeat could not be sent ({type(e).__name__}); "
                                          f"the monitor will raise its alarm if this continues", source="api")
            return False
        if self.heartbeat["last_error"] is not None:
            self.db.add_event("info", "the outside heartbeat is being sent again", source="api")
        self.heartbeat.update(last_ok=stamp, last_error=None)
        self.heartbeat["sent"] += 1
        return True

    def after_close(self, now) -> dict | None:
        """The after-close check, once per trading day from 15:35."""
        day = now.date().isoformat()
        if not is_trading_day(now.date(), self._holidays()) or (now.hour, now.minute) < close_check.RUN_AFTER:
            return None
        if self._done().get("close_check") == day:
            return None
        running = self._running()
        if running and (now.hour, now.minute) < (16, 30):
            return None   # wait for the last program to stop; after 16:30 check the records anyway
        result = close_check.run(self.db, day, running=running, angel=self.angel)
        self._mark("close_check", day)
        line = close_check.headline(result)
        level = {"ok": "ok", "check": "warn", "problem": "critical"}[result["status"]]
        self.db.add_event(level, line, source="api")
        for f in result["findings"][:8]:
            self.db.add_event("critical" if f["level"] == "critical" else "warn",
                              f"after-close check: {f['text']}", source="api")
        if result["status"] != "ok" or result["trades"]:
            body = line if result["status"] == "ok" else "\n".join(f["text"] for f in result["findings"][:6])
            self.sender("After-close check" if result["status"] == "ok" else "AFTER-CLOSE CHECK: ACTION NEEDED",
                        f"{day}\n{body}", "close_check", "critical" if result["status"] == "problem"
                        else ("warn" if result["status"] == "check" else "info"))
        return result

    def weekly_summary(self, now) -> dict | None:
        if not weekly.due(now, self._holidays()):
            return None
        wk = weekly.week_key(now.date())
        if self._done().get("weekly") == wk:
            return None
        s = weekly.summary(self.db, now.date())
        self._mark("weekly", wk)
        went = self.sender("Week in review", weekly.text(s, self.dashboard), "weekly", "info")
        sent = any(v is True for v in (went or {}).values())
        self.db.add_event("info", f"week in review: {s['trades']} real-money trade(s), net Rs {s['net']:,.2f}"
                          + ("" if sent else " (no alert channel is set up, so it was not sent to a phone)"),
                          source="api")
        return s

    def journal_retention(self, now) -> dict | None:
        day = now.date().isoformat()
        if (now.hour, now.minute) < RETENTION_AFTER or self._done().get("retention") == day:
            return None
        self._mark("retention", day)
        out = retention.run(self.db, now.date())
        if out["archived"]:
            self.db.add_event("info", f"journal retention: {out['archived']:,} lines older than "
                                      f"{out['keep_days']} days archived to {', '.join(out['files'])} and removed",
                              source="api")
        return out

    def machine_warnings(self, now) -> list[dict]:
        day = now.date().isoformat()
        if self._done().get("machine") == day or time.time() - self._machine_at < MACHINE_EVERY_SEC:
            return []
        self._machine_at = time.time()
        warnings = health.host(self.db)["warnings"]
        if not warnings:
            return []
        self._mark("machine", day)
        text = "\n".join(w["text"] for w in warnings)
        critical = any(w["level"] == "critical" for w in warnings)
        self.db.add_event("critical" if critical else "warn", "machine: " + "; ".join(w["text"] for w in warnings),
                          source="api")
        self.sender("Server needs attention", text, "machine", "critical" if critical else "warn")
        return warnings

    def backup_watch(self, now) -> list[dict]:
        """A missed backup, a failed off-machine copy or a failed restore test: once a day, from 09:00."""
        day = now.date().isoformat()
        if now.hour < 9 or self._done().get("backups") == day:
            return []
        self._mark("backups", day)
        problems = backups.alerts(self.db)
        if not problems:
            return []
        critical = any(p["level"] == "critical" for p in problems)
        self.db.add_event("critical" if critical else "warn",
                          "backups: " + "; ".join(p["text"] for p in problems), source="api")
        self.sender("Backups need attention", "\n".join(p["text"] for p in problems), "backups",
                    "critical" if critical else "warn")
        return problems

    # ── the clock ────────────────────────────────────────────────────────────
    def tick(self, now=None) -> None:
        now = now or now_ist()
        if time.time() - self._beat_at >= HEARTBEAT_EVERY_SEC:
            self._beat_at = time.time()
            self._guard("heartbeat", lambda: self.beat(now))
        for name, job in (("after-close check", self.after_close), ("weekly summary", self.weekly_summary),
                          ("journal retention", self.journal_retention), ("machine check", self.machine_warnings),
                          ("backup watch", self.backup_watch)):
            self._guard(name, lambda job=job: job(now))

    def _guard(self, name: str, job) -> None:
        try:
            job()
        except Exception as e:  # housekeeping must never take the API down
            self.db.add_event("warn", f"{name} failed: {type(e).__name__}: {str(e)[:160]}", source="api")

    def status(self) -> dict:
        return {"heartbeat": dict(self.heartbeat), "done": self._done(),
                "close_check": self.db.kv_get(close_check.K_LATEST, None),
                "angel": {"configured": close_check.angel_configured(), "enabled": close_check.angel_enabled()},
                "journal_keep_days": retention.keep_days(),
                "backups": backups.status(self.db)}

    async def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="meridian-upkeep")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _loop(self) -> None:
        while True:
            await asyncio.to_thread(self.tick)
            await asyncio.sleep(TICK_SECONDS)
