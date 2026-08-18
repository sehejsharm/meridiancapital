"""Wall-clock control of the trading session.

09:15 IST the bot comes up, 15:45 IST it goes down — every weekday that is
not an NSE holiday, without anything being connected to it. The algorithm
still enforces its own internal boundaries (no entries after 13:30, flat at
15:00, EOD report at 15:30); this is the outer envelope around that.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from .config import settings
from .holidays import is_trading_day, why_not_trading
from .runner import fleet, supervisor

log = logging.getLogger("meridian.scheduler")

_scheduler: Optional[BackgroundScheduler] = None


def _tz() -> ZoneInfo:
    try:
        return ZoneInfo(settings.tz)
    except Exception:
        return ZoneInfo("Asia/Kolkata")


def _session_start_job() -> None:
    today = date.today()
    if not is_trading_day(today, settings.skip_holidays):
        supervisor._emit_local(
            "scheduler",
            f"Session skipped — {why_not_trading(today, settings.skip_holidays)}",
            level="info",
        )
        return
    supervisor._emit_local(
        "scheduler",
        f"Scheduled start — {settings.session_start.strftime('%H:%M')} {settings.tz}",
        level="success",
    )
    # Every slot holding an algorithm starts. An empty slot refuses on its own
    # and says so, so there is nothing to filter for here.
    for lane in fleet:
        lane.restarts = 0
        lane.start(trigger="schedule")


def _session_stop_job() -> None:
    live = [lane for lane in fleet if lane.running]
    if not live:
        return
    supervisor._emit_local(
        "scheduler",
        f"Scheduled stop — {settings.session_stop.strftime('%H:%M')} {settings.tz}",
        level="warn",
    )
    for lane in live:
        lane.stop(reason="scheduled stop")


def start_scheduler() -> BackgroundScheduler:
    global _scheduler
    if _scheduler is not None:
        return _scheduler

    tz = _tz()
    _scheduler = BackgroundScheduler(timezone=tz, daemon=True)

    _scheduler.add_job(
        _session_start_job, id="session_start", replace_existing=True,
        trigger=CronTrigger(day_of_week="mon-fri",
                            hour=settings.session_start.hour,
                            minute=settings.session_start.minute,
                            timezone=tz),
        misfire_grace_time=300, coalesce=True, max_instances=1,
    )
    _scheduler.add_job(
        _session_stop_job, id="session_stop", replace_existing=True,
        trigger=CronTrigger(day_of_week="mon-fri",
                            hour=settings.session_stop.hour,
                            minute=settings.session_stop.minute,
                            timezone=tz),
        misfire_grace_time=600, coalesce=True, max_instances=1,
    )
    _scheduler.start()
    log.info("Scheduler armed: start %s, stop %s (%s)",
             settings.session_start, settings.session_stop, settings.tz)

    if settings.auto_schedule:
        _catch_up()
    return _scheduler


def _catch_up() -> None:
    """A restart at 11:00 should not mean no bot until tomorrow."""
    now = datetime.now()
    if not is_trading_day(now.date(), settings.skip_holidays):
        return
    if settings.session_start <= now.time() < settings.session_stop:
        supervisor._emit_local(
            "scheduler",
            "Server came up inside the session window — starting bots now",
            level="warn",
        )
        for lane in fleet:
            lane.start(trigger="schedule")


def shutdown_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None


def next_runs() -> dict:
    """What the phone shows as 'next start / next stop'."""
    if _scheduler is None:
        return {"enabled": False, "next_start": None, "next_stop": None}
    out = {"enabled": settings.auto_schedule}
    for job_id, key in (("session_start", "next_start"), ("session_stop", "next_stop")):
        job = _scheduler.get_job(job_id)
        nxt = getattr(job, "next_run_time", None) if job else None
        # APScheduler does not know about NSE holidays; walk forward to the
        # first date the session would actually run.
        while nxt is not None and not is_trading_day(nxt.date(), settings.skip_holidays):
            nxt = nxt + timedelta(days=1)
        out[key] = nxt.isoformat(timespec="seconds") if nxt else None
    return out


def set_schedule_enabled(enabled: bool) -> dict:
    """Pause or resume the automatic session without touching a running bot."""
    settings.auto_schedule = enabled
    if _scheduler is None:
        return {"enabled": enabled}
    for job_id in ("session_start", "session_stop"):
        job = _scheduler.get_job(job_id)
        if job is None:
            continue
        if enabled:
            job.resume()
        else:
            job.pause()
    supervisor._emit_local(
        "scheduler",
        f"Automatic schedule {'enabled' if enabled else 'paused'}",
        level="warn" if not enabled else "success",
    )
    return {"enabled": enabled}


def reschedule(start_hhmm: str, stop_hhmm: str) -> dict:
    """Move the session boundaries from the phone."""
    def _parse(raw: str):
        hh, _, mm = raw.strip().partition(":")
        h, m = int(hh), int(mm or 0)
        if not (0 <= h <= 23 and 0 <= m <= 59):
            raise ValueError(f"invalid time: {raw}")
        return h, m

    sh, sm = _parse(start_hhmm)
    eh, em = _parse(stop_hhmm)

    from datetime import time as dtime
    settings.session_start = dtime(sh, sm)
    settings.session_stop = dtime(eh, em)

    if _scheduler is not None:
        tz = _tz()
        _scheduler.reschedule_job(
            "session_start",
            trigger=CronTrigger(day_of_week="mon-fri", hour=sh, minute=sm, timezone=tz))
        _scheduler.reschedule_job(
            "session_stop",
            trigger=CronTrigger(day_of_week="mon-fri", hour=eh, minute=em, timezone=tz))

    supervisor._emit_local(
        "scheduler", f"Schedule updated — {start_hhmm} to {stop_hhmm} {settings.tz}",
        level="success",
    )
    return next_runs()
