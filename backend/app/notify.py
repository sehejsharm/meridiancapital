"""Alerts to the operator's phone: entries, exits, kill switches, crashes, automation, holidays.

Everything here was already in the journal; nothing reached the operator unless
the desk was open in front of them. The notifier follows the journal (as the
live feed does), recognises the moments worth interrupting someone for, and
sends each once through whichever channels are configured:

* **Telegram** — ``MERIDIAN_TELEGRAM_BOT_TOKEN`` and ``MERIDIAN_TELEGRAM_CHAT_ID``
  (a bot from @BotFather; push notifications on the phone, free).
* **Webhook** — ``MERIDIAN_ALERT_WEBHOOK``: a JSON POST ``{"title", "text",
  "kind", "level", "ts"}`` for anything else — a WhatsApp or email bridge
  (Make, Zapier, n8n, a Twilio function) takes it from there.

With neither set it does nothing: no message leaves the server unless the
operator configured where it goes. Secrets are read from the environment only
and never logged. A send that fails is logged once and dropped — an alert must
never hold up the desk, let alone an engine.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
import urllib.request
from datetime import datetime, timedelta

from app import webpush
from engine.clock import now_ist

POLL_SECONDS = 5.0
NO_DATA_SEC = 30.0          # a program writes its status every 5 s
NO_DATA_GRACE_SEC = 90.0    # a program just started is still logging in
SENT_KEY = "notify:sent"
CURSOR_KEY = "notify:last_event_id"
TIMEOUT_SEC = 8.0

# (kind, level, pattern) over journal lines. Lines from the desk itself are
# matched separately (crashes, automation) so a program cannot fake them.
RULES: list[tuple[str, str, re.Pattern]] = [
    # An exit by a stop (the program's own or the resting order at the exchange) before any other exit.
    ("stop_hit", "warn", re.compile(r"\bEXIT (STOP|SL|SL@EXCH)\s+(CE|PE)\b")),
    ("entry", "info", re.compile(r"\bENTER BUY\b|\bENTER\b.*\b(CE|PE)\b")),
    ("exit", "info", re.compile(r"\bEXIT (?!ORDER|ORDERS|COULD)\S+\s+(CE|PE)\b")),
    ("kill_daily", "critical", re.compile(r"\bDAILY KILL\b")),
    ("kill_weekly", "critical", re.compile(r"\bWEEKLY KILL\b")),
    ("kill_streak", "warn", re.compile(r"losses in a row — (pausing|sitting out)")),
    ("kill_drawdown", "critical", re.compile(r"\bMAX DRAWDOWN\b.*breached")),
    ("profit_lock", "info", re.compile(r"\bPROFIT LOCK\b")),
    ("exit_failed", "critical", re.compile(r"EXIT COULD NOT BE PLACED|IS STILL OPEN — square it off")),
]
CRASH = re.compile(r"^engine stopped \(pid \d+, exit (-?\d+)\)")
AUTOMATION = re.compile(r"^auto start/stop (ARMED|DISARMED) by")
TITLES = {
    "entry": "Trade entered", "exit": "Trade exited", "kill_daily": "Daily kill switch",
    "kill_weekly": "Weekly kill switch", "kill_streak": "Losing-streak halt",
    "kill_drawdown": "Drawdown halt", "profit_lock": "Profit lock", "exit_failed": "EXIT FAILED", "stop_hit": "Stop hit",
    "no_data": "NO DATA from the program", "data_back": "Data back",
    "crash": "Engine crashed", "crash_loop": "CRASH LOOP", "automation": "Automation",
    "holiday": "NSE holiday ahead", "test": "Test alert",
    # Sent by the desk's housekeeping (app/upkeep.py), not read from the journal.
    "close_check": "After-close check", "weekly": "Week in review", "machine": "Server needs attention",
    "backups": "Backups need attention",
}


def channels() -> dict:
    token = os.environ.get("MERIDIAN_TELEGRAM_BOT_TOKEN", "").strip()
    chat = os.environ.get("MERIDIAN_TELEGRAM_CHAT_ID", "").strip()
    hook = os.environ.get("MERIDIAN_ALERT_WEBHOOK", "").strip()
    return {"telegram": bool(token and chat), "webhook": bool(hook)}


def classify(event: dict) -> tuple[str, str] | None:
    """(kind, level) when a journal line is worth an alert, else None."""
    msg = event.get("message") or ""
    src = event.get("source") or ""
    if src == "supervisor":
        m = CRASH.match(msg)
        if m and int(m.group(1)) != 0 and "stopped on request" not in msg:
            return "crash", "critical"
        return None
    if src == "api":
        if AUTOMATION.match(msg):
            return "automation", "warn"
        return None
    for kind, level, rx in RULES:
        if rx.search(msg):
            return kind, level
    return None


def _post(url: str, payload: dict, headers: dict | None = None) -> None:
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST",
                                 headers={"Content-Type": "application/json", **(headers or {})})
    with urllib.request.urlopen(req, timeout=TIMEOUT_SEC) as resp:  # noqa: S310 (operator-configured URL)
        resp.read(256)


def send(title: str, text: str, kind: str = "info", level: str = "info") -> dict:
    """Send to every configured channel. Returns which went through; never raises."""
    out = {}
    token = os.environ.get("MERIDIAN_TELEGRAM_BOT_TOKEN", "").strip()
    chat = os.environ.get("MERIDIAN_TELEGRAM_CHAT_ID", "").strip()
    icon = {"critical": "🔴", "warn": "🟠"}.get(level, "🟢" if kind in ("entry", "exit", "profit_lock") else "ℹ️")
    if token and chat:
        try:
            _post(f"https://api.telegram.org/bot{token}/sendMessage",
                  {"chat_id": chat, "text": f"{icon} {title}\n{text}"[:4000], "disable_web_page_preview": True})
            out["telegram"] = True
        except Exception as e:
            out["telegram"] = False
            out["telegram_error"] = type(e).__name__
    hook = os.environ.get("MERIDIAN_ALERT_WEBHOOK", "").strip()
    if hook:
        try:
            _post(hook, {"title": title, "text": text, "kind": kind, "level": level,
                         "ts": now_ist().isoformat(timespec="seconds")})
            out["webhook"] = True
        except Exception as e:
            out["webhook"] = False
            out["webhook_error"] = type(e).__name__
    pushed = webpush.send_all(title, text, kind, level)      # phones that asked for notifications; never raises
    if pushed["sent"] or pushed["failed"]:
        out["push"] = pushed["sent"] > 0
    return out


class Notifier:
    """Follows the journal and the run history; sends each alert once."""

    def __init__(self, db, sender=send, fleet=None):
        self.db = db
        self.sender = sender
        self.fleet = fleet
        self._task: asyncio.Task | None = None
        self._silent: dict[str, bool] = {}

    def _sent(self) -> list[str]:
        return list(self.db.kv_get(SENT_KEY, None) or [])

    def _mark(self, key: str) -> bool:
        """Record an alert as sent; False if it already was."""
        sent = self._sent()
        if key in sent:
            return False
        self.db.kv_set(SENT_KEY, (sent + [key])[-500:])
        return True

    def _names(self) -> dict:
        return {a["id"]: a.get("name") or a["id"] for a in self.db.algos()}

    def tick(self) -> int:
        """One pass. Returns alerts sent."""
        if not any(channels().values()) and not webpush.subscriptions(self.db) and self.sender is send:
            # Nothing configured: keep the cursor at the head so turning a channel on
            # later does not replay a backlog of old trades.
            head = max((e["id"] for e in self.db.events(limit=1)), default=0)
            self.db.kv_set(CURSOR_KEY, head)
            return 0
        sent = 0
        cursor = self.db.kv_get(CURSOR_KEY, None)
        if cursor is None:
            cursor = max((e["id"] for e in self.db.events(limit=1)), default=0)
            self.db.kv_set(CURSOR_KEY, cursor)
        events = list(reversed(self.db.events(limit=200, after_id=int(cursor))))
        names = self._names()
        for e in events:
            hit = classify(e)
            if hit:
                kind, level = hit
                algo = names.get(e.get("algo_id"), e.get("algo_id") or "desk")
                if self._mark(f"event:{e['id']}"):
                    self.sender(TITLES.get(kind, kind), f"{algo} · {str(e.get('ts'))[11:19]} IST\n{e['message']}",
                                kind, level)
                    sent += 1
        if events:
            self.db.kv_set(CURSOR_KEY, max(e["id"] for e in events))
        sent += self._crash_loops(names)
        sent += self._holidays()
        sent += self._no_data(names)
        return sent

    def _no_data(self, names: dict, now=None) -> int:
        """A running program that has written no status for NO_DATA_SEC in market hours, and when it is back.

        A program writes its status every five seconds. Silence while it runs means it is
        stuck (a hung Angel call, a frozen feed) and is not managing its trade.
        """
        from shared.db import snapshot_key
        from shared.market_calendar import is_trading_day

        if self.fleet is None:
            return 0
        now = now or now_ist()
        open_now = (is_trading_day(now.date(), {h["day"] for h in self.db.holidays()})
                    and (9, 15) <= (now.hour, now.minute) < (15, 30))
        n = 0
        try:
            sups = self.fleet.all()
        except Exception:
            return 0
        for algo_id, sup in sups.items():
            st = sup.state
            age = None
            if open_now and st.running and st.started_ts and time.time() - st.started_ts > NO_DATA_GRACE_SEC:
                snap = self.db.kv_get(snapshot_key(algo_id), None) or {}
                try:
                    seen = datetime.fromisoformat(str(snap.get("ts"))[:19])
                    age = (now.replace(tzinfo=None) - seen).total_seconds()
                except (TypeError, ValueError):
                    age = float(time.time() - st.started_ts)
            silent = age is not None and age > NO_DATA_SEC
            was = self._silent.get(algo_id, False)
            name = names.get(algo_id, algo_id)
            if silent and not was:
                self.sender(TITLES["no_data"], f"{name}{' (real money)' if st.mode == 'live' else ''}: no status for "
                                               f"{int(age)} s. It may not be managing its trade. Check the Angel One app.",
                            "no_data", "critical" if st.mode == "live" else "warn")
                n += 1
            elif was and not silent and st.running:
                self.sender(TITLES["data_back"], f"{name} is writing its status again.", "data_back", "info")
                n += 1
            self._silent[algo_id] = silent
        return n

    def _crash_loops(self, names: dict) -> int:
        from app import reliability

        n = 0
        for a in reliability.banner_alerts(self.db):
            if self._mark(f"loop:{a['key']}"):
                self.sender(TITLES["crash_loop"],
                            f"{names.get(a['algo_id'], a['algo_id'])} exited abnormally {a['exits']} times "
                            f"({a['first_exit'][11:16]}–{a['last_exit'][11:16]} IST, exit {sorted(set(a['codes']))})"
                            + (" while on real money" if a.get("mode") == "live" else ""),
                            "crash_loop", "critical")
                n += 1
        return n

    def _holidays(self) -> int:
        """The evening before a closure the scheduler will respect (and its morning)."""
        now = now_ist()
        if now.hour < 15:
            return 0
        tomorrow = (now.date() + timedelta(days=1)).isoformat()
        horizon = (now.date() + timedelta(days=4)).isoformat()
        n = 0
        for h in self.db.holidays():
            day = str(h.get("day"))
            if tomorrow <= day <= horizon and self._mark(f"holiday:{day}"):
                self.sender(TITLES["holiday"],
                            f"NSE is closed on {day} ({h.get('label') or 'exchange holiday'}). "
                            f"No algorithm will start that day.", "holiday", "info")
                n += 1
        return n

    async def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="meridian-notifier")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _loop(self) -> None:
        while True:
            try:
                await asyncio.to_thread(self.tick)
            except Exception:  # an alert must never take the API down
                pass
            await asyncio.sleep(POLL_SECONDS)


def test_message(db) -> dict:
    """What the Reliability page's "Send a test alert" does."""
    if not any(channels().values()):
        return {"ok": False, "detail": "no alert channel is configured — set MERIDIAN_TELEGRAM_BOT_TOKEN and "
                                       "MERIDIAN_TELEGRAM_CHAT_ID (or MERIDIAN_ALERT_WEBHOOK) in meridian.env"}
    result = send(TITLES["test"], f"Meridian Capital alerts are working ({time.strftime('%H:%M:%S')}).", "test")
    return {"ok": all(v is True for k, v in result.items() if not k.endswith("_error")), "result": result}
