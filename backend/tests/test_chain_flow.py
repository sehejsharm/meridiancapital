"""Option flow: minute snapshots of the chain become money flow and positioning by strike."""

from __future__ import annotations

import json
import time

from app import chain_flow
from shared.db import Database


def chain(ts, ce, pe, strike=25300):
    """ce/pe: (ltp, day volume, oi)."""
    side = lambda tok, v: {"token": tok, "ltp": v[0], "volume": v[1], "oi": v[2]}  # noqa: E731
    return {"ts": ts, "rows": [{"strike": strike, "ce": side("c1", ce), "pe": side("p1", pe)}]}


def test_readings_follow_the_standard_table():
    assert chain_flow.reading(+2, +100) == "long_buildup"
    assert chain_flow.reading(-2, +100) == "short_buildup"
    assert chain_flow.reading(+2, -100) == "short_covering"
    assert chain_flow.reading(-2, -100) == "long_unwinding"
    assert chain_flow.reading(0, 100) == "quiet" and chain_flow.reading(None, 1) == "quiet"


def test_one_snapshot_a_minute_and_the_flow_it_gives(tmp_path):
    db = Database(tmp_path / "f.db")
    assert chain_flow.for_day(db, "2026-11-18")["available"] is False
    assert chain_flow.record(db, chain("2026-11-18T09:30:05", (100, 1000, 5000), (90, 2000, 8000)))
    assert chain_flow.record(db, chain("2026-11-18T09:30:50", (101, 1300, 5000), (90, 2100, 8000)))   # same minute: replaced
    assert chain_flow.record(db, chain("2026-11-18T09:31:10", (104, 2300, 5600), (86, 2600, 7700)))
    assert not chain_flow.record(db, chain("2026-11-18T09:29:00", (1, 1, 1), (1, 1, 1)))              # older: ignored
    f = chain_flow.for_day(db, "2026-11-18")
    assert f["available"] and (f["since"], f["to"]) == ("09:30", "09:31")
    # 09:31: calls traded 1,000 more at 104 = 1,04,000; puts 500 more at 86 = 43,000.
    assert f["timeline"] == [{"t": "09:31", "ce": 104000.0, "pe": 43000.0}]
    row = f["strikes"][0]
    assert row["strike"] == 25300
    assert row["ce"]["reading"] == "long_buildup" and row["ce"]["oi_change"] == 600
    assert row["pe"]["reading"] == "long_unwinding" and row["pe"]["oi_change"] == -300
    assert f["totals"]["put_call_ratio"] == round(43000 / 104000, 3)


def test_a_volume_that_goes_backwards_is_not_counted_as_trading(tmp_path):
    db = Database(tmp_path / "f.db")
    chain_flow.record(db, chain("2026-11-18T09:30:00", (100, 5000, 1), (90, 5000, 1)))
    chain_flow.record(db, chain("2026-11-18T09:31:00", (100, 10, 1), (90, 5000, 1)))     # a reset feed
    assert chain_flow.for_day(db, "2026-11-18")["timeline"][0]["ce"] == 0.0


def test_a_program_that_writes_its_chain_feeds_the_desk(tmp_path):
    from types import SimpleNamespace

    from app.program_output import ProgramOutput
    from engine.market_feed import K_CHAIN

    db = Database(tmp_path / "f.db")
    folder = tmp_path / "prog"
    folder.mkdir()
    sup = SimpleNamespace(program_path=folder / "gk.py")
    po = ProgramOutput(db, SimpleNamespace(all=lambda: {}))
    assert po.publish_chain("og", sup) is False                       # no file: nothing (v15 and older)
    (folder / "gk50k_chain.json").write_text(json.dumps(chain("2026-11-18T09:31:00", (1, 2, 3), (4, 5, 6))))
    assert po.publish_chain("og", sup) is True
    assert db.kv_get(K_CHAIN)["publisher"] == "og"
    assert po.publish_chain("og", sup) is False                       # the same chain is not published twice
    old = folder / "gk50k_chain.json"
    t = time.time() - 300
    import os
    os.utime(old, (t, t))
    (folder / "x").write_text("")
    assert po.publish_chain("og", sup) is False                       # a stale file is ignored


def test_a_minute_is_one_small_row_and_old_days_are_dropped(tmp_path):
    db = Database(tmp_path / "f.db")
    db.kv_set(chain_flow._key("2026-10-01"), {"minutes": [], "meta": {}})        # a blob from before the table
    db.chain_minute_put("2026-11-01", "09:30", {"c1": [1, 2, 3, 25300, "CE"]})   # twelve days back
    db.chain_minute_put("2026-11-10", "09:30", {"c1": [1, 2, 3, 25300, "CE"]})   # three days back
    assert chain_flow.record(db, chain("2026-11-13T09:30:05", (100, 1000, 5000), (90, 2000, 8000)))
    assert db.chain_minutes("2026-11-01") == [] and db.kv_get(chain_flow._key("2026-10-01")) is None
    assert len(db.chain_minutes("2026-11-10")) == 1
    # The same chain again (the upkeep job re-reads it every few seconds): no write.
    before = db.chain_minutes_version("2026-11-13")
    assert not chain_flow.record(db, chain("2026-11-13T09:30:20", (100, 1000, 5000), (90, 2000, 8000)))
    assert db.chain_minutes_version("2026-11-13") == before


def test_the_answer_is_reused_until_a_minute_changes(tmp_path):
    db = Database(tmp_path / "f.db")
    chain_flow.record(db, chain("2026-11-18T09:30:00", (100, 1000, 5000), (90, 2000, 8000)))
    chain_flow.record(db, chain("2026-11-18T09:31:00", (104, 2000, 5600), (86, 2500, 7700)))
    first = chain_flow.for_day(db, "2026-11-18")
    assert chain_flow.for_day(db, "2026-11-18") is first                  # nothing new: the same answer
    chain_flow.record(db, chain("2026-11-18T09:31:40", (105, 2600, 5700), (85, 2600, 7600)))
    again = chain_flow.for_day(db, "2026-11-18")
    assert again is not first and again["timeline"][-1]["ce"] == 1600 * 105.0


def test_a_day_kept_as_one_blob_before_the_table_still_reads(tmp_path):
    db = Database(tmp_path / "f.db")
    db.kv_set(chain_flow._key("2026-11-17"), {
        "minutes": [["09:30", {"c1": [100, 1000, 5000]}], ["09:31", {"c1": [104, 2000, 5600]}]],
        "meta": {"c1": [25300, "CE"]}})
    f = chain_flow.for_day(db, "2026-11-17")
    assert f["available"] and f["timeline"] == [{"t": "09:31", "ce": 104000.0, "pe": 0.0}]
