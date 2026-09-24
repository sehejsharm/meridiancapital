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
