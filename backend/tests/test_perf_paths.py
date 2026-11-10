"""The paths the dashboard polls do not re-read or re-parse a program's source,
and date-scoped reads ask the index (measured on a year of data, 10 Nov 2026).

/api/algos took 266 ms with two programs: every call read each active version's
~200 KB source and parsed it. The Deck asks every 10 seconds.
"""

from __future__ import annotations

from app import code_redact, logs, program_config, programs, reports
from tests.test_desk import auth, client  # noqa: F401  (fixtures)

PROGRAM = 'import sys\nBE_TRIGGER = 0.20\nRUNNER_AT = 0.90\nRUNNER_FRAC = 0.10\nif __name__ == "__main__":\n    print(sys.argv)\n'
STRATEGY_LIKE = "x = 1\n"


def _version(db, algo_id, source, sha="same-sha"):
    db.upsert_algo(algo_id, algo_id, kind="uploaded")
    vid = db.add_version(algo_id, source, sha, "sehej")
    db.set_algo_fields(algo_id, active_version=vid)
    return vid


def test_version_meta_carries_no_source(tmp_db):
    vid = _version(tmp_db, "og", PROGRAM)
    meta = tmp_db.version_meta(vid)
    assert "source" not in meta and meta["sha256"] == "same-sha" and meta["version"] == 1
    assert tmp_db.version_meta(999) is None


def test_a_programs_runtime_is_worked_out_once(tmp_db, monkeypatch):
    vid = _version(tmp_db, "og", PROGRAM)
    calls = []
    real = programs.runtime_of
    monkeypatch.setattr(programs, "runtime_of", lambda src: calls.append(1) or real(src))
    reads = []
    real_version = tmp_db.version
    monkeypatch.setattr(tmp_db, "version", lambda i: reads.append(i) or real_version(i))
    for _ in range(5):
        assert programs.runtime_of_version(tmp_db, tmp_db.version_meta(vid)) == "program"
    assert len(calls) == 1 and reads == [vid], "the source was read and parsed more than once"


def test_two_versions_are_never_confused_even_with_the_same_hash(tmp_db):
    a = _version(tmp_db, "prog", PROGRAM)
    b = _version(tmp_db, "other", STRATEGY_LIKE)        # same (fake) hash, different text
    assert programs.runtime_of_version(tmp_db, tmp_db.version_meta(a)) == "program"
    assert programs.runtime_of_version(tmp_db, tmp_db.version_meta(b)) == "invalid"


def test_code_constants_are_parsed_once_and_show_the_runner_lock(tmp_db, monkeypatch):
    _version(tmp_db, "og", PROGRAM)
    calls = []
    real = program_config.parse_source_constants
    monkeypatch.setattr(program_config, "parse_source_constants", lambda src: calls.append(1) or real(src))
    for _ in range(3):
        cfg = program_config.code_config(tmp_db, tmp_db.algo("og"))
    assert len(calls) == 1
    assert cfg["values"]["RUNNER_AT"] == 0.9 and cfg["values"]["RUNNER_FRAC"] == 0.1


def test_the_runner_lock_is_read_from_the_startup_block():
    got = program_config.parse_startup_block([
        "PROFIT LADDER (per trade):",
        "  trail      : past +20% gain, stop rides 20% behind peak (locks profit)",
        "  runner lock: past +90% gain, stop rides 10% behind peak",
        "entries 10:15–14:00, flat by 15:10. Ctrl-C to stop.",
    ])
    assert got["TRAIL_FRAC"] == 0.2 and got["RUNNER_AT"] == 0.9 and got["RUNNER_FRAC"] == 0.1


def _event(db, ts, msg, level="info"):
    eid = db.add_event(level, msg, source="program", algo_id="og")
    with db.conn() as c:
        c.execute("UPDATE events SET ts=? WHERE id=?", (ts, eid))


def test_an_old_days_log_is_found_past_the_newest_events(tmp_db, monkeypatch):
    _event(tmp_db, "2026-01-05T10:00:00", "old day line")
    for i in range(30):
        _event(tmp_db, f"2026-11-06T10:{i:02d}:00", f"recent {i}")
    monkeypatch.setattr(logs, "MAX_EVENTS", 10)          # the old code looked only at the newest N of all time
    got = logs.day_events(tmp_db, "2026-01-05", "og")
    assert [e["message"] for e in got] == ["old day line"]


def test_events_by_date_range_is_inclusive(tmp_db):
    for ts in ("2026-10-31T23:59:59", "2026-11-01T00:00:00", "2026-11-06T15:29:00", "2026-11-07T00:00:00"):
        _event(tmp_db, ts, ts)
    got = sorted(e["ts"] for e in tmp_db.events(limit=10, date_from="2026-11-01", date_to="2026-11-06"))
    assert got == ["2026-11-01T00:00:00", "2026-11-06T15:29:00"]


def test_a_report_for_an_old_period_has_its_events(tmp_db):
    _event(tmp_db, "2026-01-05T10:00:00", "old error", level="error")
    for i in range(2100):                                 # more than the 2,000 the report reads
        _event(tmp_db, f"2026-11-06T{9 + i // 600:02d}:{(i // 10) % 60:02d}:{i % 60:02d}", f"recent {i}")
    p = reports.build_payload(tmp_db, "og", "2026-01-01", "2026-01-31", "t")
    assert [e["message"] for e in p["events"]] == ["old error"] and p["summary"]["errors"] == 1


def test_the_page_payload_carries_no_balance_marks_but_the_download_does(tmp_db):
    tmp_db.upsert_algo("og", "og", kind="uploaded")
    for i in range(5):
        tmp_db.add_equity_sample("2026-11-06", 45000.0 + i, 0.0, 45000.0, float(i), algo_id="og")
    tmp_db.add_trade({"entry_ts": "2026-11-06 10:00", "exit_ts": "2026-11-06 11:00", "session_date": "2026-11-06",
                      "mode": "live", "net": 500.0, "gross": 560.0, "charges": 60.0, "algo_id": "og"})
    page = reports.build_payload(tmp_db, "og", "2026-11-01", "2026-11-30", "t", with_equity=False)
    full = reports.build_payload(tmp_db, "og", "2026-11-01", "2026-11-30", "t")
    assert page["equity"] == [] and len(full["equity"]) == 5
    assert page["summary"] == full["summary"] and page["pnl_curve"] == full["pnl_curve"]
    assert "EQUITY MARKS" in reports.to_csv(full)


def test_redaction_of_the_same_source_is_not_repeated(monkeypatch):
    code_redact._redact.cache_clear()
    src = 'API_KEY = "Zq8LmT3x"\nSTOP = 1\n'
    a = code_redact.redact(src)
    b = code_redact.redact(src)
    assert a == b and code_redact._redact.cache_info().hits == 1
    a[0][1] = "changed by a caller"                       # a caller's edit must not reach the cache
    assert code_redact.redact(src)[0][1] == "STOP = 1"
    assert "Zq8LmT3x" not in "\n".join(code_redact.redact(src)[0])


def test_the_results_page_gets_the_newest_300_lines_and_the_count(client, auth):  # noqa: F811
    from app.deps import ctx

    db = ctx().db
    for i in range(450):
        _event(db, f"2026-11-06T{9 + i // 60:02d}:{i % 60:02d}:00", f"line {i}")
    q = {"start": "2026-11-06", "end": "2026-11-06"}
    r = client.get("/api/reports", params=q, headers=auth).json()
    assert len(r["events"]) == 300 and r["events_total"] >= 450 and r["equity"] == []
    assert r["events"][0]["message"] == "line 449", "newest first"
    csv = client.get("/api/reports.csv", params=q, headers=auth).text
    assert csv.count("line ") >= 450, "the download keeps every line"
