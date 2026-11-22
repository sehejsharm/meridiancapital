"""Greeks for each trade, and the Deck's metrics panel."""

from __future__ import annotations

import json
import math

import pytest

from app import desk_metrics as dm
from app import trade_greeks as tg
from shared.db import Database
from tests.test_desk import auth, client  # noqa: F401

TRADE = {"entry_ts": "2026-11-17T10:17:00", "exit_ts": "2026-11-17T11:54:00", "session_date": "2026-11-17",
         "mode": "live", "side": "CE", "strike": 25300, "expiry": "2026-11-24", "tsym": "NIFTY24NOV2625300CE",
         "lots": 1, "qty": 65, "entry_prem": 251.90, "exit_prem": 300.25, "spot_entry": 25420.0, "spot_exit": 25480.0,
         "gross": (300.25 - 251.90) * 65, "charges": 91.29, "net": (300.25 - 251.90) * 65 - 91.29, "reason": "STOPPED",
         "algo_id": "og"}


def test_greeks_at_entry_and_exit_and_the_split_of_the_result(tmp_path):
    db = Database(tmp_path / "g.db")
    g = tg.for_trade(db, TRADE)
    e, x = g["entry"], g["exit"]
    assert 0.5 < e["delta"] < 1 and 0.5 < x["delta"] < 1          # an in-the-money call
    assert e["theta"] < 0 and e["vega"] > 0 and e["gamma"] > 0 and 5 < e["iv"] < 60
    assert e["delta_position"] == round(e["delta"] * 65, 1) and e["dte"] == 7
    s = g["split"]
    assert s["nifty_points"] == 60.0
    assert s["nifty_move"] == pytest.approx((e["delta"] + x["delta"]) / 2 * 65 * 60, abs=0.01)
    assert s["time_decay"] < 0                                     # 97 minutes of decay costs money
    assert s["nifty_move"] + s["time_decay"] + s["volatility"] + s["rest"] == pytest.approx(s["gross"], abs=0.02)
    assert s["gross"] == pytest.approx(3142.75)


def test_a_missing_nifty_level_is_found_from_the_programs_status_or_left_blank(tmp_path):
    db = Database(tmp_path / "g.db")
    t = {**TRADE, "spot_entry": None, "spot_exit": None}
    assert tg.for_trade(db, t)["entry"] is None and tg.for_trade(db, t)["split"] is None   # never guessed
    db.add_equity_sample("2026-11-17", 43000, 0, 43000, 0, algo_id="og", spot=25418.0)
    with db.conn() as c:   # the sample a minute after entry
        c.execute("UPDATE equity_samples SET ts = '2026-11-17T10:18:00'")
    g = tg.for_trade(db, t)
    assert g["entry"]["spot"] == 25418.0 and g["exit"] is None    # the exit is 96 minutes away: no stand-in
    assert tg.brief(db, TRADE)["delta_in"] == tg.for_trade(db, TRADE)["entry"]["delta"]


def test_performance_ratios():
    trades = [{"mode": "live", "exit_ts": f"2026-11-{d:02d}T12:00", "net": n} for d, n in
              ((10, 1000.0), (11, -500.0), (12, 2000.0), (13, -1500.0), (14, 800.0), (17, 700.0))]
    p = dm.performance(trades)
    assert p["trades"] == 6 and p["win_rate"] == pytest.approx(4 / 6, abs=1e-4)
    assert p["profit_factor"] == pytest.approx(4500 / 2000, abs=0.01)
    assert p["expectancy"] == pytest.approx(2500 / 6, abs=0.01)
    assert p["max_drawdown"] == -1500.0                            # 2,500 high, then down to 1,000
    assert p["from_best"] == 0.0 and p["streak"] == {"kind": "win", "count": 2}
    assert dm.performance([]) == {"trades": 0}


def test_alpha_and_beta_against_nifty():
    # NIFTY rises 1% a day; the strategy makes 0.5% of its balance a day plus a fixed 0.2%: beta 0.5, alpha 0.2%/day.
    days = [f"2026-10-{d:02d}" for d in range(1, 31)]
    closes, level = {}, 25000.0
    for i, d in enumerate(days):
        level *= 1 + (0.01 if i % 2 else -0.004)
        closes[d] = level
    trades, opens = [], {}
    for i, d in enumerate(days[1:], start=1):
        m = closes[d] / closes[days[i - 1]] - 1
        opens[d] = 50000.0
        trades.append({"mode": "live", "exit_ts": d + "T12:00", "session_date": d, "net": 50000.0 * (0.5 * m + 0.002)})
    r = dm.against_market(trades, opens, closes)
    assert r["available"] and r["enough"] and r["days"] == 29
    assert r["beta"] == pytest.approx(0.5, abs=1e-3)
    assert r["alpha_daily"] == pytest.approx(0.002, abs=1e-6)
    assert r["alpha_annual"] == pytest.approx(0.504, abs=1e-3)
    assert r["correlation"] == pytest.approx(1.0, abs=1e-6)
    assert dm.against_market([], {}, closes)["available"] is False


def test_daily_closes_are_cached_and_survive_an_unreachable_feed():
    dm._cache.clear()
    calls = []

    def fetch(url):
        calls.append(url)
        return json.dumps({"chart": {"result": [{"timestamp": [1763337600, 1763424000],
                                                  "indicators": {"quote": [{"close": [25000.0, 25250.0]}]}}]}}).encode()
    a = dm.daily_closes("%5ENSEI", fetch)
    assert list(a.values()) == [25000.0, 25250.0] and len(calls) == 1
    dm.daily_closes("%5ENSEI", fetch)
    assert len(calls) == 1                                          # cached

    def down(url):
        raise OSError("no network")
    dm._cache["%5ENSEI"]["at"] = 0
    assert dm.daily_closes("%5ENSEI", down) == a                    # the last good closes stand
    # ...and the feed is tried again after a few minutes, not after the full six hours.
    assert dm.daily_closes("%5ENSEI", fetch) == a and len(calls) == 1    # within the retry wait: no call
    dm._cache["%5ENSEI"]["at"] -= dm.RETRY_SEC + 1
    dm.daily_closes("%5ENSEI", fetch)
    assert len(calls) == 2
    m = dm.market(None, {"analytics": {"pcr_oi": 0.86, "atm_iv": 12.4, "max_pain": 25350}}, fetch)
    assert m["nifty_change_pct"] == 1.0 and m["pcr_oi"] == 0.86 and m["max_pain"] == 25350
    dm._cache.clear()


def test_the_routes(client, auth, monkeypatch):  # noqa: F811
    from app.deps import ctx

    monkeypatch.setattr(dm, "_fetch", lambda url: (_ for _ in ()).throw(OSError("offline")))
    dm._cache.clear()
    db = ctx().db
    tid = db.add_trade(dict(TRADE))
    detail = client.get(f"/api/trades/{tid}/detail", headers=auth).json()
    assert detail["greeks"]["entry"]["delta"] > 0.5 and detail["greeks"]["split"]["nifty_points"] == 60.0
    rows = client.get("/api/trades", headers=auth).json()["trades"]
    assert rows[0]["greeks"]["delta_in"] == detail["greeks"]["entry"]["delta"]
    m = client.get("/api/desk/metrics", headers=auth).json()
    assert m["performance"]["trades"] == 1 and m["vs_nifty"]["available"] is False and "market" in m
    assert math.isfinite(m["performance"]["expectancy"])
    dm._cache.clear()
