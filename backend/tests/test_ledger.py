"""Real-money figures reconciled to Angel One's ledger, to the paisa.

The numbers are the operator's own: on 12 Oct 2026 the Angel app showed an
opening ledger balance of Rs 45,291.21, premium received Rs 1,693.25, estimated
charges Rs 73.68 and a trading balance of Rs 46,910.77, while the dashboard
showed equity Rs 46,984 (charges left out) and booked the trade's charges as
Rs 80 (Angel's brokerage calculator). The balance lost Rs 73.69: the app's
estimate line is rounded down from a fraction of a paisa, and the balance
carries all of it — so the balance, not the estimate line, is the figure that
reconciles.
"""

from __future__ import annotations

import json

import pytest

from app import ledger

ALGO = "ganesh-kavach-og-real-3"


def trade(day, gross, charges, entry=194.25, exit_=220.30, mode="live", algo=ALGO, entry_ts=None):
    return {
        "entry_ts": entry_ts or f"{day}T10:30", "exit_ts": f"{day} 12:35", "session_date": day, "mode": mode,
        "view": "P", "side": "PE", "strike": 22500, "expiry": "2026-10-13", "tsym": "NIFTY13OCT2622500PE",
        "lots": 1, "qty": 65, "entry_prem": entry, "exit_prem": exit_, "gross": gross, "charges": charges,
        "net": round(gross - charges, 3), "reason": "SL@EXCH", "hold_min": 125, "algo_id": algo,
    }


def opening(db, day, balance, algo=ALGO):
    """The program's first equity sample of a session: equity - day_pl is the
    opening balance it read from Angel."""
    db.add_equity_sample(day, balance, 0.0, balance, 0.0, algo_id=algo)


def test_the_next_opening_balance_gives_angels_own_charges(tmp_db):
    opening(tmp_db, "2026-10-12", 45291.21)
    tmp_db.add_equity_sample("2026-10-12", 46984.46, 1693.25, 51099.0, 1693.25, algo_id=ALGO)  # v7: gross
    opening(tmp_db, "2026-10-13", 46910.77)
    tmp_db.add_trade(trade("2026-10-12", 1693.25, 80.0))
    [t] = ledger.reconcile(tmp_db, tmp_db.trades())
    assert t["reconciled"] == ledger.LEDGER
    assert t["charges_angel"] == 73.69 and t["net_angel"] == 1619.56
    assert t["charges"] == 80.0, "the recorded figure stays beside Angel's"


def test_a_deposit_is_never_booked_as_charges(tmp_db):
    opening(tmp_db, "2026-10-12", 45291.21)
    opening(tmp_db, "2026-10-13", 56910.77)          # Rs 10,000 added overnight
    tmp_db.add_trade(trade("2026-10-12", 1693.25, 80.0))
    [t] = ledger.reconcile(tmp_db, tmp_db.trades())
    assert t["reconciled"] == ledger.RECORDED
    assert t["charges_angel"] == 80.0 and t["net_angel"] == pytest.approx(1613.25)
    assert t["ledger_gap"] == pytest.approx(10006.31), "the money that moved outside trading is reported"


def test_a_v8_trade_carries_the_charges_angels_balance_lost(tmp_db, tmp_path):
    opening(tmp_db, "2026-10-12", 45291.21)        # no next session yet
    tmp_db.add_trade(trade("2026-10-12", 1693.25, 73.69))
    folder = tmp_path / ALGO
    folder.mkdir()
    (folder / "gk50k_money.jsonl").write_text(json.dumps({
        "session_date": "2026-10-12", "entry_ts": "2026-10-12T10:30", "gross": 1693.25,
        "charges": 73.69, "charges_source": "Angel trading balance", "charges_angel_balance": 73.69,
        "balance_after": 46910.77, "mode": "live"}) + "\n")
    [t] = ledger.reconcile(tmp_db, tmp_db.trades(), base=tmp_path)
    assert t["reconciled"] == ledger.BALANCE and t["net_angel"] == 1619.56


def test_without_angels_figure_yet_the_trade_says_so(tmp_db, tmp_path):
    opening(tmp_db, "2026-10-12", 45291.21)
    tmp_db.add_trade(trade("2026-10-12", 1693.25, 80.0))
    [t] = ledger.reconcile(tmp_db, tmp_db.trades(), base=tmp_path)
    assert t["reconciled"] == ledger.RECORDED and t["net_angel"] == pytest.approx(1613.25)


def test_headline_figures_are_real_money_only_and_from_angel(tmp_db):
    opening(tmp_db, "2026-10-09", 44013.00)
    opening(tmp_db, "2026-10-12", 45291.21)
    opening(tmp_db, "2026-10-13", 46910.77)
    tmp_db.add_trade(trade("2026-10-09", 1417.00, 74.0, entry=159.40, exit_=181.20))
    tmp_db.add_trade(trade("2026-10-12", 1693.25, 80.0))
    tmp_db.add_trade(trade("2026-10-12", -5000.0, 70.0, mode="paper", algo="og-test", entry_ts="2026-10-12T11:00"))
    s = ledger.stats(ledger.reconcile(tmp_db, tmp_db.trades()))
    assert s["n"] == 2 and s["scope"] == "real money"
    # 9 Oct: 45,291.21 - 44,013.00 = 1,278.21 kept; 12 Oct: 1,619.56 kept
    assert s["net"] == pytest.approx(1278.21 + 1619.56)
    assert s["charges"] == pytest.approx((1417.00 - 1278.21) + 73.69)
    assert s["reconciled"] == 2


def test_daily_closing_balances_are_the_next_mornings_opening(tmp_db):
    opening(tmp_db, "2026-10-09", 44013.00)
    opening(tmp_db, "2026-10-12", 45291.21)
    tmp_db.add_equity_sample("2026-10-12", 47446.0, 2155.0, 51099.0, 2155.0, algo_id=ALGO)  # mid-trade
    tmp_db.add_equity_sample("2026-10-12", 46984.46, 1693.25, 51099.0, 1693.25, algo_id="other")
    out = ledger.closing_balances(tmp_db, ALGO, live_balance=46910.77, today="2026-10-12")
    assert out == [
        {"session_date": "2026-10-09", "equity": 45291.21, "source": ledger.LEDGER},
        {"session_date": "2026-10-12", "equity": 46910.77, "source": ledger.BALANCE},
    ]


def test_the_real_money_algorithm_is_found(tmp_db):
    tmp_db.add_trade(trade("2026-10-12", -100.0, 70.0, mode="paper", algo="og-test"))
    tmp_db.add_trade(trade("2026-10-12", 1693.25, 80.0))
    assert ledger.live_algo(tmp_db) == ALGO


def test_a_report_counts_one_account_and_draws_one_curve(tmp_db):
    from app import reports

    opening(tmp_db, "2026-10-09", 44013.00)
    opening(tmp_db, "2026-10-12", 45291.21)
    opening(tmp_db, "2026-10-13", 46910.77)
    tmp_db.add_equity_sample("2026-10-12", 39000.0, -1000.0, 40000.0, -1000.0, algo_id="og-test")
    tmp_db.add_trade(trade("2026-10-09", 1417.00, 74.0, entry=159.40, exit_=181.20))
    tmp_db.add_trade(trade("2026-10-12", 1693.25, 80.0))
    tmp_db.add_trade(trade("2026-10-08", -1950.0, 60.0, mode="paper", algo="og-test", entry_ts="2026-10-08T11:00"))
    p = reports.build_payload(tmp_db, None, "2026-10-01", "2026-10-12", "t")
    s = p["summary"]
    assert s["scope"] == "real money" and s["not_counted"] == 1 and s["trades"] == 2
    assert s["net_pnl"] == pytest.approx(1278.21 + 1619.56), "the paper trade stays out of real money"
    assert {e["equity"] for e in p["equity"]} == {44013.00, 45291.21}, "no other account's samples on the curve"
    assert [t["session_date"] for t in p["trades"]] == ["2026-10-12", "2026-10-09", "2026-10-08"]
