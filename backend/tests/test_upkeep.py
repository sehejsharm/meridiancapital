"""The desk's housekeeping: outside heartbeat, after-close check, weekly summary,
journal retention and machine warnings.

Money figures below are worked by hand in the comments beside them.
"""

from __future__ import annotations

from datetime import date, datetime

import pytest

from app import angel_after_close, close_check, health, retention, upkeep, weekly
from shared.db import snapshot_key
from tests.test_desk import auth, client  # noqa: F401 - the API fixtures

TUE = "2026-11-10"          # a trading Tuesday


def trade(db, day=TUE, *, tsym="NIFTY17NOV2625500CE", qty=65, entry=150.0, exit_=180.0, mode="live",
          gross=None, charges=70.0, closed=True, algo="og", entry_time="11:02:00", exit_time="13:40:00"):
    gross = (exit_ - entry) * qty if gross is None else gross
    db.add_trade({
        "entry_ts": f"{day}T{entry_time}", "exit_ts": f"{day}T{exit_time}" if closed else None,
        "session_date": day, "mode": mode, "side": tsym[-2:], "strike": 25500, "expiry": "2026-11-17",
        "tsym": tsym, "lots": 1, "qty": qty, "entry_prem": entry, "exit_prem": exit_ if closed else None,
        "gross": gross if closed else None, "charges": charges if closed else None,
        "net": gross - charges if closed else None, "reason": "TRAIL", "algo_id": algo,
    })


class Outbox:
    def __init__(self, went=True):
        self.sent, self.went = [], went

    def __call__(self, title, text, kind="info", level="info"):
        self.sent.append({"title": title, "text": text, "kind": kind, "level": level})
        return {"telegram": True} if self.went else {}


def fills(tsym="NIFTY17NOV2625500CE", qty=65, buy=150.0, sell=180.0):
    return [
        {"tsym": tsym, "side": "BUY", "qty": qty, "price": buy, "time": "11:02:01", "order_id": "A1", "exchange": "NFO"},
        {"tsym": tsym, "side": "SELL", "qty": qty, "price": sell, "time": "13:40:02", "order_id": "A2", "exchange": "NFO"},
    ]


# ── outside heartbeat ────────────────────────────────────────────────────────

def test_no_heartbeat_leaves_the_server_unless_an_address_is_set(tmp_db, monkeypatch):
    monkeypatch.delenv("MERIDIAN_HEARTBEAT_URL", raising=False)
    calls = []
    u = upkeep.Upkeep(tmp_db, fetch=calls.append)
    assert u.beat() is None
    assert calls == [] and u.status()["heartbeat"]["configured"] is False


def test_the_heartbeat_is_sent_and_a_failure_is_journalled_once(tmp_db, monkeypatch):
    monkeypatch.setenv("MERIDIAN_HEARTBEAT_URL", "https://hc.example/ping/abc-secret")
    state = {"fail": False, "calls": 0}

    def fetch(url):
        state["calls"] += 1
        if state["fail"]:
            raise TimeoutError("timed out")

    u = upkeep.Upkeep(tmp_db, fetch=fetch)
    assert u.beat(datetime(2026, 11, 10, 10, 0)) is True
    assert u.heartbeat["last_ok"] == "2026-11-10T10:00:00" and u.heartbeat["sent"] == 1
    state["fail"] = True
    assert u.beat() is False and u.beat() is False
    lines = [e["message"] for e in tmp_db.events(limit=20)]
    assert sum("heartbeat could not be sent" in m for m in lines) == 1      # once, not every minute
    assert not any("abc-secret" in m or "hc.example" in m for m in lines)   # the address is the monitor's secret
    state["fail"] = False
    assert u.beat() is True
    assert any("being sent again" in e["message"] for e in tmp_db.events(limit=20))


def test_a_heartbeat_address_that_is_not_https_is_refused():
    with pytest.raises(ValueError):
        upkeep._fetch("http://hc.example/ping/abc")
    with pytest.raises(ValueError):
        upkeep._fetch("file:///etc/passwd")


# ── journal retention ────────────────────────────────────────────────────────

def _event_at(db, ts, message):
    eid = db.add_event("info", message, source="program", algo_id="og")
    with db.conn() as c:
        c.execute("UPDATE events SET ts = ? WHERE id = ?", (ts, eid))
    return eid


def test_old_journal_lines_are_archived_to_a_file_and_only_then_removed(tmp_db, tmp_path):
    _event_at(tmp_db, "2025-09-30T10:00:00", "old september line")
    _event_at(tmp_db, "2025-10-02T10:00:00", "old october line")
    _event_at(tmp_db, "2025-11-12T09:00:00", "inside the year: kept")   # 364 days before 11 Nov 2026
    _event_at(tmp_db, "2026-11-10T10:00:00", "yesterday: kept")
    trade(tmp_db, "2025-09-30")
    out = retention.run(tmp_db, date(2026, 11, 11), days=365, folder=tmp_path / "archive")
    assert out["archived"] == 2 and out["cutoff"] == "2025-11-11"
    assert out["files"] == ["journal-2025-09.jsonl.gz", "journal-2025-10.jsonl.gz"]
    kept = [e["message"] for e in tmp_db.events(limit=10)]
    assert kept == ["yesterday: kept", "inside the year: kept"]
    sept = retention.read_archive(tmp_path / "archive" / "journal-2025-09.jsonl.gz")
    assert [r["message"] for r in sept] == ["old september line"] and sept[0]["algo_id"] == "og"
    assert len(tmp_db.trades(limit=10)) == 1                      # trades are never touched
    again = retention.run(tmp_db, date(2026, 11, 11), days=365, folder=tmp_path / "archive")
    assert again["archived"] == 0


def test_a_second_run_appends_to_the_same_month_file(tmp_db, tmp_path):
    _event_at(tmp_db, "2025-09-01T10:00:00", "first")
    retention.run(tmp_db, date(2026, 11, 11), folder=tmp_path)
    _event_at(tmp_db, "2025-09-02T10:00:00", "second")
    retention.run(tmp_db, date(2026, 11, 11), folder=tmp_path)
    assert [r["message"] for r in retention.read_archive(tmp_path / "journal-2025-09.jsonl.gz")] == ["first", "second"]


def test_a_mistyped_retention_setting_cannot_empty_the_journal(tmp_db, tmp_path, monkeypatch):
    monkeypatch.setenv("MERIDIAN_JOURNAL_KEEP_DAYS", "1")
    assert retention.keep_days() == retention.MIN_KEEP_DAYS == 90
    _event_at(tmp_db, "2026-10-01T10:00:00", "six weeks old")
    assert retention.run(tmp_db, date(2026, 11, 11), days=1, folder=tmp_path)["archived"] == 0
    monkeypatch.setenv("MERIDIAN_JOURNAL_KEEP_DAYS", "not a number")
    assert retention.keep_days() == 365


def test_nothing_is_removed_if_the_archive_cannot_be_written(tmp_db, tmp_path):
    _event_at(tmp_db, "2025-01-01T10:00:00", "old")
    blocker = tmp_path / "archive"
    blocker.write_text("a file where the folder should be")
    with pytest.raises(OSError):
        retention.run(tmp_db, date(2026, 11, 11), folder=blocker)
    assert [e["message"] for e in tmp_db.events(limit=5)] == ["old"]


# ── weekly summary ───────────────────────────────────────────────────────────

def test_the_week_ends_on_its_last_trading_day():
    assert weekly.week_bounds(date(2026, 11, 11)) == (date(2026, 11, 9), date(2026, 11, 13))
    assert weekly.last_trading_day_of_week(date(2026, 11, 10), set()) == date(2026, 11, 13)
    # Friday a holiday: the summary goes out on Thursday.
    assert weekly.last_trading_day_of_week(date(2026, 11, 10), {"2026-11-13"}) == date(2026, 11, 12)
    assert weekly.due(datetime(2026, 11, 13, 15, 45), set()) is True
    assert weekly.due(datetime(2026, 11, 13, 15, 44), set()) is False
    assert weekly.due(datetime(2026, 11, 12, 16, 0), set()) is False
    assert weekly.due(datetime(2026, 11, 12, 16, 0), {"2026-11-13"}) is True


def test_the_weeks_figures_are_the_real_money_trades_after_charges(tmp_db):
    tmp_db.upsert_algo("og", "ganesh kavach")
    # Mon: 150 -> 180 on 65 = +1,950 gross, 70 charges = +1,880 net
    trade(tmp_db, "2026-11-09", entry=150.0, exit_=180.0, charges=70.0)
    # Wed: 200 -> 160 on 65 = -2,600 gross, 68 charges = -2,668 net
    trade(tmp_db, "2026-11-11", entry=200.0, exit_=160.0, charges=68.0)
    # Fri: 100 -> 130 on 65 = +1,950 gross, 72 charges = +1,878 net
    trade(tmp_db, "2026-11-13", entry=100.0, exit_=130.0, charges=72.0)
    trade(tmp_db, "2026-11-12", mode="paper", entry=100.0, exit_=400.0)     # paper: not in the figures
    trade(tmp_db, "2026-11-06", entry=100.0, exit_=500.0)                    # last week
    s = weekly.summary(tmp_db, date(2026, 11, 13))
    assert (s["from"], s["to"], s["week"]) == ("2026-11-09", "2026-11-13", "2026-W46")
    assert (s["trades"], s["wins"], s["losses"], s["paper_trades"]) == (3, 2, 1, 1)
    assert s["net"] == 1090.0          # 1,880 - 2,668 + 1,878
    assert s["charges"] == 210.0       # 70 + 68 + 72
    assert s["gross"] == 1300.0        # 1,950 - 2,600 + 1,950
    assert (s["best"], s["worst"]) == (1880.0, -2668.0)
    assert s["win_rate"] == 0.6667
    text = weekly.text(s, "https://desk.example/")
    assert "Week of 9–13 Nov 2026 · ganesh kavach" in text
    assert "Net +₹1,090.00 after charges of ₹210.00" in text
    assert "3 real-money trades: 2 won, 1 lost (67% win rate)" in text
    assert "Best +₹1,880.00 · worst −₹2,668.00" in text
    assert "Paper: 1 trade (not in the figures above)" in text
    assert text.endswith("Tear sheet: https://desk.example/reports")


def test_a_week_without_a_real_money_trade_says_so(tmp_db):
    s = weekly.summary(tmp_db, date(2026, 11, 13))
    assert s["trades"] == 0 and s["win_rate"] is None
    assert "No real-money trade closed this week." in weekly.text(s)


def test_the_weekly_summary_goes_out_once(tmp_db):
    trade(tmp_db, "2026-11-12")
    out = Outbox()
    u = upkeep.Upkeep(tmp_db, sender=out, dashboard="https://desk.example")
    assert u.weekly_summary(datetime(2026, 11, 12, 16, 0)) is None          # Thursday, Friday still to come
    assert u.weekly_summary(datetime(2026, 11, 13, 15, 50))["trades"] == 1
    assert u.weekly_summary(datetime(2026, 11, 13, 16, 30)) is None          # already sent this week
    assert [m["kind"] for m in out.sent] == ["weekly"]


def test_a_summary_with_nowhere_to_go_is_not_called_sent(tmp_db):
    u = upkeep.Upkeep(tmp_db, sender=Outbox(went=False))
    u.weekly_summary(datetime(2026, 11, 13, 15, 50))
    assert any("no alert channel is set up" in e["message"] for e in tmp_db.events(limit=5))


# ── after-close check ────────────────────────────────────────────────────────

def test_a_flat_day_whose_fills_match_angels_is_clean(tmp_db, monkeypatch):
    for k in ("ANGEL_API_KEY", "ANGEL_CLIENT_ID", "ANGEL_PASSWORD", "ANGEL_TOTP_SECRET"):
        monkeypatch.setenv(k, "set")
    trade(tmp_db)
    r = close_check.run(tmp_db, TUE, angel=lambda db, day: {"ok": True, "fills": fills(), "positions": [
        {"tsym": "NIFTY17NOV2625500CE", "net_qty": 0}]})
    assert r["status"] == "ok" and r["independent"] is True and r["findings"] == []
    assert close_check.headline(r) == ("after-close check: flat at Angel, 1 real-money trade on record "
                                       "agree with Angel's trade book")
    assert tmp_db.kv_get(close_check.K_LATEST)["day"] == TUE
    assert [f["order_id"] for f in tmp_db.kv_get(f"angel:fills:{TUE}")] == ["A1", "A2"]


def test_a_position_still_open_at_angel_is_critical():
    f = close_check.compare([{"tsym": "NIFTY17NOV2625500CE", "qty": 65, "entry_prem": 150.0, "exit_ts": None}],
                            fills()[:1], [{"tsym": "NIFTY17NOV2625500CE", "net_qty": 65}])
    assert f[0] == {"level": "critical", "kind": "position_open",
                    "text": "Angel shows NIFTY17NOV2625500CE still open: +65 qty"}


def test_a_hand_placed_position_is_a_warning_not_the_desks_trade():
    f = close_check.compare([], [], [{"tsym": "BANKNIFTY25NOV2658000PE", "net_qty": -30}])
    assert f[0]["level"] == "warn" and "placed by hand?" in f[0]["text"]


def test_quantity_price_and_missing_fills_are_each_reported():
    t = [{"tsym": "NIFTY17NOV2625500CE", "qty": 65, "entry_prem": 150.0, "exit_prem": 180.0, "exit_ts": "x"}]
    # Angel sold only 50 of the 65.
    assert close_check.compare(t, fills(qty=65)[:1] + [{**fills()[1], "qty": 50}], [])[0]["kind"] == "quantity"
    # Angel's average sale was 179.20: 0.80 x 65 = Rs 52 less than recorded.
    price = close_check.compare(t, fills(sell=179.20), [])
    assert price == [{"level": "warn", "kind": "price",
                      "text": "NIFTY17NOV2625500CE: recorded exit 180.00, Angel's average fill 179.20 "
                              "(-52.00 on 65 qty)"}]
    assert close_check.compare(t, fills(sell=180.05), []) == []                 # within a tick
    assert close_check.compare(t, [], [])[0]["kind"] == "no_fill"
    extra = close_check.compare(t, fills() + fills(tsym="NIFTY17NOV2625400PE"), [])
    assert [x["kind"] for x in extra] == ["unrecorded_fill"]


def test_two_fills_of_one_order_are_averaged_by_quantity():
    t = [{"tsym": "NIFTY17NOV2625500CE", "qty": 130, "entry_prem": 151.0, "exit_prem": 180.0, "exit_ts": "x"}]
    split = [{"tsym": "NIFTY17NOV2625500CE", "side": "BUY", "qty": 65, "price": 150.0, "order_id": "A1"},
             {"tsym": "NIFTY17NOV2625500CE", "side": "BUY", "qty": 65, "price": 152.0, "order_id": "A1"},
             {"tsym": "nifty17nov2625500ce", "side": "SELL", "qty": 130, "price": 180.0, "order_id": "A2"}]
    assert close_check.compare(t, split, []) == []          # (150 + 152) / 2 = 151.00


def test_the_desks_own_records_are_checked_even_without_angel(tmp_db, monkeypatch):
    for k in ("ANGEL_API_KEY", "ANGEL_CLIENT_ID", "ANGEL_PASSWORD", "ANGEL_TOTP_SECRET"):
        monkeypatch.delenv(k, raising=False)
    tmp_db.upsert_algo("og", "ganesh kavach")
    trade(tmp_db, closed=False)
    tmp_db.kv_set(snapshot_key("og"), {"ts": f"{TUE}T15:20:00", "engine": {"mode": "live"},
                                       "position": {"tsym": "NIFTY17NOV2625500CE", "qty": 65}})
    _event_at(tmp_db, f"{TUE}T15:10:02", "[15:10:02] EXIT COULD NOT BE PLACED for NIFTY17NOV2625500CE: timeout")
    asked = []
    r = close_check.run(tmp_db, TUE, angel=lambda *a: asked.append(a) or {"ok": True})
    assert asked == [] and r["angel"].startswith("not asked: the Angel credentials")
    assert r["status"] == "problem" and r["independent"] is False
    assert sorted(f["kind"] for f in r["findings"]) == ["exit_failed", "open_trade", "status_position"]


def test_angel_is_never_asked_while_a_program_is_running(tmp_db, monkeypatch):
    for k in ("ANGEL_API_KEY", "ANGEL_CLIENT_ID", "ANGEL_PASSWORD", "ANGEL_TOTP_SECRET"):
        monkeypatch.setenv(k, "set")
    asked = []
    r = close_check.run(tmp_db, TUE, running=True, angel=lambda *a: asked.append(a) or {"ok": True})
    assert asked == [] and "a second login would log it out" in r["angel"]
    monkeypatch.setenv("MERIDIAN_AFTER_CLOSE_ANGEL", "0")
    r = close_check.run(tmp_db, TUE, angel=lambda *a: asked.append(a) or {"ok": True})
    assert asked == [] and r["angel"].startswith("off")


def test_angel_not_answering_is_reported_not_passed_as_clean(tmp_db, monkeypatch):
    for k in ("ANGEL_API_KEY", "ANGEL_CLIENT_ID", "ANGEL_PASSWORD", "ANGEL_TOTP_SECRET"):
        monkeypatch.setenv(k, "set")
    r = close_check.run(tmp_db, TUE, angel=lambda db, day: {"ok": False, "reason": "Angel login failed: Invalid totp"})
    assert r["status"] == "check" and r["independent"] is False
    assert r["findings"][0]["kind"] == "angel_unread" and "Invalid totp" in r["findings"][0]["text"]


def test_the_check_runs_once_from_1535_on_a_trading_day(tmp_db, monkeypatch):
    for k in ("ANGEL_API_KEY", "ANGEL_CLIENT_ID", "ANGEL_PASSWORD", "ANGEL_TOTP_SECRET"):
        monkeypatch.setenv(k, "set")
    trade(tmp_db)
    out = Outbox()
    u = upkeep.Upkeep(tmp_db, sender=out, angel=lambda db, day: {"ok": True, "fills": fills(), "positions": []})
    assert u.after_close(datetime(2026, 11, 10, 15, 34)) is None
    assert u.after_close(datetime(2026, 11, 8, 16, 0)) is None            # a Sunday
    r = u.after_close(datetime(2026, 11, 10, 15, 35))
    assert r["status"] == "ok"
    assert u.after_close(datetime(2026, 11, 10, 15, 50)) is None           # once
    assert [(m["title"], m["level"]) for m in out.sent] == [("After-close check", "info")]
    assert any(e["level"] == "ok" and "flat at Angel" in e["message"] for e in tmp_db.events(limit=5))


def test_a_quiet_day_sends_nothing_and_a_problem_always_does(tmp_db, monkeypatch):
    for k in ("ANGEL_API_KEY", "ANGEL_CLIENT_ID", "ANGEL_PASSWORD", "ANGEL_TOTP_SECRET"):
        monkeypatch.setenv(k, "set")
    out = Outbox()
    u = upkeep.Upkeep(tmp_db, sender=out, angel=lambda db, day: {"ok": True, "fills": [], "positions": []})
    assert u.after_close(datetime(2026, 11, 10, 15, 40))["status"] == "ok"
    assert out.sent == []                                                  # no trade, nothing wrong: no message
    u2 = upkeep.Upkeep(tmp_db, sender=out, angel=lambda db, day: {
        "ok": True, "fills": [], "positions": [{"tsym": "NIFTY17NOV2625500CE", "net_qty": 65}]})
    r = u2.after_close(datetime(2026, 11, 11, 15, 40))
    assert r["status"] == "check"
    assert out.sent[-1]["title"] == "AFTER-CLOSE CHECK: ACTION NEEDED"


def test_the_check_waits_for_a_running_program_then_checks_the_records_anyway(tmp_db):
    class Sup:
        class state:
            running = True

    class Fleet:
        def refresh(self): pass
        def all(self): return {"og": Sup}

    u = upkeep.Upkeep(tmp_db, fleet=Fleet(), sender=Outbox(), angel=lambda *a: {"ok": True})
    assert u.after_close(datetime(2026, 11, 10, 15, 40)) is None
    r = u.after_close(datetime(2026, 11, 10, 16, 30))
    assert r is not None and r["independent"] is False


# ── Angel's rows, as the read-only process shapes them ──────────────────────

def test_angels_trade_book_and_position_rows_are_read():
    rows = [{"tradingsymbol": "NIFTY17NOV2625500CE", "transactiontype": "BUY", "fillprice": "150.05",
             "fillsize": "65", "orderid": "251110000123", "fillid": "77", "filltime": "11:02:01",
             "exchange": "NFO", "producttype": "CARRYFORWARD"},
            {"tradingsymbol": "X", "transactiontype": "SELL", "fillprice": "", "fillsize": "65"}]
    assert angel_after_close.fills_from(rows) == [{
        "tsym": "NIFTY17NOV2625500CE", "side": "BUY", "qty": 65, "price": 150.05, "time": "11:02:01",
        "order_id": "251110000123", "fill_id": "77", "exchange": "NFO", "product": "CARRYFORWARD"}]
    pos = angel_after_close.positions_from([{"tradingsymbol": "NIFTY17NOV2625500CE", "symboltoken": "4321",
                                             "netqty": "0", "buyqty": "65", "sellqty": "65",
                                             "totalbuyavgprice": "150.05", "totalsellavgprice": "180.00"}])
    assert pos[0]["net_qty"] == 0 and pos[0]["buy_avg"] == 150.05 and pos[0]["token"] == "4321"


def test_the_contracts_kept_for_research_cover_the_days_range_in_two_expiries():
    # Index between 25,512 and 25,688: strikes 25,500..25,700, two more each side.
    assert angel_after_close.strikes_for(25512.4, 25688.0) == list(range(25400, 25850, 50))
    table = {}
    for exp in (date(2026, 11, 3), date(2026, 11, 10), date(2026, 11, 17), date(2026, 11, 24)):
        for k in range(25000, 26050, 50):
            for right in ("CE", "PE"):
                table[(exp, k, right)] = (f"NIFTY{exp:%d%b%y}".upper() + f"{k}{right}", f"t{exp.day}{k}{right}", 65)
    picked = angel_after_close.pick_contracts(table, date(2026, 11, 10), 25512.4, 25688.0)
    assert sorted({c["expiry"] for c in picked}) == ["2026-11-10", "2026-11-17"]    # expiry day itself, and next week
    assert {c["dte"] for c in picked} == {0, 7}
    assert len(picked) == 9 * 2 * 2
    assert angel_after_close.bars_from([["2026-11-10T09:15:00+05:30", 150, 152.5, 149, 151.2, 1300], ["bad"]]) == [
        ["09:15", 150.0, 152.5, 149.0, 151.2, 1300]]


# ── the machine ──────────────────────────────────────────────────────────────

def test_the_machine_report_has_processor_memory_and_disk(tmp_db):
    h = health.host(tmp_db)
    assert h["cpu"]["cores"] >= 1 and h["disk"]["total_gb"] > 0
    assert 0 <= h["disk"]["free_pct"] <= 100
    assert h["thresholds"]["disk_free_warn_pct"] == 20.0
    assert h["storage"]["database_mb"] is not None


def test_disk_below_a_fifth_free_is_a_warning(tmp_db, monkeypatch):
    class Usage:
        total, free = 100 * 1024 ** 3, 15 * 1024 ** 3

    monkeypatch.setattr(health.shutil, "disk_usage", lambda p: Usage)
    monkeypatch.setattr(health, "_load", lambda: [0.1, 0.1, 0.1])
    monkeypatch.setattr(health, "_meminfo", lambda: {"total_mb": 1000.0, "used_mb": 300.0, "used_fraction": 0.3})
    w = health.host(tmp_db)["warnings"]
    assert w == [{"kind": "disk", "level": "warning", "text": "15% of the disk is free (15.0 GB) — below 20%"}]
    Usage.free = 5 * 1024 ** 3
    assert health.host(tmp_db)["warnings"][0]["level"] == "critical"
    Usage.free = 40 * 1024 ** 3
    assert health.host(tmp_db)["warnings"] == []


def test_a_machine_warning_is_sent_once_a_day(tmp_db, monkeypatch):
    monkeypatch.setattr(health, "host", lambda db: {"warnings": [
        {"kind": "disk", "level": "warning", "text": "15% of the disk is free (15.0 GB) — below 20%"}]})
    out = Outbox()
    u = upkeep.Upkeep(tmp_db, sender=out)
    assert len(u.machine_warnings(datetime(2026, 11, 10, 9, 0))) == 1
    u._machine_at = 0.0
    assert u.machine_warnings(datetime(2026, 11, 10, 14, 0)) == []
    assert [m["title"] for m in out.sent] == ["Server needs attention"]


def test_one_job_failing_does_not_stop_the_others(tmp_db, monkeypatch):
    monkeypatch.setattr(upkeep.weekly, "due", lambda *a: (_ for _ in ()).throw(RuntimeError("boom")))
    u = upkeep.Upkeep(tmp_db, sender=Outbox())
    u.tick(datetime(2026, 11, 10, 16, 5))
    assert any("weekly summary failed: RuntimeError: boom" in e["message"] for e in tmp_db.events(limit=10))
    assert u._done().get("retention") == "2026-11-10"                    # the job after it still ran


# ── through the API ──────────────────────────────────────────────────────────

def test_host_and_close_check_endpoints(client, auth, monkeypatch):  # noqa: F811
    from app.deps import ctx

    for k in ("ANGEL_API_KEY", "ANGEL_CLIENT_ID", "ANGEL_PASSWORD", "ANGEL_TOTP_SECRET", "MERIDIAN_HEARTBEAT_URL"):
        monkeypatch.delenv(k, raising=False)
    assert client.get("/api/reliability/host").status_code == 401
    body = client.get("/api/reliability/host", headers=auth).json()
    assert body["machine"]["cpu"]["cores"] >= 1
    assert body["upkeep"]["heartbeat"]["configured"] is False
    assert body["upkeep"]["angel"] == {"configured": False, "enabled": True}
    assert body["upkeep"]["journal_keep_days"] == 365
    assert client.get("/api/reliability/close-check", headers=auth).json() == {"check": None}

    asked = []
    ctx().upkeep.angel = lambda db, day: asked.append(day) or {"ok": True, "fills": [], "positions": []}
    ran = client.post("/api/reliability/close-check/run", headers=auth).json()["check"]
    assert ran["status"] == "ok" and asked == []                     # no credentials on the server: Angel not asked
    assert ran["angel"].startswith("not asked")
    assert client.get("/api/reliability/close-check", headers=auth).json()["check"]["day"] == ran["day"]
    assert client.get("/api/reliability/close-check?day=2026-13-99x", headers=auth).status_code == 422
    assert any(a["action"] == "close_check.run" for a in ctx().db.audit_tail(5))


def test_no_credential_or_monitor_address_is_ever_in_the_reply(client, auth, monkeypatch):  # noqa: F811
    monkeypatch.setenv("ANGEL_API_KEY", "KEY-VALUE-123")
    monkeypatch.setenv("ANGEL_CLIENT_ID", "A123456")
    monkeypatch.setenv("ANGEL_PASSWORD", "4321")
    monkeypatch.setenv("ANGEL_TOTP_SECRET", "JBSWY3DPEHPK3PXP")
    monkeypatch.setenv("MERIDIAN_HEARTBEAT_URL", "https://hc.example/ping/very-secret-id")
    raw = client.get("/api/reliability/host", headers=auth).text
    for secret in ("KEY-VALUE-123", "A123456", "JBSWY3DPEHPK3PXP", "very-secret-id", "hc.example"):
        assert secret not in raw
    assert client.get("/api/reliability/host", headers=auth).json()["upkeep"]["angel"]["configured"] is True
