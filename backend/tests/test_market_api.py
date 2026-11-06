"""Which NIFTY candles the deck gets, and what it is told about the chain."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from app import market
from engine.market_feed import K_BARS, K_CHAIN


@pytest.fixture(autouse=True)
def _fresh_yahoo_cache():
    market._yahoo.update(at=0.0, bars=None, error=None)
    yield
    market._yahoo.update(at=0.0, bars=None, error=None)


def _now_iso(offset_sec=0):
    return (datetime.now().astimezone() - timedelta(seconds=offset_sec)).isoformat(timespec="seconds")


_T0 = int(datetime(2026, 9, 10, 3, 35, tzinfo=timezone.utc).timestamp())
YAHOO = json.dumps({"chart": {"result": [{
    "timestamp": [_T0, _T0 + 60],                        # 03:35 / 03:36 UTC
    "indicators": {"quote": [{"open": [25000, None], "high": [25010, 25011],
                              "low": [24990, 24991], "close": [25005, 25006]}]},
}]}}).encode()


def test_a_fresh_engine_feed_wins(tmp_db):
    tmp_db.kv_set(K_BARS, {"ts": _now_iso(20), "bars": [{"t": 1, "o": 1, "h": 1, "l": 1, "c": 1}]})
    out = market.nifty_chart(tmp_db, fetch=lambda url: pytest.fail("must not call Yahoo"))
    assert out["source"] == "angel"


def test_with_no_engine_the_public_feed_is_used_and_shifted_to_ist(tmp_db):
    out = market.nifty_chart(tmp_db, fetch=lambda url: YAHOO)
    assert out["source"] == "yahoo"
    assert len(out["bars"]) == 1, "a candle with a missing value is dropped, not drawn as zero"
    shown = datetime(1970, 1, 1) + timedelta(seconds=out["bars"][0]["t"])
    assert (shown.hour, shown.minute) == (9, 5), "03:35 UTC is 09:05 IST"


def test_a_stale_engine_feed_gives_way_to_the_public_one(tmp_db):
    tmp_db.kv_set(K_BARS, {"ts": _now_iso(3600), "bars": [{"t": 1, "o": 1, "h": 1, "l": 1, "c": 1}]})
    assert market.nifty_chart(tmp_db, fetch=lambda url: YAHOO)["source"] == "yahoo"


def test_when_everything_is_down_old_engine_candles_are_shown_marked_stale(tmp_db):
    tmp_db.kv_set(K_BARS, {"ts": _now_iso(3600), "bars": [{"t": 1, "o": 1, "h": 1, "l": 1, "c": 1}]})

    def down(url):
        raise OSError("blocked")

    out = market.nifty_chart(tmp_db, fetch=down)
    assert out["source"] == "angel" and out["stale"] is True


def test_the_public_feed_is_asked_at_most_every_30_seconds(tmp_db):
    calls = []

    def fetch(url):
        calls.append(url)
        return YAHOO

    market.nifty_chart(tmp_db, fetch=fetch)
    market.nifty_chart(tmp_db, fetch=fetch)
    assert len(calls) == 1


def test_no_chain_says_how_to_get_one(tmp_db):
    out = market.option_chain(tmp_db)
    assert out["available"] is False and "engine" in out["reason"]


def test_an_old_chain_is_marked_stale(tmp_db):
    tmp_db.kv_set(K_CHAIN, {"ts": _now_iso(300), "rows": []})
    assert market.option_chain(tmp_db)["stale"] is True
    tmp_db.kv_set(K_CHAIN, {"ts": _now_iso(5), "rows": []})
    assert market.option_chain(tmp_db)["stale"] is False


# ── what the chain says beyond each quote ────────────────────────────────────
def _side(token, oi, vol=0.0, iv=None):
    return {"token": token, "oi": oi, "volume": vol, "iv": iv}


def _chain(ts, oi_shift=0.0):
    # Strikes 24300..24700; calls heaviest at 24700, puts heaviest at 24300.
    strikes = [24300, 24400, 24500, 24600, 24700]
    ce = [100.0, 200.0, 300.0, 400.0, 900.0]
    pe = [800.0, 400.0, 300.0, 200.0, 100.0]
    ivs = [(14.0, 16.5), (13.5, 15.6), (13.2, 14.9), (12.9, 14.4), (12.7, 14.0)]
    return {"ts": ts, "atm": 24500, "spot": 24510.0, "rows": [
        {"strike": k, "ce": _side(f"c{k}", c + oi_shift, 10.0, iv[0]), "pe": _side(f"p{k}", p, 30.0, iv[1])}
        for k, c, p, iv in zip(strikes, ce, pe, ivs)]}


def test_put_call_ratios_heaviest_strikes_and_atm_iv():
    a = market.chain_analytics(_chain("2026-11-06T10:00:00"), None)["analytics"]
    assert a["pcr_oi"] == round(1800 / 1900, 2) and a["pcr_volume"] == 3.0
    assert a["max_call_oi"] == 24700 and a["max_put_oi"] == 24300 and a["max_oi"] == 900.0
    assert a["atm_iv"] == round((13.2 + 14.9) / 2, 2)
    assert a["oi_change"] == {} and a["oi_since"] is None


def test_max_pain_is_where_holders_collect_least():
    a = market.chain_analytics(_chain("2026-11-06T10:00:00"), None)["analytics"]
    # Worked by hand: holders collect 2.0L at 24300, 1.1L at 24400, 0.8L at 24500, 1.1L at 24600, 2.0L at 24700.
    assert a["max_pain"] == 24500


def test_skew_compares_puts_and_calls_the_same_distance_out():
    wide = _chain("2026-11-06T10:00:00")
    for k, extra in ((24200, 24800), (24100, 24900)):
        wide["rows"].insert(0, {"strike": k, "ce": _side(f"c{k}", 1.0, 0, 15.0), "pe": _side(f"p{k}", 1.0, 0, 18.0)})
        wide["rows"].append({"strike": extra, "ce": _side(f"c{extra}", 1.0, 0, 12.0), "pe": _side(f"p{extra}", 1.0, 0, 13.0)})
    wide["rows"].sort(key=lambda r: r["strike"])
    s = market.chain_analytics(wide, None)["analytics"]["skew"]
    assert s == {"put_strike": 24200, "call_strike": 24800, "put_iv": 18.0, "call_iv": 12.0, "skew": 6.0}
    assert market.chain_analytics(_chain("2026-11-06T10:00:00"), None)["analytics"]["skew"] is None, "not enough strikes"


def test_oi_change_is_measured_from_the_days_first_reading(tmp_db):
    tmp_db.kv_set(K_CHAIN, {**_chain(_now_iso(5))})
    first = market.option_chain(tmp_db)
    assert first["analytics"]["oi_change"]["c24700"] == 0.0
    since = first["analytics"]["oi_since"]
    later = {**_chain(_now_iso(1), oi_shift=50.0)}
    later["rows"].append({"strike": 24800, "ce": _side("c24800", 10.0), "pe": _side("p24800", 10.0)})
    tmp_db.kv_set(K_CHAIN, later)
    a = market.option_chain(tmp_db)["analytics"]
    assert a["oi_change"]["c24700"] == 50.0 and a["oi_change"]["p24300"] == 0.0
    assert a["oi_since"] == since
    assert "c24800" not in a["oi_change"], "a strike first seen later has no change from the open to show"


def test_a_stale_chain_sets_no_baseline(tmp_db):
    tmp_db.kv_set(K_CHAIN, _chain(_now_iso(300)))
    market.option_chain(tmp_db)
    assert tmp_db.kv_get(market._open_key(_chain(_now_iso(300))), None) is None
