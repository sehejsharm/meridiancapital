"""The dashboard's chart and option chain, as an engine publishes them."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pandas as pd
import pytest

from engine.greeks import price, years_to_expiry
from engine.market_feed import K_BARS, K_CHAIN, K_OWNER, MarketFeed, chart_time

NOW = datetime(2026, 9, 10, 11, 0)            # a Thursday, mid-session
EXPIRY = date(2026, 9, 15)                    # 5 days out: inside the strategy's window
SPOT = 25_012.0
VOL = 0.13
STRIKES = range(24_500, 25_550, 50)


def _table():
    t = {}
    for i, k in enumerate(STRIKES):
        for r in ("CE", "PE"):
            t[(EXPIRY, k, r)] = (f"NIFTY15SEP26{k}{r}", f"{k}{0 if r == 'CE' else 1}", 65)
    return t


class FakeBroker:
    def __init__(self, held=None):
        self.held = held or []
        self.quote_calls = 0
        df_ts = [NOW - timedelta(minutes=n) for n in range(120, 0, -1)]
        self._bars = {"df": pd.DataFrame({"ts": pd.to_datetime(df_ts), "o": 25000.0, "h": 25010.0,
                                           "l": 24990.0, "close": 25005.0, "v": 0})}

    def ltp(self, exch, tsym, token):
        return SPOT

    def open_positions(self):
        return self.held

    def quotes(self, exch, tokens):
        self.quote_calls += 1
        t = years_to_expiry(NOW, EXPIRY)
        out = {}
        for tok in tokens:
            k, r = int(tok[:-1]), ("CE" if tok[-1] == "0" else "PE")
            out[tok] = {"ltp": round(price(SPOT, k, t, VOL, r), 2), "volume": 1000.0, "oi": 5000.0,
                        "bid": None, "ask": None, "change": 0.0, "change_pct": 0.0, "high": None, "low": None}
        return out


class FakeEngine:
    def __init__(self, db, held=None, pos=None, algo_id="gk50k"):
        self.db, self.algo_id, self.pos = db, algo_id, pos
        self.br = FakeBroker(held)
        self.table = _table()
        self.last_spot, self.last_spot_at = SPOT, __import__("time").time()


def test_the_chain_is_centred_on_the_money_with_greeks_that_recover_the_vol(tmp_db):
    feed = MarketFeed(FakeEngine(tmp_db))
    assert feed.tick(NOW) is True
    chain = tmp_db.kv_get(K_CHAIN)
    assert chain["atm"] == 25_000 and chain["expiry"] == "2026-09-15"
    assert len(chain["rows"]) == 17                      # eight either side
    atm = next(r for r in chain["rows"] if r["strike"] == 25_000)
    assert atm["ce"]["iv"] == pytest.approx(VOL * 100, abs=0.2)
    assert 0.4 < atm["ce"]["delta"] < 0.65 and -0.6 < atm["pe"]["delta"] < -0.35
    assert atm["ce"]["volume"] == 1000.0 and atm["ce"]["oi"] == 5000.0


def test_the_whole_chain_costs_one_quote_call(tmp_db):
    eng = FakeEngine(tmp_db)
    MarketFeed(eng).tick(NOW)
    assert eng.br.quote_calls == 1


def test_a_contract_the_account_holds_is_highlighted_even_if_a_program_opened_it(tmp_db):
    held = [{"tradingsymbol": "NIFTY15SEP2624950CE", "symboltoken": "249500", "netqty": "65"}]
    MarketFeed(FakeEngine(tmp_db, held=held)).tick(NOW)
    h = tmp_db.kv_get(K_CHAIN)["highlight"]
    assert h["source"] == "account" and h["strike"] == 24_950 and h["right"] == "CE" and h["qty"] == 65


def test_the_engines_own_position_wins(tmp_db):
    pos = {"tsym": "NIFTY15SEP2625050PE", "token": "250501", "qty": 130}
    MarketFeed(FakeEngine(tmp_db, pos=pos)).tick(NOW)
    h = tmp_db.kv_get(K_CHAIN)["highlight"]
    assert h["source"] == "engine" and h["strike"] == 25_050 and h["right"] == "PE"


def test_nothing_is_published_outside_market_hours(tmp_db):
    eng = FakeEngine(tmp_db)
    assert MarketFeed(eng).tick(datetime(2026, 9, 10, 20, 0)) is False
    assert eng.br.quote_calls == 0 and tmp_db.kv_get(K_CHAIN) is None


def test_only_one_engine_publishes_at_a_time(tmp_db):
    a, b = FakeEngine(tmp_db, algo_id="a"), FakeEngine(tmp_db, algo_id="b")
    assert MarketFeed(a).tick(NOW) is True
    assert MarketFeed(b).tick(NOW) is False
    assert b.br.quote_calls == 0
    assert tmp_db.kv_get(K_OWNER)["algo"] == "a"


def test_todays_candles_are_published_in_indian_time(tmp_db):
    MarketFeed(FakeEngine(tmp_db)).tick(NOW)
    bars = tmp_db.kv_get(K_BARS)["bars"]
    assert len(bars) == 120
    last = datetime(1970, 1, 1) + timedelta(seconds=bars[-1]["t"])
    assert last == NOW - timedelta(minutes=1), "the chart axis must read IST wall-clock"


def test_chart_time_reads_ist_as_wall_clock():
    assert chart_time(datetime(2026, 9, 10, 9, 15)) % 86_400 == 9 * 3600 + 15 * 60


def test_a_feed_failure_never_escapes_the_thread(tmp_db, monkeypatch):
    """Angel failing on every quote must cost the dashboard its chain and
    nothing else: the thread keeps running and counts the errors."""
    import time

    import engine.market_feed as mf

    monkeypatch.setattr(mf, "FEED_INTERVAL", 0.01)
    monkeypatch.setattr(mf, "now_ist", lambda: NOW)
    eng = FakeEngine(tmp_db)

    def boom(*a):
        raise RuntimeError("angel down")

    eng.br.quotes = boom
    feed = MarketFeed(eng)
    feed.start()
    deadline = time.time() + 5
    while feed.errors < 3 and time.time() < deadline:
        time.sleep(0.01)
    alive = feed._thread.is_alive()
    feed.stop()
    assert feed.errors >= 3 and alive
    assert "angel down" in feed.last_error
