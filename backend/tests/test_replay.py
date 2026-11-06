"""A past session put back together for replay (app/replay.py)."""

from __future__ import annotations

from datetime import datetime, timezone

from app import replay


def ist_epoch(day: str, hm: str) -> int:
    """Chart time: epoch seconds already shifted to IST, as the chart feed sends it."""
    d = datetime.fromisoformat(f"{day}T{hm}:00").replace(tzinfo=timezone.utc)
    return int(d.timestamp())


def test_a_session_comes_back_with_its_pnl_trades_journal_and_candles(tmp_db):
    tmp_db.upsert_algo("og", "ganesh_kavach_og_real (3)")
    for i, (pl, spot) in enumerate([(0.0, 24480.0), (900.0, 24530.0), (3847.64, 24610.0)]):
        tmp_db.add_equity_sample("2026-11-06", 42027.28 + pl, 0.0, 69810.0, pl, algo_id="og", spot=spot)
    tmp_db.add_trade({"entry_ts": "2026-11-06 12:14", "exit_ts": "2026-11-06 13:16", "session_date": "2026-11-06",
                      "mode": "live", "side": "CE", "strike": 24500, "qty": 65, "entry_prem": 150.0,
                      "exit_prem": 210.96, "gross": 3962.25, "charges": 114.61, "net": 3847.64, "algo_id": "og"})
    eid = tmp_db.add_event("info", "[12:14:03] ENTER BUY CE 24500", source="program", algo_id="og")
    with tmp_db.conn() as c:
        c.execute("UPDATE events SET ts='2026-11-06T12:14:03' WHERE id=?", (eid,))
    tmp_db.kv_set("market:bars:2026-11-06", {"day": "2026-11-06", "source": "angel",
                                             "bars": [{"t": ist_epoch("2026-11-06", "12:14"), "o": 1, "h": 2, "l": 0.5, "c": 1.5}]})

    s = replay.session(tmp_db, "2026-11-06", "og")
    assert s["name"] == "ganesh_kavach_og_real (3)" and s["day"] == "2026-11-06"
    assert [x["day_pl"] for x in s["samples"]] == [0.0, 900.0, 3847.64]
    assert [x["spot"] for x in s["samples"]] == [24480.0, 24530.0, 24610.0]
    assert s["trades"][0]["net"] == 3847.64
    assert s["events"][0]["message"].startswith("[12:14:03] ENTER")
    assert len(s["bars"]) == 1 and s["bars_source"] == "angel"
    assert s["sessions"] == ["2026-11-06"]


def test_without_a_day_the_latest_session_is_replayed(tmp_db):
    tmp_db.upsert_algo("og", "og")
    tmp_db.add_equity_sample("2026-11-05", 100.0, 0.0, 100.0, 0.0, algo_id="og")
    tmp_db.add_equity_sample("2026-11-06", 100.0, 0.0, 100.0, 0.0, algo_id="og")
    assert replay.session(tmp_db, None, "og")["day"] == "2026-11-06"


def test_only_todays_candles_are_archived_and_angels_are_kept(tmp_db, monkeypatch):
    monkeypatch.setattr(replay, "now_ist", lambda: datetime(2026, 11, 9, 11, 0))
    today = [{"t": ist_epoch("2026-11-09", "09:15"), "o": 1, "h": 1, "l": 1, "c": 1},
             {"t": ist_epoch("2026-11-09", "09:16"), "o": 1, "h": 1, "l": 1, "c": 1}]
    friday = [{"t": ist_epoch("2026-11-06", "15:29"), "o": 1, "h": 1, "l": 1, "c": 1}]
    assert replay.archive_today(tmp_db, {"source": "angel", "bars": friday + today}) == 2
    assert len(tmp_db.kv_get("market:bars:2026-11-09", None)["bars"]) == 2
    # Yahoo's fewer bars never replace Angel's.
    assert replay.archive_today(tmp_db, {"source": "yahoo", "bars": today[:1]}) == 0
    assert tmp_db.kv_get("market:bars:2026-11-09", None)["source"] == "angel"
