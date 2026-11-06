"""Alerts reach the operator's phone: entries, exits, kill switches, crashes, automation, holidays.

Each line below is the program's or the desk's own wording.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from app import notify, reliability


@pytest.mark.parametrize("message,source,kind", [
    ("[12:14:03] ENTER BUY CE 24500 (ITM) x1 lot (65 qty, lot 65) @ 150.00", "program", "entry"),
    ("[13:16:40] EXIT TRAIL  CE 24500 x65  150.00->210.96  P&L Rs 3962.250", "program", "exit"),
    ("[14:43:05] EXIT SL@EXCH  CE 22700 x65  202.70->111.45  P&L Rs -5931.250", "program", "exit"),
    ("[11:02:00] DAILY KILL — realised -Rs 12,400 today. Done for the day.", "program", "kill_daily"),
    ("[11:02:00] WEEKLY KILL — realised -Rs 25,100 this week. Done for the week.", "program", "kill_weekly"),
    ("[11:02:00] 4 losses in a row — pausing new entries for the day.", "program", "kill_streak"),
    ("[11:02:00] MAX DRAWDOWN -45.2% breached. Halting all new entries.", "program", "kill_drawdown"),
    ("[11:02:00] PROFIT LOCK — up Rs 20,300 today. Banking it, done for the day.", "program", "profit_lock"),
    ("[15:10:02] EXIT COULD NOT BE PLACED for NIFTY... : timeout. Square it off in the Angel app.", "program", "exit_failed"),
    ("engine stopped (pid 4242, exit 1): process exited — last output: KeyError", "supervisor", "crash"),
    ("auto start/stop DISARMED by sehej", "api", "automation"),
])
def test_the_moments_worth_an_alert_are_recognised(message, source, kind):
    assert notify.classify({"message": message, "source": source})[0] == kind


@pytest.mark.parametrize("message,source", [
    ("[13:16:40] EXIT ORDER FAILED (timeout) — retry 1", "program"),          # a retry, not an exit
    ("engine stopped (pid 4242, exit 0): process exited", "supervisor"),      # a clean stop
    ("engine stopped (pid 4242, exit -2): stopped on request (operator stop); process exited", "supervisor"),
    ("ENTER BUY CE 24500", "api"),                                             # the desk cannot fake a trade
    ("auto start/stop ARMED by sehej", "program"),                             # nor a program the desk's switch
    ("[09:06:41]   daily kill : stop the day at Rs 12,000 realised loss", "program"),  # the startup block
])
def test_ordinary_lines_are_not_alerts(message, source):
    assert notify.classify({"message": message, "source": source}) is None


class Outbox:
    def __init__(self):
        self.sent = []

    def __call__(self, title, text, kind="info", level="info"):
        self.sent.append((title, kind, level, text))
        return {"test": True}


def test_each_alert_goes_out_once(tmp_db, monkeypatch):
    monkeypatch.setattr(reliability, "_now", lambda: datetime(2026, 11, 6, 9, 0))
    reliability.clear_cache()
    tmp_db.upsert_algo("og", "ganesh_kavach_og_real (3)")
    out = Outbox()
    n = notify.Notifier(tmp_db, sender=out)
    n.tick()                                  # takes the cursor at the head: no backlog
    tmp_db.add_event("info", "[12:14:03] ENTER BUY CE 24500 (ITM) x1 lot", source="program", algo_id="og")
    tmp_db.add_event("info", "[12:20:00] waiting for the stop", source="program", algo_id="og")
    tmp_db.add_event("ok", "[13:16:40] EXIT TRAIL  CE 24500 x65  150.00->210.96", source="program", algo_id="og")
    assert n.tick() == 2
    assert [(t, k) for t, k, _, _ in out.sent] == [("Trade entered", "entry"), ("Trade exited", "exit")]
    assert "ganesh_kavach_og_real (3)" in out.sent[0][3]
    assert n.tick() == 0, "nothing twice"


def test_with_no_channel_nothing_is_sent_and_no_backlog_builds(tmp_db, monkeypatch):
    for k in ("MERIDIAN_TELEGRAM_BOT_TOKEN", "MERIDIAN_TELEGRAM_CHAT_ID", "MERIDIAN_ALERT_WEBHOOK"):
        monkeypatch.delenv(k, raising=False)
    tmp_db.add_event("info", "[12:14:03] ENTER BUY CE 24500", source="program", algo_id="og")
    n = notify.Notifier(tmp_db)                # the real sender, unconfigured
    assert n.tick() == 0
    assert tmp_db.kv_get(notify.CURSOR_KEY, None) == max(e["id"] for e in tmp_db.events(limit=1))
    assert notify.test_message(tmp_db)["ok"] is False


def test_a_crash_loop_is_alerted_once(tmp_db, monkeypatch):
    monkeypatch.setattr(reliability, "_now", lambda: datetime(2026, 10, 21, 10, 0))
    reliability.clear_cache()
    for i in range(4):
        rid = tmp_db.start_run(1, "live", "scheduler", algo_id="og")
        with tmp_db.conn() as c:
            c.execute("UPDATE engine_runs SET started_ts=?, stopped_ts=?, exit_code=1, reason='process exited' "
                      "WHERE id=?", (f"2026-10-21T09:0{i}:00", f"2026-10-21T09:0{i}:30", rid))
    out = Outbox()
    n = notify.Notifier(tmp_db, sender=out)
    n.tick()
    n.tick()
    loops = [x for x in out.sent if x[1] == "crash_loop"]
    assert len(loops) == 1 and loops[0][2] == "critical" and "real money" in loops[0][3]


def test_the_holiday_ahead_is_announced_the_evening_before(tmp_db, monkeypatch):
    monkeypatch.setattr(notify, "now_ist", lambda: datetime(2026, 11, 9, 15, 30))
    monkeypatch.setattr(reliability, "_now", lambda: datetime(2026, 11, 9, 15, 30))
    reliability.clear_cache()
    tmp_db.add_holiday("2026-11-10", "Guru Nanak Jayanti")
    out = Outbox()
    n = notify.Notifier(tmp_db, sender=out)
    n.tick()
    n.tick()
    assert [x[1] for x in out.sent] == ["holiday"]
    assert "2026-11-10" in out.sent[0][3]


def test_secrets_never_leave_in_a_report(monkeypatch):
    monkeypatch.setenv("MERIDIAN_TELEGRAM_BOT_TOKEN", "123:secret")
    monkeypatch.setenv("MERIDIAN_TELEGRAM_CHAT_ID", "42")
    assert notify.channels() == {"telegram": True, "webhook": False}
