"""The financial-year P&L for the CA (app/tax.py), checked by hand."""

from __future__ import annotations

from datetime import datetime

import pytest

from app import tax
from tests.test_desk import auth, client  # noqa: F401  (fixtures)


def trade(db, entry: str, exit_: str | None, entry_prem: float, exit_prem: float | None, charges: float,
          mode: str = "live", qty: int = 65, tsym: str = "NIFTY10NOV2624500CE") -> None:
    gross = None if exit_prem is None else round((exit_prem - entry_prem) * qty, 2)
    db.add_trade({"entry_ts": entry, "exit_ts": exit_, "session_date": entry[:10], "mode": mode, "side": "CE",
                  "strike": 24500, "tsym": tsym, "qty": qty, "lots": qty // 65, "entry_prem": entry_prem,
                  "exit_prem": exit_prem, "gross": gross, "charges": charges,
                  "net": None if gross is None else round(gross - charges, 2), "algo_id": "og"})


@pytest.fixture
def year(tmp_db, monkeypatch):
    monkeypatch.setattr(tax, "now_ist", lambda: datetime(2026, 11, 6, 16, 0))
    # Closed on 31 March: last year's.
    trade(tmp_db, "2026-03-31 10:00", "2026-03-31 11:00", 100.0, 120.0, 50.0)
    # Bought 31 March, sold 1 April: this year's (the year it was closed in). +1,300 gross.
    trade(tmp_db, "2026-03-31 14:00", "2026-04-01 10:00", 100.0, 120.0, 60.0)
    # A June loss: -1,950 gross.
    trade(tmp_db, "2026-06-10 10:00", "2026-06-10 11:00", 150.0, 120.0, 40.0)
    # 6 Nov: +3,962.40 gross, 114.76 charges.
    trade(tmp_db, "2026-11-06 12:14", "2026-11-06 13:16", 150.0, 210.96, 114.76)
    # Paper beside it, never counted; and a live position still open.
    trade(tmp_db, "2026-11-06 12:14", "2026-11-06 13:16", 150.0, 300.0, 10.0, mode="paper")
    trade(tmp_db, "2026-11-06 14:00", None, 150.0, None, 0.0)
    return tmp_db


def test_financial_years_run_april_to_march():
    assert tax.fy_of("2026-11-06") == "2026-27"
    assert tax.fy_of("2026-03-31") == "2025-26"
    assert tax.fy_of("2026-04-01") == "2026-27"
    assert tax.fy_bounds("2026-27") == ("2026-04-01", "2027-03-31")
    assert tax.fy_bounds("2099-00") == ("2099-04-01", "2100-03-31")
    for bad in ("2026", "2026-28", "26-27", "", "2026-2027"):
        with pytest.raises(ValueError):
            tax.fy_bounds(bad)


def test_the_year_counts_real_money_trades_closed_in_it(year):
    r = tax.build(year, "2026-27")
    t = r["totals"]
    assert t["trades"] == 3 and t["wins"] == 2 and t["losses"] == 1
    assert t["gross_profit"] == 1300.0 + 3962.4
    assert t["gross_loss"] == 1950.0
    assert t["gross_pnl"] == round(1300.0 - 1950.0 + 3962.4, 2)
    assert t["charges"] == round(60.0 + 40.0 + 114.76, 2)
    assert t["net_pnl"] == round(t["gross_pnl"] - t["charges"], 2)
    # ICAI: the sum of absolute profits and losses, premium on sale not added.
    assert t["turnover"] == round(1300.0 + 1950.0 + 3962.4, 2)
    assert t["sell_value"] == round((120.0 + 120.0 + 210.96) * 65, 2)
    assert r["open_positions"] == 1
    assert [row["exit_date"] for row in r["trades"]] == ["2026-04-01", "2026-06-10", "2026-11-06"]
    assert all(row["net"] == round(row["gross"] - row["charges"], 2) for row in r["trades"])
    assert [m["month"] for m in r["months"]] == ["2026-04", "2026-06", "2026-11"]


def test_last_years_trade_stays_in_last_year(year):
    r = tax.build(year, "2025-26")
    assert r["totals"]["trades"] == 1 and r["totals"]["gross_pnl"] == 1300.0
    assert r["years"] == ["2026-27", "2025-26"]


def test_advance_tax_dates_show_realised_pnl_to_each(year):
    inst = tax.build(year, "2026-27")["instalments"]
    assert [(i["label"], i["due"], i["cumulative_pct"]) for i in inst] == [
        ("15 Jun", "2026-06-15", 15), ("15 Sep", "2026-09-15", 45),
        ("15 Dec", "2026-12-15", 75), ("15 Mar", "2027-03-15", 100)]
    to_jun = round(1300.0 - 60.0 - 1950.0 - 40.0, 2)
    assert inst[0]["net_to_date"] == to_jun and inst[0]["passed"]
    assert inst[1]["net_in_window"] == 0.0 and inst[1]["net_to_date"] == to_jun
    assert inst[2]["net_in_window"] == round(3962.4 - 114.76, 2) and not inst[2]["passed"]
    assert inst[3]["net_to_date"] == round(to_jun + 3962.4 - 114.76, 2)


def test_the_notes_say_what_to_check(year):
    notes = " ".join(tax.build(year, "2026-27")["notes"])
    assert "legal record" in notes and "ICAI" in notes
    assert "1 position(s) opened this year are still open" in notes
    assert "charges the program recorded" in notes  # no Angel ledger in the test
    assert "not over" in notes


def test_csv_and_pdf_carry_the_same_year(year):
    r = tax.build(year, "2026-27")
    text = tax.to_csv(r)
    assert "Turnover (abs P&L)" in text and "NOTES" in text
    assert text.count("NIFTY10NOV2624500CE") == 3  # the paper and last year's trades are not in it
    doc = tax.to_pdf(r)
    assert doc.startswith(b"%PDF-1.4") and doc.rstrip().endswith(b"%%EOF")
    assert b"F&O trading P&L, FY 2026-27" in doc and b"page 1 of" in doc


def test_a_long_year_paginates(tmp_db, monkeypatch):
    monkeypatch.setattr(tax, "now_ist", lambda: datetime(2026, 11, 6, 16, 0))
    for i in range(120):
        trade(tmp_db, f"2026-07-{1 + i % 28:02d} 10:00", f"2026-07-{1 + i % 28:02d} 11:00", 100.0, 101.0, 5.0)
    doc = tax.to_pdf(tax.build(tmp_db, "2026-27"))
    assert doc.count(b"/Type /Page ") >= 3


def test_the_api_validates_the_year_and_downloads(client, auth):  # noqa: F811
    assert client.get("/api/tax", params={"fy": "2026-28"}, headers=auth).status_code == 400
    r = client.get("/api/tax", params={"fy": "2026-27"}, headers=auth)
    assert r.status_code == 200 and r.json()["fy"] == "2026-27"
    r = client.get("/api/tax.pdf", params={"fy": "2026-27"}, headers=auth)
    assert r.status_code == 200 and r.content.startswith(b"%PDF")
    assert 'filename="meridian-tax-pnl-FY2026-27.pdf"' in r.headers["content-disposition"]
    r = client.get("/api/tax.csv", params={"fy": "2026-27"}, headers=auth)
    assert r.status_code == 200 and r.text.startswith("\"F&O trading P&L, financial year 2026-27")
    assert client.get("/api/tax").status_code == 401
