"""The weekly and monthly tear sheet (app/tearsheet.py)."""

from __future__ import annotations

import pytest

from app import tearsheet
from tests.test_desk import auth, client  # noqa: F401  (fixtures)


def trade(db, day: str, net: float, reason: str = "trail", mode: str = "live", hold: float = 30.0) -> None:
    db.add_trade({"entry_ts": f"{day} 10:00", "exit_ts": f"{day} 10:30", "session_date": day, "mode": mode,
                  "side": "CE", "strike": 24500, "tsym": "NIFTY10NOV2624500CE", "qty": 65, "lots": 1,
                  "entry_prem": 100.0, "exit_prem": 100.0 + (net + 50) / 65, "gross": net + 50, "charges": 50.0,
                  "net": net, "reason": reason, "hold_min": hold, "algo_id": "og"})


def test_a_week_runs_monday_to_sunday_and_a_month_is_the_calendar_month():
    assert tearsheet.period_bounds("week", "2026-11-06") == ("2026-11-02", "2026-11-08", "Week of 2 Nov 2026")
    assert tearsheet.period_bounds("week", "2026-11-02")[0] == "2026-11-02"
    assert tearsheet.period_bounds("month", "2026-11-06") == ("2026-11-01", "2026-11-30", "November 2026")
    assert tearsheet.period_bounds("month", "2028-02-10")[1] == "2028-02-29"
    with pytest.raises(ValueError):
        tearsheet.period_bounds("year", "2026-11-06")


def test_sharpe_and_sortino_on_daily_results():
    r = tearsheet.ratios([1000.0, -500.0, 2000.0])
    # mean 833.33; sample sd 1258.31; downside sd sqrt(500^2 / 3) = 288.68; x sqrt(252) = 10.51 and 45.826
    assert r["sharpe"] == 10.51 and r["sortino"] == 45.83
    assert r["days"] == 3 and r["few_days"]
    assert tearsheet.ratios([500.0])["sharpe"] is None
    assert tearsheet.ratios([500.0, 700.0])["sortino"] is None, "no losing day: no downside to divide by"


def test_the_week_in_numbers(tmp_db):
    tmp_db.upsert_algo("og", "ganesh_kavach_og_real (3)")
    trade(tmp_db, "2026-11-02", 1000.0)
    trade(tmp_db, "2026-11-04", -800.0, reason="stop", hold=12.0)
    trade(tmp_db, "2026-11-06", 3847.64)
    trade(tmp_db, "2026-11-06", -100.0, mode="paper")
    trade(tmp_db, "2026-10-30", 9999.0)  # the Friday before: not this week
    s = tearsheet.build(tmp_db, "og", "week", "2026-11-06")
    assert s["name"] == "ganesh_kavach_og_real (3)" and s["label"] == "Week of 2 Nov 2026"
    assert s["summary"]["trades"] == 3 and s["summary"]["net_pnl"] == 4047.64
    assert [d["day"] for d in s["daily"]] == ["2026-11-02", "2026-11-04", "2026-11-06"]
    e = s["extra"]
    assert e["green_days"] == 2 and e["red_days"] == 1
    assert e["best_day"] == {"day": "2026-11-06", "net": 3847.64}
    assert e["worst_day"] == {"day": "2026-11-04", "net": -800.0}
    assert e["avg_loss"] == -800.0 and e["avg_win"] == 2423.82
    assert e["exit_reasons"] == [{"reason": "trail", "n": 2}, {"reason": "stop", "n": 1}]
    assert e["avg_hold_min"] == 24.0
    assert len(s["trades"]) == 3, "the paper trade is not in a real-money sheet"


def test_the_pdf_draws_for_a_busy_month_and_an_empty_week(tmp_db):
    for i in range(45):
        trade(tmp_db, f"2026-11-{2 + i % 20:02d}", 500.0 if i % 3 else -400.0)
    month = tearsheet.to_pdf(tearsheet.build(tmp_db, None, "month", "2026-11-06"))
    assert month.startswith(b"%PDF-1.4") and month.rstrip().endswith(b"%%EOF")
    assert month.count(b"/Type /Page ") == 2, "45 trades run onto a second page"
    assert b" re f" in month and b" l S" in month, "bars and the curve are drawn"
    empty = tearsheet.to_pdf(tearsheet.build(tmp_db, None, "week", "2026-12-07"))
    assert b"No closed trades in this period." in empty


def test_the_api(client, auth):  # noqa: F811
    assert client.get("/api/tearsheet", params={"period": "year"}, headers=auth).status_code == 400
    assert client.get("/api/tearsheet", params={"date": "06-11-2026"}, headers=auth).status_code == 400
    r = client.get("/api/tearsheet.pdf", params={"period": "week", "date": "2026-11-06"}, headers=auth)
    assert r.status_code == 200 and r.content.startswith(b"%PDF")
    assert "meridian-tearsheet-week-2026-11-02-all.pdf" in r.headers["content-disposition"]
