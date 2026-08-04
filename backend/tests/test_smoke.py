"""Smoke tests — run with:  python -m tests.test_smoke   (from backend/)

Covers the parts that must not break silently: date-range maths for exports,
the trade/session persistence path the supervisor uses, every export format,
and the authenticated API surface. The algorithm itself is not exercised here
because it needs a live Angel One session.
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

TMP = tempfile.mkdtemp(prefix="meridian-test-")
os.environ.update({
    "DATA_DIR": TMP,
    "API_TOKEN": "test-token-123",
    "PAPER_MODE": "true",
    "TZ": "Asia/Kolkata",
    "AUTO_SCHEDULE": "false",
    "ANGEL_API_KEY": "x", "ANGEL_CLIENT_ID": "x",
    "ANGEL_PASSWORD": "x", "ANGEL_TOTP_SECRET": "x",
})

from app import db, exports  # noqa: E402
from app.config import settings  # noqa: E402

PASS, FAIL = 0, 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  \033[32mPASS\033[0m  {label}")
    else:
        FAIL += 1
        print(f"  \033[31mFAIL\033[0m  {label}  {detail}")


# ---------------------------------------------------------------- ranges

def test_ranges() -> None:
    print("\nDate range resolution (anchor 2026-08-04, a Tuesday)")
    a = "2026-08-04"
    cases = [
        ("day",     ("2026-08-04", "2026-08-04")),
        ("week",    ("2026-08-03", "2026-08-09")),   # Mon..Sun
        ("month",   ("2026-08-01", "2026-08-31")),
        ("quarter", ("2026-07-01", "2026-09-30")),   # Q3
        ("year",    ("2026-01-01", "2026-12-31")),
    ]
    for period, (want_s, want_e) in cases:
        s, e, label = exports.resolve_range(period, a)
        check(f"{period:8s} -> {s} .. {e}  ({label})",
              (s, e) == (want_s, want_e), f"expected {want_s}..{want_e}")

    s, e, _ = exports.resolve_range("custom", None, "2026-08-10", "2026-08-01")
    check("custom range swaps reversed dates", (s, e) == ("2026-08-01", "2026-08-10"))

    # Quarter boundaries in every quarter.
    for anchor, want in [("2026-01-15", ("2026-01-01", "2026-03-31")),
                         ("2026-05-20", ("2026-04-01", "2026-06-30")),
                         ("2026-11-30", ("2026-10-01", "2026-12-31"))]:
        s, e, _ = exports.resolve_range("quarter", anchor)
        check(f"quarter containing {anchor}", (s, e) == want, f"got {s}..{e}")

    # Leap-year February must not lose a day.
    s, e, _ = exports.resolve_range("month", "2028-02-10")
    check("Feb 2028 (leap) ends on the 29th", e == "2028-02-29", f"got {e}")


# ---------------------------------------------------------------- storage

def seed() -> None:
    db.init(settings.db_path)
    trades = [
        dict(session_date="2026-08-04", mode="PAPER", entry_time="2026-08-04 09:52:11",
             exit_time="10:41:03", hold_min=48.9, symbol="NIFTY07AUG2624500CE",
             opt_type="C", strike=24500, qty=75, avg_entry=142.5, exit_fill=178.2,
             gross_pnl=2677.5, brokerage=40.0, stt=6.68, exch_txn=12.03, sebi=0.02,
             stamp=0.32, gst=9.37, charges=68.42, net_pnl=2609.08,
             reason="LOCK1_10PCT", stage="LOCK1",
             entry_reason="CE — EMA9 above EMA21, spot above VWAP, GARCH 12.4%",
             spot_at_entry=24512.3, garch_vol=0.124, entry_iv=0.131,
             model_prem=138.9, lot_cost=10687.5, real_margin=10687.5,
             risk_rs=1068.75, day_pnl=2609.08, equity_after=22609.08, latency_ms=214.0),
        dict(session_date="2026-08-04", mode="PAPER", entry_time="2026-08-04 11:20:44",
             exit_time="11:58:12", hold_min=37.5, symbol="NIFTY07AUG2624450PE",
             opt_type="P", strike=24450, qty=75, avg_entry=118.0, exit_fill=106.2,
             gross_pnl=-885.0, brokerage=40.0, stt=3.98, exch_txn=8.41, sebi=0.02,
             stamp=0.27, gst=8.72, charges=61.40, net_pnl=-946.40,
             reason="STOP_LOSS", stage="INIT",
             entry_reason="PE — EMA9 below EMA21, spot below VWAP, GARCH 11.8%",
             spot_at_entry=24438.1, garch_vol=0.118, entry_iv=0.126,
             model_prem=121.4, lot_cost=8850.0, real_margin=8850.0,
             risk_rs=885.0, day_pnl=1662.68, equity_after=21662.68, latency_ms=198.0),
        dict(session_date="2026-08-05", mode="PAPER", entry_time="2026-08-05 10:05:00",
             exit_time="12:30:00", hold_min=145.0, symbol="NIFTY07AUG2624600CE",
             opt_type="C", strike=24600, qty=75, avg_entry=95.0, exit_fill=142.0,
             gross_pnl=3525.0, brokerage=40.0, stt=5.33, exch_txn=8.89, sebi=0.02,
             stamp=0.21, gst=8.81, charges=63.26, net_pnl=3461.74,
             reason="TRAIL_FREE_RUN", stage="FREE",
             entry_reason="CE — strong trend stack", spot_at_entry=24590.0,
             garch_vol=0.141, entry_iv=0.138, model_prem=92.0, lot_cost=7125.0,
             real_margin=7125.0, risk_rs=712.5, day_pnl=3461.74,
             equity_after=25124.42, latency_ms=203.0),
    ]
    for t in trades:
        db.upsert_trade(t)

    db.upsert_session(dict(
        session_date="2026-08-04", mode="PAPER", trades=2, open_equity=20000.0,
        close_equity=21662.68, day_pnl=1662.68, charges=129.82, killed=0,
        chop_blocked=0, chop_score=0.62, garch=12.4, adx=23.1,
        vol_regime="TRADEABLE", trend="TRENDING", direction="UP",
        efficiency="DIRECTIONAL", day_range_pts=186.0, avg_latency_ms=206.0,
        peak_equity=22609.08, drawdown_pct=-4.19, win_rate=50.0, profit_factor=2.76))
    db.upsert_session(dict(
        session_date="2026-08-05", mode="PAPER", trades=1, open_equity=21662.68,
        close_equity=25124.42, day_pnl=3461.74, charges=63.26, killed=0,
        chop_blocked=0, chop_score=0.71, garch=14.1, adx=27.5,
        vol_regime="TRADEABLE", trend="STRONG TREND", direction="UP",
        efficiency="DIRECTIONAL", day_range_pts=241.0, avg_latency_ms=203.0,
        peak_equity=25124.42, drawdown_pct=0.0, win_rate=100.0, profit_factor=None))
    db.upsert_session(dict(
        session_date="2026-08-06", mode="PAPER", trades=0, open_equity=25124.42,
        close_equity=25124.42, day_pnl=0.0, charges=0.0, killed=0,
        chop_blocked=1, chop_score=0.09, garch=7.2, adx=11.0,
        vol_regime="QUIET", trend="NO TREND / CHOP", direction="SIDEWAYS",
        efficiency="CHOPPY", day_range_pts=64.0, avg_latency_ms=210.0,
        peak_equity=25124.42, drawdown_pct=0.0))

    db.insert_event("2026-08-04T09:15:02", "2026-08-04", "boot",
                    "Bot starting — PAPER TRADING", "success", {"paper": True})
    db.insert_event("2026-08-04T09:52:11", "2026-08-04", "entry",
                    "BOUGHT 24500CE @ Rs142.50", "success", {"strike": 24500})
    db.insert_equity_mark("2026-08-04T10:00:00", "2026-08-04", 20000.0, 0.0, True, 420.0)
    db.insert_equity_mark("2026-08-04T11:00:00", "2026-08-04", 22609.08, 2609.08, False)


def test_storage() -> None:
    print("\nPersistence")
    check("trades stored", len(db.trades_between("2026-08-01", "2026-08-31")) == 3)
    check("single-day query isolates the day",
          len(db.trades_between("2026-08-05", "2026-08-05")) == 1)
    check("sessions stored", len(db.sessions_between("2026-08-01", "2026-08-31")) == 3)
    check("events stored", len(db.recent_events(limit=50)) >= 2)
    check("equity marks stored", len(db.equity_marks("2026-08-04")) == 2)

    # Re-inserting the same trade must not duplicate it.
    before = len(db.trades_between("2026-08-04", "2026-08-04"))
    db.upsert_trade(dict(session_date="2026-08-04", entry_time="2026-08-04 09:52:11",
                         symbol="NIFTY07AUG2624500CE", net_pnl=2609.08))
    check("re-inserting a trade is idempotent",
          len(db.trades_between("2026-08-04", "2026-08-04")) == before)


def test_aggregate() -> None:
    print("\nAggregation")
    agg = db.aggregate("2026-08-01", "2026-08-31")
    check(f"trade count = 3 (got {agg['trades']})", agg["trades"] == 3)
    check(f"wins = 2 (got {agg['wins']})", agg["wins"] == 2)
    check(f"win rate ≈ 66.7 (got {agg['win_rate']:.1f})", abs(agg["win_rate"] - 66.667) < 0.1)
    expected_net = round(2609.08 - 946.40 + 3461.74, 2)
    check(f"net P&L = {expected_net} (got {agg['net_pnl']})",
          abs(agg["net_pnl"] - expected_net) < 0.01)
    check(f"profit factor ≈ 6.42 (got {agg['profit_factor']})",
          abs(agg["profit_factor"] - 6.42) < 0.05)
    check("chop-blocked day counted", agg["days_blocked_chop"] == 1)
    check(f"return % computed (got {agg['return_pct']})", agg["return_pct"] > 25)
    check("max drawdown is negative or zero", agg["max_drawdown_pct"] <= 0)

    empty = db.aggregate("2025-01-01", "2025-01-31")
    check("empty range does not divide by zero", empty["trades"] == 0 and empty["win_rate"] == 0)


def test_exports() -> None:
    print("\nExport formats")
    s, e, label = exports.resolve_range("month", "2026-08-04")
    payload = exports.build_payload(s, e, label, include_events=True)

    csv_text = exports.to_csv(payload)
    check("CSV has a summary block", "SUMMARY" in csv_text)
    check("CSV has trades", "NIFTY07AUG2624500CE" in csv_text)
    check("CSV has daily sessions", "DAILY SESSIONS" in csv_text)
    check("CSV has the event log", "EVENT LOG" in csv_text)

    js = exports.to_json(payload)
    check("JSON parses", js.strip().startswith("{") and '"trades"' in js)

    xlsx = exports.to_xlsx(payload)
    check(f"XLSX produced ({len(xlsx):,} bytes)", len(xlsx) > 4000 and xlsx[:2] == b"PK")

    name = exports.filename("month", s, e, "csv")
    check(f"filename sensible ({name})", name.endswith(".csv") and "2026-08-01" in name)


def test_api() -> None:
    print("\nAPI")
    from fastapi.testclient import TestClient
    from app.main import app

    with TestClient(app) as client:
        r = client.get("/api/health")
        check("health is open", r.status_code == 200 and r.json()["ok"])

        r = client.get("/api/status")
        check("status refuses without a token", r.status_code == 401)

        r = client.get("/api/status", headers={"X-API-Token": "wrong"})
        check("status refuses a wrong token", r.status_code == 401)

        h = {"X-API-Token": "test-token-123"}
        r = client.get("/api/status", headers=h)
        check("status accepts the right token", r.status_code == 200)
        body = r.json()
        check("status reports the bot stopped", body["state"] in ("stopped", "error"))
        check("status carries the schedule", "schedule" in body)
        check("status never leaks the TOTP secret", "totp" not in r.text.lower())

        r = client.get("/api/summary?period=month&anchor=2026-08-04", headers=h)
        check("summary endpoint works",
              r.status_code == 200 and r.json()["summary"]["trades"] == 3)

        r = client.get("/api/trades?period=day&anchor=2026-08-05", headers=h)
        check("single-day trades endpoint", r.status_code == 200 and r.json()["count"] == 1)

        for fmt, sig in (("csv", b"MERIDIAN"), ("json", b"{"), ("xlsx", b"PK")):
            r = client.get(f"/api/export?period=month&anchor=2026-08-04&format={fmt}",
                           headers=h)
            check(f"export {fmt} downloads",
                  r.status_code == 200 and r.content.startswith(sig)
                  and "attachment" in r.headers.get("content-disposition", ""))

        r = client.get("/api/export?period=day&anchor=2026-08-04&format=csv"
                       "&token=test-token-123")
        check("export accepts a query-string token (browser download)", r.status_code == 200)

        r = client.get("/api/export?period=day&anchor=2026-08-04&token=nope")
        check("export refuses a bad query token", r.status_code == 401)

        r = client.get("/api/export/preview?period=quarter&anchor=2026-08-04", headers=h)
        check("export preview counts rows",
              r.status_code == 200 and r.json()["trades"] == 3)

        r = client.post("/api/bot/stop", json={"reason": "test"}, headers=h)
        check("stopping an already-stopped bot returns 409", r.status_code == 409)

        r = client.get("/api/today", headers=h)
        check("today endpoint works", r.status_code == 200 and "snapshot" in r.json())

        r = client.get("/", headers=h)
        check("dashboard page served", r.status_code == 200 and "html" in r.text.lower())

        # ---- strategy ----
        r = client.get("/api/strategy", headers=h)
        check("strategy describes itself", r.status_code == 200 and len(r.json()["groups"]) > 0)
        body = r.json()
        n_params = sum(len(g["params"]) for g in body["groups"])
        check(f"all parameters exposed ({n_params})", n_params >= 30)
        check("baseline reports no drift", body["drift"] == {})
        check("baseline reports no problems", body["problems"] == [])

        r = client.put("/api/strategy", headers=h,
                       json={"values": {"SL_PCT": 0.12, "MAX_TRADES_PER_DAY": 5}})
        check("valid strategy edit accepted", r.status_code == 200)
        check("edit shows up as drift",
              set(r.json()["drift"]) == {"SL_PCT", "MAX_TRADES_PER_DAY"}, str(r.json()["drift"]))

        r = client.put("/api/strategy", headers=h, json={"values": {"BE_FLOOR_PCT": 0.30}})
        check("inconsistent ladder rejected with a reason",
              r.status_code == 400 and "below its trigger" in r.json()["detail"],
              r.text[:200])

        r = client.put("/api/strategy", headers=h, json={"values": {"SL_PCT": 9.0}})
        check("out-of-range value rejected", r.status_code == 400)

        r = client.put("/api/strategy", headers=h, json={"values": {"NOT_A_PARAM": 1}})
        check("unknown parameter rejected", r.status_code == 400)

        r = client.post("/api/strategy/profiles", headers=h, json={"name": "wider stop"})
        check("profile saved", r.status_code == 200
              and any(p["name"] == "wider stop" for p in r.json()["profiles"]))

        client.post("/api/strategy/reset", headers=h)
        r = client.get("/api/strategy", headers=h)
        check("reset clears drift", r.json()["drift"] == {})

        r = client.post("/api/strategy/profiles/load", headers=h, json={"name": "wider stop"})
        check("profile restores its values",
              r.status_code == 200 and "SL_PCT" in r.json()["drift"], str(r.json()["drift"]))
        client.post("/api/strategy/reset", headers=h)

        r = client.get("/api/strategy", headers=h)
        check("strategy endpoint needs no bot running", r.status_code == 200)
        r = client.get("/api/strategy")
        check("strategy endpoint is authenticated", r.status_code == 401)

        # ---- chart ----
        r = client.get("/api/chart", headers=h)
        check("chart endpoint answers with the bot stopped",
              r.status_code == 200 and r.json()["available"] is False)
        check("chart explains why it is empty", bool(r.json().get("reason")))
        r = client.get("/api/chart")
        check("chart endpoint is authenticated", r.status_code == 401)


def main() -> int:
    print("=" * 60)
    print("  MERIDIAN CAPITAL — SMOKE TESTS")
    print("=" * 60)
    test_ranges()
    seed()
    test_storage()
    test_aggregate()
    test_exports()
    test_api()
    print("\n" + "=" * 60)
    print(f"  {PASS} passed, {FAIL} failed")
    print("=" * 60)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
