#!/usr/bin/env python3
"""Fill the database with a plausible session so the dashboard can be developed
and reviewed without a broker connection.

    MERIDIAN_DATA_DIR=./var python scripts/seed_demo.py

Writes only to the database named by MERIDIAN_DB_PATH. Never run it against the
data directory of a live deployment — it inserts fictitious trades.
"""

from __future__ import annotations

import math
import random
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine import config as C  # noqa: E402
from engine.clock import now_ist  # noqa: E402
from engine.strategy import effective_stop, stop_label  # noqa: E402
from shared.db import K_SNAPSHOT, Database  # noqa: E402

random.seed(7)


def main() -> int:
    db = Database(C.DB_PATH)
    now = now_ist()
    today = now.strftime("%Y-%m-%d")
    equity = 712_450.0
    start_equity = 698_200.0

    # Intraday equity samples across the session so far.
    for i in range(130):
        ts = now - timedelta(minutes=130 - i)
        drift = 14_250 * (i / 130) + math.sin(i / 9) * 2_400
        value = start_equity + drift
        with db.conn() as conn:
            conn.execute(
                """INSERT INTO equity_samples(ts, session_date, equity, realised_today,
                                              peak_equity, day_pl)
                   VALUES(?,?,?,?,?,?)""",
                (ts.isoformat(timespec="seconds"), today, round(value, 1), 0.0,
                 round(max(start_equity, value), 1), round(value - start_equity, 1)),
            )

    # Closed trades across recent sessions.
    outcomes = [
        (-6_420.0, "STOP", 1), (11_800.0, "TARGET", 3), (9_240.0, "TARGET", 2),
        (-4_100.0, "STOP", 1), (7_650.0, "EOD", 2), (14_280.0, "TARGET", 3),
    ]
    running = start_equity - sum(n for n, _, _ in outcomes)
    for offset, (net, reason, lots) in enumerate(reversed(outcomes)):
        day = now - timedelta(days=len(outcomes) - offset)
        session = day.strftime("%Y-%m-%d")
        qty = lots * C.LOT_SIZE
        # Keep the exit premium positive: a loss can never exceed the premium paid.
        floor = abs(net) / qty / C.STOP_FRAC if net < 0 else 0.0
        entry_prem = round(max(random.uniform(105, 240), floor), 2)
        exit_prem = round(max(0.05, entry_prem + net / qty), 2)
        charges = round(abs(net) * 0.035 + 120, 1)
        running += net
        db.add_trade({
            "entry_ts": day.replace(hour=10, minute=42).strftime("%Y-%m-%d %H:%M"),
            "exit_ts": day.replace(hour=12, minute=8).strftime("%Y-%m-%d %H:%M"),
            "session_date": session, "mode": "paper",
            "view": "C" if net > 0 else "P", "side": "CE" if net > 0 else "PE",
            "strike": 24_950 + offset * 50, "expiry": (day + timedelta(days=4)).date().isoformat(),
            "tsym": f"NIFTY{(day + timedelta(days=4)):%d%b%y}".upper() + f"{24_950 + offset * 50}CE",
            "lots": lots, "qty": qty, "entry_prem": entry_prem, "exit_prem": exit_prem,
            "spot_entry": 24_980.0 + offset * 30, "spot_exit": 25_040.0 + offset * 30,
            "peak_pct": round(random.uniform(-12, 68), 1),
            "gross": round(net + charges, 1), "charges": charges, "net": net,
            "reason": reason, "hold_min": 86.0, "equity": round(running, 1),
            "pnl_source": "angel",
        })
        db.add_equity_sample(session, round(running, 1), net, round(running, 1), net)

    for level, message in [
        ("info", "rulebook active"),
        ("ok", "connected as DEMO123"),
        ("ok", "82,431 NIFTY option contracts loaded from Angel One scrip master"),
        ("info", "clock verified against NTP (drift +0.04s)"),
        ("warn", "NO TRADE — feed divergence 31.4pt (candle 25,012.2 vs LTP 24,980.8, limit 25pt)"),
        ("ok", "ENTER BUY CE 24950 (ITM) x3 lots (75) @ 168.40  spot 25002  exp 2026-09-17"),
    ]:
        db.add_event(level, message)

    peak = 726_900.0
    entry, live = 168.40, 214.75
    gain = (live - entry) / entry
    es = effective_stop(gain)

    db.kv_set(K_SNAPSHOT, {
        "ts": now.isoformat(timespec="seconds"),
        "engine": {"version": "1.0.0", "mode": "paper", "phase": "IN POSITION", "pid": 4242,
                   "uptime_sec": 8_140.0, "banner": C.BANNER, "build": C.BUILD_VERSION},
        "market": {"open": True, "session_date": today,
                   "entry_window": {"start": "10:15", "cutoff": "14:00",
                                    "force_close": "15:10", "open_now": True}},
        "account": {"equity": equity, "start_equity": start_equity, "peak_equity": peak,
                    "week_start_equity": 690_000.0, "day_pl": equity - start_equity,
                    "day_pl_pct": (equity - start_equity) / start_equity,
                    "realised_today": 0.0, "realised_week": 7_650.0,
                    "drawdown_pct": (equity - peak) / peak, "currency": "INR"},
        "signal": {"spot": 25_042.6, "channel_high": 25_010.0, "channel_low": 24_638.0,
                   "lookback": C.DONCHIAN_LB, "view": "C", "state": "break_up",
                   "room_up": -32.6, "room_down": 404.6, "bar_close": 25_041.2,
                   "bar_age_sec": 34.0, "bars_loaded": 462, "divergence_pts": 1.4,
                   "divergence_limit": C.MAX_FEED_DIVERGENCE_PTS, "next_strike": 24_950},
        "position": {
            "tsym": "NIFTY17SEP2624950CE", "side": "CE", "strike": 24_950,
            "expiry": "2026-09-17", "view": "C", "lots": 3, "qty": 75,
            "entry_premium": entry, "live_premium": live, "gain_pct": gain,
            "peak_pct": 0.32, "unrealised": (live - entry) * 75, "notional": live * 75,
            "stop_price": entry * (1 - es), "stop_pct": es, "stop_state": stop_label(es),
            "target_pts": C.TARGET_PTS, "index_move_pts": 62.0, "spot_entry": 24_980.6,
            "opened_ts": now.replace(hour=10, minute=46).isoformat(), "hold_min": 74.0,
        },
        "guards": {
            "halted": False, "week_halted": False, "locked_profit": False,
            "trades_today": 1, "max_trades_day": C.MAX_TRADES_DAY,
            "consec_losses": 0, "consec_loss_halt": C.CONSEC_LOSS_HALT,
            "daily_loss_used": 0.0, "daily_loss_limit": C.DAILY_LOSS_LIMIT_RS,
            "weekly_loss_used": 0.0, "weekly_loss_limit": C.WEEKLY_LOSS_LIMIT_RS,
            "profit_lock_progress": 0.0, "profit_lock_target": C.DAILY_PROFIT_LOCK_RS,
            "drawdown_stop": C.MAX_DRAWDOWN_STOP, "min_capital": C.MIN_CAPITAL,
            "capital_ok": True, "deploy_fraction": C.DEPLOY_FRACTION,
            "per_trade_equity_cap": C.PER_TRADE_EQUITY_CAP,
            "per_trade_risk_rs": C.PER_TRADE_RISK_RS, "max_lots": C.MAX_LOTS,
        },
        "health": {"clock_drift_sec": 0.04, "broker_connected": True,
                   "broker_client_id": "DEMO123",
                   "api": {"total_calls": 3_184, "throttles": 0, "waited_sec": 12.4},
                   "contracts_loaded": 82_431, "telemetry_dropped": 0, "last_error": None},
    })

    print(f"Seeded demo data into {C.DB_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
