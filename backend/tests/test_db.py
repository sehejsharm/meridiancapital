from __future__ import annotations

from shared.db import K_SNAPSHOT


def test_kv_round_trip_and_revision(tmp_db):
    tmp_db.kv_set(K_SNAPSHOT, {"a": 1})
    assert tmp_db.kv_get(K_SNAPSHOT) == {"a": 1}
    rev1 = tmp_db.kv_rev(K_SNAPSHOT)
    tmp_db.kv_set(K_SNAPSHOT, {"a": 2})
    assert tmp_db.kv_rev(K_SNAPSHOT) == rev1 + 1
    assert tmp_db.kv_get(K_SNAPSHOT) == {"a": 2}


def test_kv_missing_key_returns_default(tmp_db):
    assert tmp_db.kv_get("nope", "fallback") == "fallback"


def test_events_are_returned_newest_first(tmp_db):
    for i in range(5):
        tmp_db.add_event("info", f"event {i}")
    events = tmp_db.events(limit=3)
    assert [e["message"] for e in events] == ["event 4", "event 3", "event 2"]


def test_events_filter_by_level(tmp_db):
    tmp_db.add_event("info", "quiet")
    tmp_db.add_event("critical", "loud")
    assert [e["message"] for e in tmp_db.events(levels=["critical"])] == ["loud"]


def test_events_after_id_streams_only_new(tmp_db):
    first = tmp_db.add_event("info", "one")
    tmp_db.add_event("info", "two")
    assert [e["message"] for e in tmp_db.events(after_id=first)] == ["two"]


def test_commands_are_claimed_exactly_once(tmp_db):
    tmp_db.enqueue_command("halt")
    tmp_db.enqueue_command("resume", {"week": True})
    first = tmp_db.claim_commands()
    assert [c["action"] for c in first] == ["halt", "resume"]
    assert first[1]["payload"] == {"week": True}
    assert tmp_db.claim_commands() == []


def test_finished_command_records_result(tmp_db):
    cid = tmp_db.enqueue_command("ping")
    tmp_db.claim_commands()
    tmp_db.finish_command(cid, "pong")
    assert tmp_db.recent_commands()[0]["result"] == "pong"


def test_stale_commands_expire_instead_of_replaying(tmp_db):
    tmp_db.enqueue_command("flatten")
    tmp_db.claim_commands()
    assert tmp_db.expire_stale_commands() == 1
    assert tmp_db.recent_commands()[0]["status"] == "expired"
    assert tmp_db.claim_commands() == []


def test_equity_curve_is_chronological(tmp_db):
    for eq in (100.0, 200.0, 300.0):
        tmp_db.add_equity_sample("2026-09-10", eq, 0.0, eq, 0.0)
    assert [p["equity"] for p in tmp_db.equity_curve()] == [100.0, 200.0, 300.0]


def test_holidays_upsert_and_delete(tmp_db):
    tmp_db.add_holiday("2026-10-21", "Diwali")
    tmp_db.add_holiday("2026-10-21", "Diwali Laxmi Pujan")
    assert tmp_db.holidays() == [{"day": "2026-10-21", "label": "Diwali Laxmi Pujan", "source": "operator"}]
    tmp_db.remove_holiday("2026-10-21")
    assert tmp_db.holidays() == []


def test_a_published_holiday_is_loaded_once(tmp_db):
    published = {"2026-10-02": "Mahatma Gandhi Jayanti", "2026-10-20": "Dussehra"}
    assert tmp_db.seed_holidays(published, source="NSE") == ["2026-10-02", "2026-10-20"]
    assert {h["day"]: h["source"] for h in tmp_db.holidays()} == {"2026-10-02": "NSE", "2026-10-20": "NSE"}
    assert tmp_db.seed_holidays(published, source="NSE") == [], "nothing is loaded twice"
    # A closure the operator removes (say NSE called it off) stays removed...
    tmp_db.remove_holiday("2026-10-20")
    assert tmp_db.seed_holidays(published, source="NSE") == []
    assert [h["day"] for h in tmp_db.holidays()] == ["2026-10-02"]
    # ...while a date new to the list, next year's say, is picked up.
    assert tmp_db.seed_holidays({**published, "2027-01-26": "Republic Day"}, source="NSE") == ["2027-01-26"]


def test_an_older_calendar_gains_the_source_column(tmp_path):
    import sqlite3

    from shared.db import Database

    path = tmp_path / "old.db"
    with sqlite3.connect(path) as c:
        c.execute("CREATE TABLE holidays (day TEXT PRIMARY KEY, label TEXT)")
        c.execute("INSERT INTO holidays VALUES ('2026-10-21', 'Diwali')")
    db = Database(path)
    assert db.holidays() == [{"day": "2026-10-21", "label": "Diwali", "source": "operator"}]


def test_engine_run_lifecycle_is_recorded(tmp_db):
    run_id = tmp_db.start_run(4242, "paper", "schedule")
    tmp_db.end_run(run_id, 0, "session ended")
    run = tmp_db.recent_runs()[0]
    assert run["pid"] == 4242 and run["exit_code"] == 0 and run["stopped_ts"]
