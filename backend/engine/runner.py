"""The GANESH KAVACH 50K trading loop, as a supervised long-running process.

The trading decisions here are a faithful port of the backtested single-file
build: same Donchian-90 breakout, same ITM50 strike, same profit/stop ladder,
same kill switches, same order-verification path. What is new is that the loop
publishes structured telemetry instead of painting a console, and that it obeys
control commands issued from the dashboard between iterations.
"""

from __future__ import annotations

import argparse
import csv
import os
import signal
import sys
import time
from datetime import datetime

from engine import config as C
from engine.broker import Broker, Credentials
from engine.clock import ge, is_market_hours, lt, now_ist, ntp_offset
from engine.state import State
from engine.strategy import (
    donchian,
    effective_stop,
    money,
    pick_contract,
    round_trip_charges,
    size_position,
    stop_label,
    target_strike,
)
from engine.telemetry import Telemetry
from shared.db import Database

ENGINE_VERSION = "1.0.0"


class Shutdown(Exception):
    """Raised inside the loop when a stop has been requested."""


class Engine:
    def __init__(self, mode: str = "paper"):
        self.mode = "live" if mode == "live" else "paper"
        self.dry_run = self.mode != "live"
        self.db = Database(C.DB_PATH)
        self.tm = Telemetry(self.db)
        self.started_at = time.time()
        self.stop_requested = False
        self.stop_reason = ""
        self.force_stop = False
        self.br: Broker | None = None
        self.st = State()
        self.pos: dict | None = None
        self.equity: float = 0.0
        self.clock_drift: float | None = None
        self.last_equity_sample = 0.0
        self.last_snapshot = 0.0
        self.table: dict = {}
        self.table_day = ""
        self.reported = False
        self.eod_pending = False

    # ── lifecycle ────────────────────────────────────────────────────────────
    def install_signals(self) -> None:
        def handler(signum, _frame):
            self.stop_requested = True
            self.stop_reason = f"signal {signal.Signals(signum).name}"

        signal.signal(signal.SIGTERM, handler)
        signal.signal(signal.SIGINT, handler)

    def connect(self) -> None:
        creds = Credentials.from_env()
        self.tm.log(f"{C.BANNER} — engine v{ENGINE_VERSION}", "info")
        self.tm.log(
            f"connecting to Angel One — mode {'LIVE (real orders)' if not self.dry_run else 'PAPER (no orders)'}",
            "warn" if not self.dry_run else "info",
        )
        self.br = Broker(creds, dry_run=self.dry_run, log=self.tm.as_broker_logger())
        self.tm.log(f"connected as {self.br.client_id}", "ok")

    def bootstrap(self) -> None:
        assert self.br is not None
        equity = self.br.funds()
        if equity is None:
            raise RuntimeError("Could not read account funds from Angel One. Refusing to start blind.")
        self.equity = equity
        self.tm.log(f"live capital pulled from Angel One: {money(equity)}", "ok")

        self.st = State.load()
        today = now_ist().strftime("%Y-%m-%d")
        resuming = bool(self.st.position) and self.st.session_date == today

        if equity < C.MIN_CAPITAL:
            if resuming:
                self.tm.log(
                    f"capital {money(equity)} is below the {money(C.MIN_CAPITAL)} floor, but an OPEN "
                    f"POSITION is being resumed. Managing it; no new entries will be taken.",
                    "warn",
                )
            else:
                raise RuntimeError(
                    f"Capital {money(equity)} is below the {money(C.MIN_CAPITAL)} floor. "
                    f"This build is validated for Rs 50,000+. Refusing to start."
                )

        if self.st.session_date == today:
            self.tm.log(
                f"recovered today's state: {self.st.trades_today} trade(s), "
                f"realised {money(self.st.realised_today)}"
                + (", OPEN POSITION restored" if self.st.position else ""),
                "warn",
            )
        else:
            t = now_ist()
            wk = f"{t.isocalendar().year}-W{t.isocalendar().week:02d}"
            self.st = State(
                session_date=today, week_id=wk, start_equity=equity,
                week_start_equity=equity, peak_equity=equity,
            )
            self.st.save()

        self.check_clock(initial=True)
        self.reconcile_broker_position()

        self.table = self.br.option_table()
        self.table_day = today
        self.tm.log(
            f"{len(self.table):,} NIFTY option contracts loaded from Angel One scrip master", "ok"
        )
        self.log_rulebook()
        self.pos = self.st.position or None

    def log_rulebook(self) -> None:
        rules = [
            f"take-profit: FIXED +{C.TARGET_PTS} index pts (single hard target)",
            f"break-even : at +{int(C.BE_TRIGGER * 100)}% premium gain, stop jumps to entry",
            f"trail      : past +{int(C.TRAIL_FRAC * 100)}% gain, stop rides "
            f"{int(C.TRAIL_FRAC * 100)}% behind peak",
            f"hard stop  : -{int(C.STOP_FRAC * 100)}% of premium initially, tightens only",
            f"rupee cap  : skip any trade risking more than {money(C.PER_TRADE_RISK_RS)}",
            f"daily kill : stop the day at {money(C.DAILY_LOSS_LIMIT_RS)} realised loss",
            f"weekly kill: stop the week at {money(C.WEEKLY_LOSS_LIMIT_RS)} realised loss",
            f"streak halt: pause the day after {C.CONSEC_LOSS_HALT} losses in a row",
            f"profit lock: bank the day at +{money(C.DAILY_PROFIT_LOCK_RS)}",
            f"trades/day : fixed {C.MAX_TRADES_DAY}",
            f"drawdown   : halt all new entries at -{int(C.MAX_DRAWDOWN_STOP * 100)}% from peak",
            f"entries {C.ENTRY_START[0]:02d}:{C.ENTRY_START[1]:02d}"
            f"–{C.ENTRY_CUTOFF[0]:02d}:{C.ENTRY_CUTOFF[1]:02d}, "
            f"flat by {C.FORCE_CLOSE[0]:02d}:{C.FORCE_CLOSE[1]:02d}",
        ]
        self.tm.log("rulebook active", "info", {"rules": rules})

    def check_clock(self, initial: bool = False) -> None:
        drift = ntp_offset()
        self.clock_drift = drift
        if drift is None:
            self.tm.log("NTP unreachable — cannot verify system clock.", "warn")
        elif abs(drift) > C.HALT_CLOCK_DRIFT_SEC:
            if initial:
                raise RuntimeError(
                    f"System clock is off by {drift:+.1f}s (limit {C.HALT_CLOCK_DRIFT_SEC:.0f}s). "
                    f"Entry/exit windows would be wrong."
                )
            self.st.halted = True
            self.st.save()
            self.tm.log(
                f"CLOCK DRIFT {drift:+.1f}s exceeds {C.HALT_CLOCK_DRIFT_SEC:.0f}s — halting new entries.",
                "critical",
            )
        elif abs(drift) > C.MAX_CLOCK_DRIFT_SEC:
            self.tm.log(f"clock drift {drift:+.1f}s — sync recommended", "warn")
        elif initial:
            self.tm.log(f"clock verified against NTP (drift {drift:+.2f}s)", "ok")

    def reconcile_broker_position(self) -> None:
        """An open lot at Angel that this engine does not know about is an emergency."""
        assert self.br is not None
        if self.dry_run:
            return
        live = self.br.open_positions()
        if not live:
            if self.st.position:
                self.tm.log(
                    f"state file had position {self.st.position.get('tsym')} but Angel reports none — "
                    f"clearing local position.",
                    "warn",
                )
                self.st.position = {}
                self.st.save()
            return
        known = {self.st.position.get("tsym")} if self.st.position else set()
        orphans = [p for p in live if str(p.get("tradingsymbol")) not in known]
        if orphans:
            self.tm.log(
                f"ORPHAN POSITION(S) at Angel not managed by this engine: "
                f"{', '.join(str(p.get('tradingsymbol')) for p in orphans)}. "
                f"Square them off manually or use FLATTEN.",
                "critical",
                {"positions": orphans},
            )

    # ── remote control ───────────────────────────────────────────────────────
    def process_commands(self) -> None:
        for cmd in self.db.claim_commands():
            action = str(cmd.get("action", "")).lower()
            payload = cmd.get("payload") or {}
            try:
                result = self.handle_command(action, payload)
                self.db.finish_command(cmd["id"], result)
                self.tm.log(f"command '{action}' -> {result}", "info", {"by": cmd.get("issued_by")})
            except Exception as e:
                self.db.finish_command(cmd["id"], f"failed: {e}", status="failed")
                self.tm.log(f"command '{action}' failed: {e}", "error")

    def handle_command(self, action: str, payload: dict) -> str:
        if action == "halt":
            self.st.halted = True
            self.st.save()
            return "new entries halted for the day"
        if action == "resume":
            self.st.halted = False
            self.st.locked_profit = False
            if payload.get("week"):
                self.st.week_halted = False
            self.st.save()
            return "entries re-armed"
        if action == "flatten":
            if not self.pos:
                return "no open position"
            ok = self.exit_position("MANUAL", forced=True)
            return "flattened" if ok else "FLATTEN FAILED — square off in the Angel app"
        if action == "stop":
            if self.pos and not payload.get("force"):
                return "refused: position is open — use flatten first, or pass force"
            self.stop_requested = True
            self.force_stop = bool(payload.get("force"))
            self.stop_reason = payload.get("reason") or "operator stop"
            return "stopping"
        if action == "reload_scrip":
            assert self.br is not None
            self.table = self.br.option_table()
            self.table_day = now_ist().strftime("%Y-%m-%d")
            return f"{len(self.table):,} contracts reloaded"
        if action == "ping":
            return "pong"
        return f"unknown action '{action}'"

    # ── session bookkeeping ──────────────────────────────────────────────────
    def roll_session(self, t: datetime) -> None:
        assert self.br is not None
        if not self.reported:
            self.emit_eod_report()
        self.equity = self.br.funds() or self.equity
        wk = f"{t.isocalendar().year}-W{t.isocalendar().week:02d}"
        new_week = wk != self.st.week_id
        prev_week_real = 0.0 if new_week else self.st.realised_week
        prev_week_start = self.equity if new_week else (self.st.week_start_equity or self.equity)
        if self.pos is not None:
            self.tm.log(
                f"position {self.pos.get('tsym')} was still open at session rollover. It is NOT "
                f"being managed anymore — check the Angel app and square off.",
                "critical",
            )
        self.st = State(
            session_date=t.strftime("%Y-%m-%d"), week_id=wk, start_equity=self.equity,
            week_start_equity=prev_week_start, peak_equity=max(self.equity, 0.0),
            realised_week=prev_week_real,
        )
        self.st.save()
        self.pos = None
        self.reported = False
        self.tm.log(
            f"new session {t:%Y-%m-%d %a} — capital {money(self.equity)} "
            f"({'new week' if new_week else 'week ' + wk})",
            "info",
        )

    def todays_realised(self) -> tuple[int, float]:
        # Keyed on the engine's own session date, not the wall clock, so a query
        # landing either side of a rollover still reads one coherent session.
        rows = self.db.trades(limit=100, session_date=self.st.session_date)
        closed = [r for r in rows if r.get("exit_ts")]
        return len(closed), float(sum(r.get("net") or 0.0 for r in closed))

    def record_trade(self, row: dict) -> None:
        self.db.add_trade(row)
        try:
            new = not C.TRADE_LOG.exists()
            with open(C.TRADE_LOG, "a", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                if new:
                    w.writerow(list(row.keys()))
                w.writerow(list(row.values()))
        except OSError:
            pass

    def emit_eod_report(self) -> None:
        today = self.st.session_date or now_ist().strftime("%Y-%m-%d")
        rows = [r for r in self.db.trades(limit=100, session_date=today) if r.get("exit_ts")]
        nets = [float(r.get("net") or 0.0) for r in rows]
        wins = [n for n in nets if n > 0]
        report = {
            "session_date": today,
            "mode": self.mode,
            "trades": len(rows),
            "wins": len(wins),
            "losses": len(rows) - len(wins),
            "win_rate": (len(wins) / len(rows)) if rows else 0.0,
            "gross": sum(float(r.get("gross") or 0.0) for r in rows),
            "charges": sum(float(r.get("charges") or 0.0) for r in rows),
            "net": sum(nets),
            "best": max(nets) if nets else 0.0,
            "worst": min(nets) if nets else 0.0,
            "avg_hold_min": (sum(float(r.get("hold_min") or 0) for r in rows) / len(rows)) if rows else 0.0,
            "start_equity": self.st.start_equity,
            "end_equity": self.equity,
            "day_pl": self.equity - self.st.start_equity if self.st.start_equity else 0.0,
            "drawdown_pct": 0.0 if self.st.peak_equity <= 0
            else (self.equity - self.st.peak_equity) / self.st.peak_equity,
            "blotter": rows,
        }
        self.db.kv_set(f"report:eod:{today}", report)
        self.db.kv_set("report:eod:latest", report)
        self.tm.log(
            f"END OF DAY {today} — {report['trades']} trade(s), net {money(report['net'])}, "
            f"equity {money(self.equity)}",
            "info",
            {"report": {k: v for k, v in report.items() if k != "blotter"}},
        )
        self.reported = True

    # ── snapshot for the dashboard ───────────────────────────────────────────
    def build_snapshot(self, t, spot, view, hi, lo, bars, live_prem, phase) -> dict:
        st = self.st
        dd = 0.0 if st.peak_equity <= 0 else (self.equity - st.peak_equity) / st.peak_equity
        day_pl = self.equity - st.start_equity if st.start_equity else 0.0
        bar_close = float(bars["close"].iloc[-1]) if bars is not None and len(bars) else None
        bar_age = None
        if bars is not None and len(bars):
            bar_age = (t - bars["ts"].iloc[-1].to_pydatetime()).total_seconds()

        position = None
        if self.pos:
            p = self.pos
            fv = ((live_prem - p["entry"]) / p["entry"]) if live_prem else None
            peak = max(p.get("peak", fv if fv is not None else 0.0), fv if fv is not None else -9.0)
            es = effective_stop(peak)
            qty = p["lots"] * C.LOT_SIZE
            idx_move = ((spot - p["spot"]) * (1 if p["view"] == "C" else -1)) if spot else None
            position = {
                "tsym": p["tsym"], "side": p["right"], "strike": p["strike"], "expiry": p["expiry"],
                "view": p["view"], "lots": p["lots"], "qty": qty,
                "entry_premium": p["entry"], "live_premium": live_prem,
                "gain_pct": fv, "peak_pct": peak,
                "unrealised": ((live_prem - p["entry"]) * qty) if live_prem else None,
                "notional": (live_prem or p["entry"]) * qty,
                "stop_price": p["entry"] * (1 - es), "stop_pct": es, "stop_state": stop_label(es),
                "target_pts": C.TARGET_PTS, "index_move_pts": idx_move,
                "spot_entry": p["spot"], "opened_ts": p["ts"],
                "hold_min": (t - datetime.fromisoformat(p["ts"])).total_seconds() / 60,
            }

        signal_state = "unknown"
        if spot is not None and hi is not None and lo is not None:
            signal_state = "break_up" if spot > hi else "break_down" if spot < lo else "inside"

        return {
            "ts": t.isoformat(timespec="seconds"),
            "engine": {
                "version": ENGINE_VERSION, "mode": self.mode, "phase": phase, "pid": os.getpid(),
                "uptime_sec": round(time.time() - self.started_at, 1),
                "banner": C.BANNER, "build": C.BUILD_VERSION,
            },
            "market": {
                "open": is_market_hours(t),
                "session_date": st.session_date,
                "entry_window": {
                    "start": f"{C.ENTRY_START[0]:02d}:{C.ENTRY_START[1]:02d}",
                    "cutoff": f"{C.ENTRY_CUTOFF[0]:02d}:{C.ENTRY_CUTOFF[1]:02d}",
                    "force_close": f"{C.FORCE_CLOSE[0]:02d}:{C.FORCE_CLOSE[1]:02d}",
                    "open_now": ge(t, C.ENTRY_START) and lt(t, C.ENTRY_CUTOFF),
                },
            },
            "account": {
                "equity": self.equity, "start_equity": st.start_equity,
                "peak_equity": st.peak_equity, "week_start_equity": st.week_start_equity,
                "day_pl": day_pl,
                "day_pl_pct": (day_pl / st.start_equity) if st.start_equity else 0.0,
                "realised_today": st.realised_today, "realised_week": st.realised_week,
                "drawdown_pct": dd, "currency": "INR",
            },
            "signal": {
                "spot": spot, "channel_high": hi, "channel_low": lo,
                "lookback": C.DONCHIAN_LB, "view": view, "state": signal_state,
                "room_up": (hi - spot) if (hi is not None and spot) else None,
                "room_down": (spot - lo) if (lo is not None and spot) else None,
                "bar_close": bar_close, "bar_age_sec": bar_age,
                "bars_loaded": int(len(bars)) if bars is not None else 0,
                "divergence_pts": abs(bar_close - spot) if (bar_close is not None and spot) else None,
                "divergence_limit": C.MAX_FEED_DIVERGENCE_PTS,
                "next_strike": target_strike(spot, "CE" if view == "C" else "PE") if spot and view else None,
            },
            "position": position,
            "guards": {
                "halted": st.halted, "week_halted": st.week_halted, "locked_profit": st.locked_profit,
                "trades_today": st.trades_today, "max_trades_day": C.MAX_TRADES_DAY,
                "consec_losses": st.consec_losses, "consec_loss_halt": C.CONSEC_LOSS_HALT,
                "daily_loss_used": min(0.0, st.realised_today), "daily_loss_limit": C.DAILY_LOSS_LIMIT_RS,
                "weekly_loss_used": min(0.0, st.realised_week or 0.0),
                "weekly_loss_limit": C.WEEKLY_LOSS_LIMIT_RS,
                "profit_lock_progress": max(0.0, st.realised_today),
                "profit_lock_target": C.DAILY_PROFIT_LOCK_RS,
                "drawdown_stop": C.MAX_DRAWDOWN_STOP,
                "min_capital": C.MIN_CAPITAL, "capital_ok": self.equity >= C.MIN_CAPITAL,
                "deploy_fraction": C.DEPLOY_FRACTION, "per_trade_equity_cap": C.PER_TRADE_EQUITY_CAP,
                "per_trade_risk_rs": C.PER_TRADE_RISK_RS, "max_lots": C.MAX_LOTS,
            },
            "health": {
                "clock_drift_sec": self.clock_drift,
                "broker_connected": self.br is not None,
                "broker_client_id": self.br.client_id if self.br else None,
                "api": self.br.rl.stats() if self.br else None,
                "contracts_loaded": len(self.table),
                "telemetry_dropped": self.tm.dropped,
                "last_error": self.br.last_error if self.br else None,
            },
        }

    # ── trade execution ──────────────────────────────────────────────────────
    def exit_position(self, why: str, forced: bool = False, spot: float | None = None,
                      live_prem: float | None = None) -> bool:
        assert self.br is not None and self.pos is not None
        p = self.pos
        t = now_ist()
        qty = p["lots"] * C.LOT_SIZE
        ok, fill_px, detail = self.br.place(p["tsym"], p["token"], "SELL", qty)
        if not ok:
            for attempt in range(C.ORDER_RETRIES + 2):
                self.tm.log(f"EXIT ORDER FAILED ({detail}) — retry {attempt + 1}", "error")
                time.sleep(2)
                ok, fill_px, detail = self.br.place(p["tsym"], p["token"], "SELL", qty)
                if ok:
                    break
        if not ok:
            self.tm.log(
                f"EXIT COULD NOT BE PLACED for {p['tsym']}: {detail}. POSITION IS STILL OPEN — "
                f"square off manually in the Angel app NOW.",
                "critical",
            )
            self.st.position = p
            self.st.save()
            return False

        realised_before = self.st.realised_today or 0.0
        time.sleep(1.5)
        fill = fill_px if fill_px else (None if self.br.dry_run else self.br.last_fill(p["tsym"]))
        fallback = live_prem if live_prem else self.br.ltp("NFO", p["tsym"], p["token"]) or p["entry"]
        exit_px = fill if (fill is not None and fill > 0) else fallback
        realised_after = self.br.realised_pnl(force=True)
        equity_after = self.br.funds(force=True)

        if realised_after is not None:
            net = realised_after - realised_before
            pnl_source = "angel"
        else:
            net = (exit_px - p["entry"]) * qty - round_trip_charges(p["entry"], exit_px, qty)
            pnl_source = "estimate"
            self.tm.log("realised P&L unavailable from Angel — trade P&L is an ESTIMATE", "warn")

        self.equity = equity_after if equity_after is not None else (self.equity + net)
        gross = (exit_px - p["entry"]) * qty
        charges = gross - net
        self.st.peak_equity = max(self.st.peak_equity, self.equity)
        hold = (t - datetime.fromisoformat(p["ts"])).total_seconds() / 60

        self.record_trade({
            "entry_ts": p["ts"][:16].replace("T", " "),
            "exit_ts": t.strftime("%Y-%m-%d %H:%M"),
            "session_date": self.st.session_date, "mode": self.mode,
            "view": p["view"], "side": p["right"], "strike": p["strike"], "expiry": p["expiry"],
            "tsym": p["tsym"], "lots": p["lots"], "qty": qty,
            "entry_prem": round(p["entry"], 2), "exit_prem": round(exit_px, 2),
            "spot_entry": round(p["spot"], 2), "spot_exit": round(spot or 0.0, 2),
            "peak_pct": round(100 * p.get("peak", 0.0), 1),
            "gross": round(gross, 1), "charges": round(charges, 1), "net": round(net, 1),
            "reason": why, "hold_min": round(hold, 0), "equity": round(self.equity, 1),
            "pnl_source": pnl_source,
        })
        self.tm.log(
            f"EXIT {why}  {p['right']} {p['strike']} x{p['lots']}  {p['entry']:.2f}->{exit_px:.2f}  "
            f"peak {100 * p.get('peak', 0.0):+.0f}%  NET {money(net)} ({pnl_source})  "
            f"equity {money(self.equity)}",
            "ok" if net > 0 else "error",
        )
        self.st.consec_losses = (self.st.consec_losses + 1) if net < 0 else 0
        self.st.realised_week = (self.st.realised_week or 0.0) + net
        if self.st.consec_losses >= C.CONSEC_LOSS_HALT and not self.st.halted:
            self.st.halted = True
            self.tm.log(
                f"{self.st.consec_losses} losses in a row — pausing new entries for the day.", "warn"
            )
        if forced:
            self.st.halted = True
        self.pos = None
        self.st.position = {}
        self.st.save()
        return True

    def try_entry(self, t, spot, view, hi, lo, bars) -> None:
        assert self.br is not None
        if bars is None or len(bars) < C.DONCHIAN_LB + 2:
            self.tm.log(
                f"NO TRADE — candle feed short ({0 if bars is None else len(bars)} bars, "
                f"need {C.DONCHIAN_LB + 2})",
                "warn",
            )
            return
        bar_close = float(bars["close"].iloc[-1])
        bar_age = (t - bars["ts"].iloc[-1].to_pydatetime()).total_seconds()
        if bar_age > C.MAX_BAR_AGE_SEC:
            self.tm.log(
                f"NO TRADE — candle feed stale ({bar_age:.0f}s old, limit {C.MAX_BAR_AGE_SEC:.0f}s)",
                "warn",
            )
            return
        divergence = abs(bar_close - spot)
        if divergence > C.MAX_FEED_DIVERGENCE_PTS:
            self.tm.log(
                f"NO TRADE — feed divergence {divergence:.1f}pt (candle {bar_close:,.1f} vs "
                f"LTP {spot:,.1f}, limit {C.MAX_FEED_DIVERGENCE_PTS:.0f}pt)",
                "warn",
            )
            return

        got = pick_contract(self.table, spot, "CE" if view == "C" else "PE", t.date())
        if got is None:
            self.tm.log(f"signal {view} but no ITM contract at DTE {C.MIN_DTE}-{C.MAX_DTE}", "warn")
            return
        tsym, token, expiry, strike = got
        prem = self.br.ltp("NFO", tsym, token)
        if prem is None or prem > C.MAX_PREMIUM:
            self.tm.log(f"signal {view} but {strike} unpriced or premium {prem} above cap", "warn")
            return
        lots = size_position(self.equity, prem)
        if lots < 1:
            self.tm.log(
                f"signal {view} but premium {prem:.2f} too large to size safely — skipped", "warn"
            )
            return

        qty = lots * C.LOT_SIZE
        ok, fill_px, detail = self.br.place(tsym, token, "BUY", qty)
        if not ok:
            for attempt in range(C.ORDER_RETRIES):
                self.tm.log(f"entry failed ({detail}) — retry {attempt + 1}/{C.ORDER_RETRIES}", "warn")
                time.sleep(2)
                ok, fill_px, detail = self.br.place(tsym, token, "BUY", qty)
                if ok:
                    break
        if not ok:
            self.tm.log(f"ENTRY ABANDONED for {tsym}: {detail}. No position opened.", "error")
            return
        time.sleep(1.5)
        real_entry = fill_px if fill_px else (None if self.br.dry_run else self.br.last_fill(tsym))
        entry_px = real_entry if (real_entry is not None and real_entry > 0) else prem
        if not (entry_px and entry_px > 0):
            self.tm.log(
                f"ENTRY ABORTED — no valid fill price for {tsym}. Square off manually if filled.",
                "critical",
            )
            return

        self.pos = {
            "ts": t.isoformat(), "view": view, "right": "CE" if view == "C" else "PE",
            "strike": strike, "expiry": expiry.isoformat(), "tsym": tsym, "token": token,
            "entry": entry_px, "spot": spot, "lots": lots, "peak": 0.0,
        }
        self.st.trades_today += 1
        self.st.position = self.pos
        self.st.save()
        self.tm.log(
            f"ENTER BUY {self.pos['right']} {strike} (ITM) x{lots} lots ({qty}) @ {entry_px:.2f}  "
            f"spot {spot:.0f}  exp {expiry}  channel [{lo:.0f}..{hi:.0f}]",
            "ok",
            {"tsym": tsym, "lots": lots, "qty": qty, "entry": entry_px, "spot": spot},
        )

    # ── main loop ────────────────────────────────────────────────────────────
    def run(self) -> int:
        assert self.br is not None
        last_clock_check = time.time()

        while True:
            try:
                if self.stop_requested:
                    raise Shutdown(self.stop_reason or "stop requested")

                t = now_ist()
                self.process_commands()

                if t.strftime("%Y-%m-%d") != self.st.session_date:
                    self.roll_session(t)
                if t.strftime("%Y-%m-%d") != self.table_day:
                    try:
                        self.table = self.br.option_table()
                        self.table_day = t.strftime("%Y-%m-%d")
                    except Exception:
                        pass

                if time.time() - last_clock_check > C.CLOCK_RECHECK_SEC:
                    last_clock_check = time.time()
                    self.check_clock()

                market_open = is_market_hours(t)
                spot = self.br.ltp(C.INDEX_EXCH, C.INDEX_TSYM, C.INDEX_TOKEN) if market_open else None
                bars = self.br.one_min_bars() if market_open else None
                view, hi, lo = donchian(bars["close"]) if bars is not None else ("", None, None)

                n_today, pnl_log = self.todays_realised()
                angel_realised = self.br.realised_pnl()
                pnl_today = angel_realised if angel_realised is not None else pnl_log
                self.st.trades_today = max(self.st.trades_today, n_today)
                self.st.realised_today = pnl_today
                self.equity = self.br.funds() or self.equity
                self.st.peak_equity = max(self.st.peak_equity, self.equity)

                live_prem = (
                    self.br.ltp("NFO", self.pos["tsym"], self.pos["token"]) if self.pos else None
                )
                phase = (
                    "MARKET CLOSED" if not market_open
                    else "IN POSITION" if self.pos
                    else "HALTED" if self.st.halted
                    else "SCANNING" if ge(t, C.ENTRY_START) and lt(t, C.ENTRY_CUTOFF)
                    else "PRE-ENTRY WINDOW" if lt(t, C.ENTRY_START)
                    else "ENTRY WINDOW CLOSED"
                )

                self.tm.snapshot(
                    self.build_snapshot(t, spot, view, hi, lo, bars, live_prem, phase),
                    min_interval=C.SNAPSHOT_SECONDS,
                )
                if time.time() - self.last_equity_sample > C.EQUITY_SAMPLE_SECONDS:
                    self.last_equity_sample = time.time()
                    self.db.add_equity_sample(
                        self.st.session_date, self.equity, self.st.realised_today,
                        self.st.peak_equity,
                        self.equity - self.st.start_equity if self.st.start_equity else 0.0,
                    )

                if market_open and ge(t, C.FORCE_CLOSE) and not self.reported and not self.pos:
                    self.emit_eod_report()

                if not market_open:
                    time.sleep(C.POLL_SECONDS)
                    continue

                # ── manage open position ──────────────────────────────────────
                if self.pos is not None:
                    if live_prem is not None:
                        p = self.pos
                        fv = (live_prem - p["entry"]) / p["entry"]
                        p["peak"] = max(p.get("peak", -9.0), fv)
                        idx_move = (
                            (spot - p["spot"]) * (1 if p["view"] == "C" else -1) if spot else 0
                        )
                        es = effective_stop(p["peak"])
                        why = (
                            "TARGET" if idx_move >= C.TARGET_PTS
                            else "STOP" if fv <= -es
                            else "EOD" if ge(t, C.FORCE_CLOSE)
                            else None
                        )
                        self.st.position = p
                        self.st.save()
                        if why:
                            self.exit_position(why, spot=spot, live_prem=live_prem)
                    time.sleep(C.POLL_IN_TRADE)
                    continue

                # ── guard ladder ──────────────────────────────────────────────
                dd = (
                    0.0 if self.st.peak_equity <= 0
                    else (self.equity - self.st.peak_equity) / self.st.peak_equity
                )
                if dd <= -C.MAX_DRAWDOWN_STOP and not self.st.halted:
                    self.st.halted = True
                    self.st.save()
                    self.tm.log(f"MAX DRAWDOWN {100 * dd:.1f}% breached. Halting all new entries.", "critical")

                if (self.st.realised_week or 0.0) <= -C.WEEKLY_LOSS_LIMIT_RS and not self.st.week_halted:
                    self.st.week_halted = True
                    self.st.save()
                    self.tm.log(
                        f"WEEKLY KILL — realised {money(self.st.realised_week)} this week. "
                        f"Done for the week.",
                        "critical",
                    )
                if self.st.week_halted:
                    time.sleep(C.POLL_SECONDS)
                    continue

                if pnl_today <= -C.DAILY_LOSS_LIMIT_RS:
                    if not self.st.halted:
                        self.tm.log(f"DAILY KILL — realised {money(pnl_today)} today. Done for the day.", "critical")
                        self.st.halted = True
                        self.st.save()
                    time.sleep(C.POLL_SECONDS)
                    continue

                if C.DAILY_PROFIT_LOCK_RS and pnl_today >= C.DAILY_PROFIT_LOCK_RS and not self.st.locked_profit:
                    self.st.locked_profit = True
                    self.st.halted = True
                    self.st.save()
                    self.tm.log(f"PROFIT LOCK — up {money(pnl_today)} today. Banking it, done for the day.", "ok")
                    time.sleep(C.POLL_SECONDS)
                    continue

                if self.equity < C.MIN_CAPITAL:
                    self.tm.log(
                        f"NO TRADE — capital {money(self.equity)} below the "
                        f"{money(C.MIN_CAPITAL)} floor",
                        "warn",
                    )
                    time.sleep(C.POLL_SECONDS)
                    continue

                if self.st.halted or self.st.trades_today >= C.MAX_TRADES_DAY:
                    time.sleep(C.POLL_SECONDS)
                    continue
                if not (ge(t, C.ENTRY_START) and lt(t, C.ENTRY_CUTOFF)):
                    time.sleep(C.POLL_SECONDS)
                    continue
                if view not in ("C", "P") or spot is None:
                    time.sleep(C.POLL_SCANNING)
                    continue

                self.try_entry(t, spot, view, hi, lo, bars)

            except Shutdown as s:
                if self.pos is not None and not self.force_stop:
                    self.tm.log(
                        f"ENGINE STOPPING with {self.pos.get('tsym')} STILL OPEN ({s}). "
                        f"The position is no longer managed — square off in the Angel app.",
                        "critical",
                    )
                else:
                    self.tm.log(f"engine stopping: {s}", "info")
                if not self.reported:
                    self.emit_eod_report()
                return 0
            except Exception as e:
                self.tm.log(f"loop error (recovering): {type(e).__name__}: {str(e)[:200]}", "error")
            time.sleep(C.POLL_SECONDS)

    def publish_offline(self, reason: str) -> None:
        self.db.kv_set(
            "engine:offline",
            {"ts": now_ist().isoformat(timespec="seconds"), "reason": reason, "mode": self.mode},
        )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Meridian Capital — GANESH KAVACH 50K engine")
    ap.add_argument(
        "--mode",
        default=os.environ.get("MERIDIAN_TRADING_MODE", "paper"),
        choices=["paper", "live"],
        help="paper places no orders; live sends real orders to Angel One",
    )
    args = ap.parse_args(argv)

    eng = Engine(mode=args.mode)
    eng.install_signals()
    try:
        eng.connect()
        eng.bootstrap()
    except Exception as e:
        eng.tm.log(f"STARTUP FAILED: {type(e).__name__}: {e}", "critical")
        eng.publish_offline(f"startup failed: {e}")
        return 2
    try:
        return eng.run()
    finally:
        eng.publish_offline(eng.stop_reason or "exited")
        eng.db.expire_stale_commands()


if __name__ == "__main__":
    sys.exit(main())
