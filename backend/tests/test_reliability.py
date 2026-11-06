"""Crash loops, Angel API trouble, feed divergence and uptime reach the desk.

21 Oct 2026: the live program exited with code 1 seven times between 09:05
and 09:49 before it ran clean from 10:43, holding real money throughout, and
nothing on screen said so. 23 Oct: one exit -2 at 09:05. Angel's timeouts and
rate limits turned up most days, visible only as "241 errors logged" in a
report's drawdown tile.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from app import reliability

ALGO = "og"


def run(db, start, stop, code, reason="process exited", algo=ALGO, mode="live"):
    rid = db.start_run(4242, mode, "scheduler", algo_id=algo)
    with db.conn() as c:
        c.execute("UPDATE engine_runs SET started_ts=?, stopped_ts=?, exit_code=?, reason=? WHERE id=?",
                  (start, stop, code, reason, rid))
    return rid


def oct21(db):
    """The crash loop as the run history recorded it, then the clean session."""
    for start, stop in (("09:05:01", "09:05:20"), ("09:05:40", "09:06:02"), ("09:21:00", "09:21:30"),
                        ("09:23:00", "09:23:40"), ("09:25:00", "09:25:35"), ("09:30:00", "09:30:30"),
                        ("09:49:00", "09:49:20")):
        run(db, f"2026-10-21T{start}", f"2026-10-21T{stop}", 1)
    run(db, "2026-10-21T10:43:00", "2026-10-21T15:25:10", 0)


def test_seven_exits_in_44_minutes_are_one_crash_loop(tmp_db):
    oct21(tmp_db)
    run(tmp_db, "2026-10-23T09:05:00", "2026-10-23T09:05:08", -2)
    eps = reliability.crash_episodes(reliability.runs_since(tmp_db, "2026-10-01"))
    [loop] = eps["loops"]
    assert loop["exits"] == 7 and loop["codes"] == [1] * 7
    assert (loop["first_exit"], loop["last_exit"]) == ("2026-10-21T09:05:20", "2026-10-21T09:49:20")
    [single] = eps["crashes"]
    assert single["first_exit"].startswith("2026-10-23T09:05") and single["codes"] == [-2]


def test_a_stop_that_was_asked_for_is_never_a_crash(tmp_db):
    for i in range(5):
        run(tmp_db, f"2026-10-24T10:0{i}:00", f"2026-10-24T10:0{i}:30", -2,
            reason="stopped on request (operator stop); process exited")
    eps = reliability.crash_episodes(reliability.runs_since(tmp_db, "2026-10-01"))
    assert eps == {"loops": [], "crashes": []}


def test_exits_far_apart_are_separate_crashes(tmp_db):
    run(tmp_db, "2026-10-24T09:05:00", "2026-10-24T09:06:00", 1)
    run(tmp_db, "2026-10-24T11:05:00", "2026-10-24T11:06:00", 1)
    run(tmp_db, "2026-10-24T14:05:00", "2026-10-24T14:06:00", 1)
    eps = reliability.crash_episodes(reliability.runs_since(tmp_db, "2026-10-01"))
    assert eps["loops"] == [] and len(eps["crashes"]) == 3


def test_uptime_counts_the_crash_loop_as_time_down(tmp_db):
    oct21(tmp_db)
    up = reliability.uptime(reliability.runs_since(tmp_db, "2026-10-01"), ALGO, now=datetime(2026, 10, 22, 9, 0))
    [day] = up["daily"]
    assert day["date"] == "2026-10-21"
    # Up 09:21-09:21:30 ... and from 10:43 to 15:30 (the 15:25 stop): about 76% of 375 min.
    assert 75.0 <= day["pct"] <= 78.0
    assert up["overall_pct"] == day["pct"]


def test_a_running_engine_counts_up_to_now(tmp_db):
    rid = tmp_db.start_run(1, "live", "scheduler", algo_id=ALGO)
    with tmp_db.conn() as c:
        c.execute("UPDATE engine_runs SET started_ts=? WHERE id=?", ("2026-11-06T09:05:00", rid))
    up = reliability.uptime(reliability.runs_since(tmp_db, "2026-11-01"), ALGO, now=datetime(2026, 11, 6, 12, 0))
    assert up["daily"][0]["pct"] == 100.0


@pytest.mark.parametrize("line,kind", [
    ("ltpData failed: HTTPSConnectionPool(host='apiconnect.angelone.in'): Read timed out. (read timeout=7)", "timeouts"),
    ("candle request refused: Access denied because of exceeding access rate", "rate_limits"),
    ("getPosition: Couldn't parse the JSON response received from the server: invalid response from upstream server", "upstream"),
    ("502 Bad Gateway from Angel", "upstream"),
    ("order placed: NIFTY 24500 CE x65", None),
])
def test_angel_api_trouble_is_sorted_by_kind(line, kind):
    assert reliability.classify_api(line) == kind


def test_errors_are_counted_per_day_and_week_on_week(tmp_db):
    def ev(ts, level, msg, source="program"):
        eid = tmp_db.add_event(level, msg, source=source, algo_id=ALGO)
        with tmp_db.conn() as c:
            c.execute("UPDATE events SET ts=? WHERE id=?", (ts, eid))

    ev("2026-10-27T10:00:00", "warn", "Read timed out")
    ev("2026-10-27T10:01:00", "error", "Access denied because of exceeding access rate")
    ev("2026-11-03T11:00:00", "error", "invalid response from upstream server")
    ev("2026-11-03T11:05:00", "error", "Traceback (most recent call last):")
    ev("2026-11-03T11:06:00", "error", "could not read og's status file", source="api")   # the desk's own
    events = reliability.journal_since(tmp_db, "2026-10-27")
    days = ["2026-10-27", "2026-11-03"]
    got = reliability.api_errors(events, days)
    d27, d03 = got["daily"]
    assert (d27["timeouts"], d27["rate_limits"], d27["total"]) == (1, 1, 2)
    assert (d03["upstream"], d03["other_errors"], d03["total"]) == (1, 1, 2)
    assert got["totals"]["total"] == 4
    assert got["trend"] == {"this_week": 2, "last_week": 2, "change_pct": 0.0}


def test_a_feed_divergence_is_surfaced_with_its_size(tmp_db):
    tmp_db.add_event("warn", "price stream vs Angel's candles: closes differ by up to 371.1 pts in the last "
                             "15 min — Angel's are used", source="program", algo_id=ALGO)
    got = reliability.divergences(reliability.journal_since(tmp_db, "2000-01-01"))
    assert got["count"] == 1 and got["max_pts"] == 371.1


def test_a_recent_crash_loop_is_on_the_banner_until_acknowledged(tmp_db, monkeypatch):
    monkeypatch.setattr(reliability, "_now", lambda: datetime(2026, 10, 21, 10, 0))
    reliability.clear_cache()
    oct21(tmp_db)
    [alert] = reliability.banner_alerts(tmp_db)
    assert alert["kind"] == "crash_loop" and alert["exits"] == 7
    reliability.acknowledge(tmp_db, alert["key"])
    reliability.clear_cache()
    assert reliability.banner_alerts(tmp_db) == []


def test_an_old_crash_loop_leaves_the_banner_on_its_own(tmp_db, monkeypatch):
    monkeypatch.setattr(reliability, "_now", lambda: datetime(2026, 10, 23, 12, 0))
    reliability.clear_cache()
    oct21(tmp_db)
    assert reliability.banner_alerts(tmp_db) == []
    rep = reliability.report(tmp_db, days=10, algo_id=ALGO)
    assert len(rep["runs"]["loops"]) == 1, "it stays in the reliability report"


def test_a_requested_stop_is_recorded_as_one(tmp_db, tmp_path):
    """The supervisor marks a run it was asked to stop, so its exit code is not read as a crash."""
    from app.supervisor import Supervisor

    sup = Supervisor(tmp_db, algo_id="og-test")
    sup.state.run_id = tmp_db.start_run(99, "paper", "test", algo_id="og-test")
    sup.state.running, sup.state.pid = True, 99
    sup.state.stop_requested = "operator stop"
    sup._mark_stopped(-2, "process exited")
    [r] = tmp_db.recent_runs(1, algo_id="og-test")
    assert r["reason"].startswith("stopped on request (operator stop)")
    assert reliability.abnormal(r) is False
