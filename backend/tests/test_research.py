"""The research recorder: ticks from the program's own status, execution quality,
the exit rule sets on recorded trades, and the trade page's payload."""

from __future__ import annotations

import gzip
import json

import pytest

from app import exit_rules, research
from tests.test_desk import auth, client  # noqa: F401  (fixtures)

DAY = "2026-11-11"
TSYM = "NIFTY17NOV2625500CE"


def snap(ts, premium, peak=0.0, stop=82.5, opened=f"{DAY}T10:30:00", spot=25510.0):
    return {"ts": ts, "signal": {"spot": spot}, "position": {
        "tsym": TSYM, "side": "CE", "strike": 25500, "expiry": "2026-11-17", "opened_ts": opened,
        "entry_premium": 150.0, "live_premium": premium, "peak_pct": peak, "stop_price": stop, "qty": 65}}


def add_trade(db, *, exit_prem=158.0, exit_ts=f"{DAY}T10:34:20", mode="live", reason="TRAIL"):
    return db.add_trade({
        "entry_ts": f"{DAY}T10:30:00", "exit_ts": exit_ts, "session_date": DAY, "mode": mode, "side": "CE",
        "strike": 25500, "expiry": "2026-11-17", "tsym": TSYM, "lots": 1, "qty": 65, "entry_prem": 150.0,
        "exit_prem": exit_prem, "gross": (exit_prem - 150.0) * 65, "charges": 70.0,
        "net": (exit_prem - 150.0) * 65 - 70.0, "reason": reason, "algo_id": "og"})


def record_path(db):
    rec = research.TickRecorder(db)
    # Bought at 150. Up to 190 by 10:33 (+26.7%, so the stop trails to 160), then 159 at 10:34:15:
    # through the stop, and the program sells (filled at 158.00 at 10:34:20).
    feed = [("10:30:05", 150.5, 0.3, 82.5), ("10:30:10", 151.0, 0.7, 82.5), ("10:31:02", 158.0, 5.3, 82.5),
            ("10:32:30", 181.0, 20.7, 151.0), ("10:33:10", 190.0, 26.7, 160.0), ("10:34:15", 159.0, 26.7, 160.0)]
    t = 1000.0
    for hms, prem, peak, stop in feed:
        t += 40.0
        assert rec.record("og", snap(f"{DAY}T{hms}", prem, peak, stop), "live", t)
    return rec


def keep_candles(db, bars, traded=True):
    folder = research.folder(db)
    folder.mkdir(parents=True, exist_ok=True)
    with gzip.open(folder / f"candles-{DAY}.json.gz", "wt") as fh:
        json.dump({"day": DAY, "index": [["10:30", 25500, 25520, 25490, 25510]],
                   "contracts": [{"tsym": TSYM, "token": "1", "expiry": "2026-11-17", "strike": 25500,
                                  "right": "CE", "dte": 6, "traded": traded, "bars": bars}]}, fh)


# ── recording ────────────────────────────────────────────────────────────────

def test_ticks_are_kept_while_a_position_is_open_and_not_more_often_than_needed(tmp_db):
    rec = research.TickRecorder(tmp_db)
    assert rec.record("og", {"ts": f"{DAY}T10:29:00", "position": None}, "live", 0.0) is False
    assert rec.record("og", snap(f"{DAY}T10:30:05", 150.5), "live", 100.0) is True
    assert rec.record("og", snap(f"{DAY}T10:30:10", 150.6), "live", 105.0) is False     # 5 s later, nothing new
    assert rec.record("og", snap(f"{DAY}T10:30:20", 150.7), "live", 116.0) is True      # 15 s gone
    assert rec.record("og", snap(f"{DAY}T10:30:25", 153.0, peak=2.0), "live", 121.0) is True   # a new best price
    assert rec.record("og", snap(f"{DAY}T10:30:30", 153.0, peak=2.0, stop=150.0), "live", 126.0) is True  # the stop moved
    assert rec.record("og", snap(f"{DAY}T10:31:00", 153.0, peak=2.0, stop=150.0), "live", 131.0) is True  # a new minute
    assert rec.record("og", snap(f"{DAY}T10:31:00", 153.0, peak=2.0, stop=150.0), "live", 200.0) is False  # same status again
    rows = tmp_db.ticks("og", f"{DAY}T10:30:00")
    assert [r["premium"] for r in rows] == [150.5, 150.7, 153.0, 153.0, 153.0]
    assert rows[0]["mode"] == "live" and rows[0]["entry"] == 150.0 and rows[0]["spot"] == 25510.0
    assert rows[0]["session_date"] == DAY and rows[0]["tsym"] == TSYM


def test_the_minute_close_is_the_last_reading_of_the_minute(tmp_db):
    record_path(tmp_db)
    ticks = tmp_db.ticks("og", f"{DAY}T10:30:00")
    assert research.minute_closes(ticks) == [("10:30", 151.0), ("10:31", 158.0), ("10:32", 181.0),
                                             ("10:33", 190.0), ("10:34", 159.0)]


# ── execution quality ────────────────────────────────────────────────────────

def test_the_exit_fill_is_measured_against_the_last_price_seen(tmp_db):
    record_path(tmp_db)
    add_trade(tmp_db)
    trade = tmp_db.trades(limit=1)[0]
    e = research.execution(tmp_db, trade)
    # Last reading before the 10:34:20 sale was 159.00 at 10:34:15; sold at 158.00: 1.00 x 65 = Rs 65 worse.
    assert (e["last_seen"], e["last_seen_time"], e["stop_then"]) == (159.0, "10:34:15", 160.0)
    assert (e["vs_last_seen"], e["vs_last_seen_rs"]) == (-1.0, -65.0)
    assert e["minute_close"] is None and e["vs_minute_close_rs"] is None     # no candles kept that day


def test_with_candles_kept_the_fill_is_also_measured_against_the_minutes_close(tmp_db):
    record_path(tmp_db)
    add_trade(tmp_db)
    keep_candles(tmp_db, [["10:30", 149, 152, 148, 150.4, 900], ["10:34", 189, 190, 156, 157.2, 1200]])
    e = research.execution(tmp_db, tmp_db.trades(limit=1)[0])
    assert e["minute_close"] == 157.2
    assert (e["vs_minute_close"], e["vs_minute_close_rs"]) == (0.8, 52.0)      # sold 0.80 above the 10:34 close
    # Bought at 150.00 in a minute that closed at 150.40: 0.40 x 65 = Rs 26 better than the close.
    assert (e["entry_minute_close"], e["entry_vs_minute_close_rs"]) == (150.4, 26.0)


def test_the_execution_report_totals_only_what_was_recorded(tmp_db):
    record_path(tmp_db)
    add_trade(tmp_db)
    tmp_db.add_trade({"entry_ts": "2026-10-01T11:00:00", "exit_ts": "2026-10-01T12:00:00", "session_date": "2026-10-01",
                      "mode": "live", "tsym": "NIFTY06OCT2625000PE", "qty": 65, "entry_prem": 100.0,
                      "exit_prem": 120.0, "net": 1230.0, "reason": "EOD", "algo_id": "og"})   # before recording began
    rep = research.execution_report(tmp_db, tmp_db.trades(limit=10))
    assert rep["exit_vs_last_seen"] == {"trades": 1, "total_rs": -65.0, "average_rs": -65.0}
    assert rep["trades_without_a_path"] == 1
    assert rep["by_reason"] == [{"reason": "TRAIL", "trades": 1, "average_rs": -65.0, "total_rs": -65.0}]


# ── the rule sets on recorded trades ─────────────────────────────────────────

def full_day():
    """Entry 150 at 10:30. Up to 190 by 10:33 (+26.7%: the stop trails to 160), 157.20 at the 10:34
    close (through it), then on to 215 by 13:35 and 300 by 14:30, closing 280 at 15:09."""
    bars = [["10:30", 149, 152, 148, 150.4, 0], ["10:31", 0, 0, 0, 158.0, 0], ["10:32", 0, 0, 0, 181.0, 0],
            ["10:33", 0, 0, 0, 190.0, 0], ["10:34", 0, 0, 0, 157.2, 0], ["13:35", 0, 0, 0, 215.0, 0],
            ["14:30", 0, 0, 0, 300.0, 0], ["15:09", 0, 0, 0, 280.0, 0], ["15:20", 0, 0, 0, 275.0, 0]]
    return bars


def test_a_path_that_stops_at_the_live_exit_cannot_judge_a_rule_still_holding(tmp_db):
    record_path(tmp_db)
    add_trade(tmp_db)
    r = research.rules_on_trade(tmp_db, tmp_db.trades(limit=1)[0])
    assert r["path"] == {"source": "the program's own readings", "complete": False, "minutes": 4}
    # Every rule set here sells at 10:34 (stop at 160 after the 190 peak, 159 is through it): decided.
    for name in ("v8", "v9", "v10"):
        assert (r["rules"][name]["exit_time"], r["rules"][name]["exit"], r["rules"][name]["decided"]) == ("10:34", 159.0, True)
    # A rule set still holding when the recorded path ends has no answer yet.
    wide = exit_rules.RuleSet("wide", stop=0.45, arm=0.60, trail_from=0.60, trail=0.20)
    undecided = research.rules_on_trade(tmp_db, tmp_db.trades(limit=1)[0], rulesets=(wide,))["rules"]["wide"]
    assert undecided["reason"] == "EOD" and undecided["decided"] is False


def test_with_the_days_candles_every_rule_set_is_replayed_to_its_own_exit(tmp_db):
    add_trade(tmp_db)
    keep_candles(tmp_db, full_day())
    r = research.rules_on_trade(tmp_db, tmp_db.trades(limit=1)[0])
    assert r["path"]["source"] == "Angel one-minute candles" and r["path"]["complete"] is True
    assert r["path"]["minutes"] == 7                                  # after the entry minute, to 15:09
    assert r["rules"]["v10"]["exit_time"] == "10:34" and r["rules"]["v10"]["reason"] == "TRAIL"
    assert r["rules"]["v10"]["exit"] == 157.2
    assert r["actual"] == {"exit": 158.0, "reason": "TRAIL", "exit_time": "10:34", "net": 450.0}
    # With the whole day on record, a rule set that would have held on is replayed to its own exit:
    wide = exit_rules.RuleSet("wide", stop=0.45, arm=0.60, trail_from=0.60, trail=0.20)
    held = research.rules_on_trade(tmp_db, tmp_db.trades(limit=1)[0], rulesets=(wide,))["rules"]["wide"]
    # 300 at 14:30 is +100%: the stop trails to +80% (270); 280 at 15:09 is above it, so it closes there.
    assert (held["exit_time"], held["exit"], held["reason"], held["decided"]) == ("15:09", 280.0, "EOD", True)


def test_rule_sets_are_totalled_on_the_same_trades_only(tmp_db):
    add_trade(tmp_db)
    keep_candles(tmp_db, full_day())
    rep = research.rules_report(tmp_db, tmp_db.trades(limit=10))
    assert rep["rules"] == [rs.name for rs in exit_rules.STANDARD]
    assert rep["comparable_trades"] == 1 and rep["enough"] is False and rep["actual_net"] == 450.0
    # (157.20 - 150) x 65 = 468.00 gross. Charges: 40 brokerage; exchange 19,968 x 0.0003503 = 6.9948;
    # SEBI 0.0200; stamp 0.2925; STT 10.2180; GST 18% of 47.0148 = 8.4627: 65.99. Net 402.01.
    assert rep["totals"]["v9"] == {"net": 402.01, "wins": 1}
    assert rep["totals"]["v8"] == rep["totals"]["v10"] == rep["totals"]["v9"]


def test_a_trade_with_no_path_is_listed_and_not_counted(tmp_db):
    add_trade(tmp_db)
    rep = research.rules_report(tmp_db, tmp_db.trades(limit=10))
    assert rep["trades"] == 1 and rep["recorded_trades"] == 0 and rep["comparable_trades"] == 0
    assert rep["rows"][0]["rules"] == {}


# ── export and the trade page ────────────────────────────────────────────────

def test_the_tick_export_is_one_row_a_reading(tmp_db):
    record_path(tmp_db)
    rows = research.ticks_csv(tmp_db, DAY, DAY).splitlines()
    assert rows[0].split(",") == list(research.TICK_COLUMNS)
    assert len(rows) == 7
    first = dict(zip(research.TICK_COLUMNS, rows[1].split(",")))
    assert first["premium"] == "150.5" and first["gain_pct"] == "0.33" and first["tsym"] == TSYM
    assert research.ticks_csv(tmp_db, "2026-01-01", "2026-01-02").strip() == ",".join(research.TICK_COLUMNS)


def test_the_summary_counts_what_is_on_record(tmp_db):
    assert research.summary(tmp_db)["paths"] == 0
    record_path(tmp_db)
    keep_candles(tmp_db, full_day())
    s = research.summary(tmp_db)
    assert (s["paths"], s["real_money_paths"], s["ticks"], s["candle_days"]) == (1, 1, 6, 1)
    assert s["first_candle_day"] == s["last_candle_day"] == DAY


def test_the_trade_page_has_the_path_the_journal_and_the_order_ids(client, auth):  # noqa: F811
    from app.deps import ctx

    db = ctx().db
    db.upsert_algo("og", "ganesh kavach")
    record_path(db)
    trade_id = add_trade(db)
    keep_candles(db, full_day())
    db.kv_set(f"angel:fills:{DAY}", [
        {"tsym": TSYM, "side": "BUY", "qty": 65, "price": 150.0, "time": "10:30:01", "order_id": "251111000111"},
        {"tsym": "NIFTY17NOV2625400PE", "side": "BUY", "qty": 65, "price": 90.0, "order_id": "999"}])
    eid = db.add_event("ok", "[10:30:01] ENTER BUY CE 25500 x1 lot @ 150.00", source="program", algo_id="og")
    with db.conn() as c:
        c.execute("UPDATE events SET ts = ? WHERE id = ?", (f"{DAY}T10:30:01", eid))
    assert client.get(f"/api/trades/{trade_id}/detail").status_code == 401
    d = client.get(f"/api/trades/{trade_id}/detail", headers=auth).json()
    assert d["trade"]["id"] == trade_id and d["algo_name"] == "ganesh kavach"
    assert d["tick_count"] == 6 and d["ticks"][0]["premium"] == 150.5
    assert d["bars"][0][0] == "10:30" and d["bars_source"].startswith("Angel")
    assert [f["order_id"] for f in d["fills"]] == ["251111000111"]          # this contract's only
    assert [e["message"] for e in d["events"]] == ["[10:30:01] ENTER BUY CE 25500 x1 lot @ 150.00"]
    assert d["execution"]["vs_last_seen_rs"] == -65.0
    assert set(d["rules"]["rules"]) == {rs.name for rs in exit_rules.STANDARD}
    assert client.get("/api/trades/999999/detail", headers=auth).status_code == 404


def test_the_research_endpoints_answer_and_export(client, auth):  # noqa: F811
    from app.deps import ctx

    db = ctx().db
    record_path(db)
    add_trade(db)
    assert client.get("/api/research", headers=auth).json()["paths"] == 1
    assert client.get("/api/research/execution", headers=auth).json()["exit_vs_last_seen"]["trades"] == 1
    assert client.get("/api/research/rules", headers=auth).json()["trades"] == 1
    assert client.get("/api/research/rules?mode=paper", headers=auth).json()["trades"] == 0
    r = client.get(f"/api/research/ticks.csv?from={DAY}&to={DAY}", headers=auth)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv") and TSYM in r.text
    assert client.get(f"/api/research/ticks.csv?from={DAY}&to=2026-01-01", headers=auth).status_code == 400
    assert client.get(f"/api/research/candles.csv?day={DAY}", headers=auth).status_code == 404
    keep_candles(db, full_day())
    body = client.get(f"/api/research/candles.csv?day={DAY}", headers=auth).text
    assert body.splitlines()[0].startswith("day,tsym,expiry,strike,right,dte,traded,minute")
    assert f"{DAY},{TSYM},2026-11-17,25500,CE,6,yes,10:31" in body


def test_the_open_position_carries_what_its_exit_will_do_next(tmp_db):
    from app.program_output import ProgramOutput

    out = ProgramOutput(tmp_db, fleet=None)
    tmp_db.upsert_algo("og", "ganesh kavach")
    for line in ("PROFIT LADDER (per trade):", "  take-profit: no all-day target", "  break-even : at +20% gain, stop moves to entry",
                 "  trail      : past +20% gain, stop rides 20% of entry behind the peak",
                 "  late TP    : from 180 min after entry, sell at +40% or better",
                 "  runner lock: past +90% gain, stop rides 10% behind peak", "  hard stop  : -45% of premium"):
        tmp_db.add_event("info", line, source="program", algo_id="og")
    s = {"position": {"entry_premium": 200.0, "peak_pct": 35.0, "hold_min": 200.0}}
    out._attach_exit_plan(tmp_db.algo("og"), 4242, s)
    plan = s["position"]["exit_plan"]
    assert plan["stage"] == "trailing" and plan["stop_price"] == 230.0
    assert plan["next"]["key"] == "runner" and plan["late_tp"]["on"] is True
    # A program whose settings cannot be read gets no plan rather than a guess.
    tmp_db.upsert_algo("other", "other")
    s2 = {"position": {"entry_premium": 200.0, "peak_pct": 35.0, "hold_min": 10.0}}
    out._attach_exit_plan(tmp_db.algo("other"), 1, s2)
    assert "exit_plan" not in s2["position"]


def test_the_charge_model_is_the_research_harnesss():
    # 150 -> 166 on 65: 40 + 7.1952 + 0.0205 + 0.2925 + 10.7900 + 8.4988 = 66.80
    assert pytest.approx(exit_rules.charges(150.0, 166.0), abs=0.005) == 66.80


def test_the_expectation_is_stored_and_returns_the_real_money_trades_since_its_date(client, auth):  # noqa: F811
    from app.deps import ctx

    db = ctx().db
    add_trade(db)                                              # real money, today, +450.00
    add_trade(db, mode="paper", exit_ts=f"{DAY}T10:40:00")     # paper: not counted
    assert client.get("/api/research/expectation", headers=auth).json() == {"expectation": None, "trades": []}
    bad = client.post("/api/research/expectation", headers=auth, json={"mean": 290, "sd": 10, "since": DAY})
    assert bad.status_code == 422                              # a spread that small is a typing slip
    r = client.post("/api/research/expectation", headers=auth,
                    json={"mean": 290, "sd": 3450, "since": DAY, "label": "v12 backtest"}).json()
    assert r["expectation"]["mean"] == 290 and r["expectation"]["label"] == "v12 backtest"
    assert r["trades"] == [{"day": DAY, "net": 450.0, "lots": 1}]
    assert any(a["action"] == "research.expectation" for a in db.audit_tail(5))
    assert client.post("/api/research/expectation", json={"mean": 1, "sd": 100, "since": DAY}).status_code == 401
