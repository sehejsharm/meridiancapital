"""A standalone program's own status file, turned into the desk's snapshot.

The built-in engine publishes a snapshot every two seconds, and every box on
the deck — equity, day P&L, the index strip, the card's position, the Angel
rate budget — is drawn from it. A program only prints, so those boxes stayed
empty while it traded. Programs that keep a status file beside themselves
(any ``*status*.json`` in their folder, rewritten as they run) now feed the
same snapshot: this reads the newest one and fills the engine's shape, with a
safe default for anything the program does not say.

Nothing here calls Angel. It reads what the program already wrote.
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

from engine.config import RATE_LIMITS as ENGINE_CAPS

PHASES = {
    "MARKET CLOSED", "IN POSITION", "HALTED", "SCANNING",
    "PRE-ENTRY WINDOW", "ENTRY WINDOW CLOSED",
}
# A status file this old belongs to a program that has stopped writing: the
# desk should say nothing rather than show numbers that stopped being true.
MAX_AGE_SEC = 120.0


def find_status_file(folder: Path) -> Path | None:
    """The status file the program is writing now: the newest of its kind."""
    try:
        files = [p for p in folder.glob("*status*.json") if p.is_file()]
    except OSError:
        return None
    if not files:
        return None
    return max(files, key=lambda p: p.stat().st_mtime)


def read_status(folder: Path, pid: int | None) -> dict | None:
    """The program's latest status, or None if there is none from this run."""
    path = find_status_file(folder)
    if path is None:
        return None
    try:
        if time.time() - path.stat().st_mtime > MAX_AGE_SEC:
            return None
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    # A file left by an earlier run of the program must not stand in for this one.
    if pid is not None and raw.get("pid") not in (None, pid):
        return None
    return raw


def _num(v) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and abs(f) != float("inf") else None


def _int(v, default: int = 0) -> int:
    f = _num(v)
    return int(f) if f is not None else default


def _api(raw: dict) -> dict | None:
    api = raw.get("api")
    if not isinstance(api, dict):
        return None
    calls = {str(k): _int(v) for k, v in (api.get("calls") or {}).items()}
    caps = api.get("caps") or {}
    recent = api.get("recent") or {}
    cooling = set(api.get("cooling_off") or [])
    window = _num(api.get("window_sec")) or 60.0
    endpoints = []
    for key in sorted(set(calls) | set(caps)):
        cap = _num(caps.get(key)) or _num(ENGINE_CAPS.get(key)) or 0.0
        rate = (_num(recent.get(key)) or 0.0) / window
        endpoints.append({
            "endpoint": key,
            "cap_per_sec": cap,
            "rate_per_sec": round(rate, 2),
            "utilisation": round(min(rate / cap, 1.0), 3) if cap else 0.0,
            "calls": calls.get(key, 0),
            "throttled": _int((api.get("throttled") or {}).get(key)),
            "cooling_off": key in cooling,
            "account_calls_this_second": None,
        })
    return {
        "calls": calls,
        "total_calls": sum(calls.values()),
        "waited_sec": round(_num(api.get("waited_sec")) or 0.0, 1),
        "throttles": _int(api.get("throttles")),
        "rejected": _int(api.get("rejected")),
        "per_min": _int(api.get("per_min")),
        "window_sec": window,
        "shared_budget": False,
        "shared_waited_sec": 0.0,
        "endpoints": endpoints,
        "peak_utilisation": round(max((e["utilisation"] for e in endpoints), default=0.0), 3),
    }


def _position(raw: dict, ts: str) -> dict | None:
    pos = raw.get("position")
    if not isinstance(pos, dict):
        return None
    side = str(pos.get("right") or pos.get("side") or "")
    strike = _int(pos.get("strike"))
    qty = _int(pos.get("qty"))
    entry = _num(pos.get("entry")) or _num(pos.get("entry_premium")) or 0.0
    live = _num(pos.get("live_premium"))
    legs = pos.get("exchange_orders") or {}
    sl = legs.get("sl") if isinstance(legs, dict) else None
    stop = _num(pos.get("stop_price")) or (_num(sl.get("trig")) if isinstance(sl, dict) else None) or 0.0
    stop_pct = round(100 * (1 - stop / entry), 2) if stop and entry else 0.0
    opened = str(pos.get("opened_ts") or pos.get("ts") or ts)
    hold = 0.0
    try:
        hold = max(0.0, (datetime.fromisoformat(ts) - datetime.fromisoformat(opened)).total_seconds() / 60)
    except ValueError:
        pass
    spot_entry = _num(pos.get("spot"))
    spot = _num(raw.get("spot"))
    move = None
    if spot_entry and spot:
        move = (spot - spot_entry) if side == "CE" else (spot_entry - spot)
    return {
        "tsym": str(pos.get("tsym") or f"NIFTY {strike} {side}".strip()),
        "side": side,
        "strike": strike,
        "expiry": str(pos.get("expiry") or "—"),
        "view": str(raw.get("signal") or ""),
        "lots": _int(pos.get("lots")),
        "qty": qty,
        "entry_premium": entry,
        "live_premium": live,
        "gain_pct": round(100 * (live / entry - 1), 2) if live is not None and entry else None,
        "peak_pct": _num(pos.get("peak_pct")) or 0.0,
        "unrealised": round((live - entry) * qty, 2) if live is not None and entry else None,
        "notional": round(entry * qty, 2),
        "stop_price": stop,
        "stop_pct": stop_pct,
        "stop_state": "resting at Angel" if stop else "held by the program",
        "target_pts": _num(pos.get("target_pts")) or 0.0,
        "index_move_pts": round(move, 1) if move is not None else None,
        "spot_entry": spot_entry or 0.0,
        "opened_ts": opened,
        "hold_min": round(hold, 1),
    }


def program_snapshot(raw: dict, *, name: str, mode: str, pid: int | None,
                     uptime_sec: float = 0.0) -> dict:
    """The deck's snapshot shape, filled from a program's status file."""
    ts = str(raw.get("ts") or datetime.now().isoformat(timespec="seconds"))
    equity = _num(raw.get("equity"))
    day = _num(raw.get("day_pnl"))
    start = (equity - day) if equity is not None and day is not None else equity
    realised_today = _num(raw.get("realised_today")) or 0.0
    realised_week = _num(raw.get("realised_week")) or 0.0
    spot, hi, lo = _num(raw.get("spot")), _num(raw.get("channel_high")), _num(raw.get("channel_low"))
    if spot is not None and hi is not None and lo is not None:
        state = "break_up" if spot > hi else "break_down" if spot < lo else "inside"
    else:
        state = "unknown"
    bar_close = _num(raw.get("bar_close"))
    guards = raw.get("guards") if isinstance(raw.get("guards"), dict) else {}
    phase = str(raw.get("phase") or "")
    win = raw.get("entry_window") if isinstance(raw.get("entry_window"), dict) else {}

    snap = {
        "ts": ts,
        "source": "program",
        "engine": {
            "version": str(raw.get("version") or "program"),
            "mode": "live" if mode == "live" else "paper",
            "phase": phase if phase in PHASES else "SCANNING",
            "pid": pid or _int(raw.get("pid")),
            "uptime_sec": round(uptime_sec, 1),
            "banner": name,
            "build": "standalone program",
        },
        "market": {
            "open": bool(raw.get("market_open")),
            "session_date": ts[:10],
            "entry_window": {
                "start": str(win.get("start") or "—"),
                "cutoff": str(win.get("cutoff") or "—"),
                "force_close": str(win.get("force_close") or "—"),
                "open_now": phase in ("SCANNING", "IN POSITION"),
            },
        },
        "account": {
            "equity": equity or 0.0,
            "start_equity": start or 0.0,
            "peak_equity": _num(raw.get("peak_equity")) or equity or 0.0,
            "week_start_equity": (equity - realised_week) if equity is not None else 0.0,
            "day_pl": day or 0.0,
            "day_pl_pct": round(100 * day / start, 2) if day is not None and start else 0.0,
            "realised_today": realised_today,
            "realised_week": realised_week,
            "drawdown_pct": _num(raw.get("drawdown_pct")) or 0.0,
            "currency": "INR",
        },
        "signal": {
            "spot": spot,
            "channel_high": hi,
            "channel_low": lo,
            "lookback": _int(raw.get("lookback"), 90),
            "view": str(raw.get("signal") or ""),
            "state": state,
            "room_up": round(hi - spot, 2) if hi is not None and spot is not None else None,
            "room_down": round(spot - lo, 2) if lo is not None and spot is not None else None,
            "bar_close": bar_close,
            "bar_age_sec": _num(raw.get("bar_age_sec")),
            "bars_loaded": _int(raw.get("bars_loaded")),
            "divergence_pts": round(abs(bar_close - spot), 1) if bar_close is not None and spot is not None else None,
            "divergence_limit": _num(raw.get("divergence_limit")) or 25.0,
            "next_strike": None,
        },
        "position": _position(raw, ts),
        "guards": {
            "halted": bool(raw.get("halted")),
            "week_halted": bool(raw.get("week_halted")),
            "locked_profit": bool(raw.get("locked_profit")),
            "trades_today": _int(raw.get("trades_today")),
            "max_trades_day": _int(raw.get("max_trades"), 1),
            "consec_losses": _int(raw.get("consec_losses")),
            "consec_loss_halt": _int(guards.get("streak_halt")),
            "daily_loss_used": max(0.0, -realised_today),
            "daily_loss_limit": _num(guards.get("daily_kill")) or 0.0,
            "weekly_loss_used": max(0.0, -realised_week),
            "weekly_loss_limit": _num(guards.get("weekly_kill")) or 0.0,
            "profit_lock_progress": max(0.0, realised_today),
            "profit_lock_target": _num(guards.get("profit_lock")) or 0.0,
            "drawdown_stop": (_num(guards.get("dd_halt_pct")) or 0.0) / 100,
            "min_capital": _num(guards.get("capital_floor")) or 0.0,
            "capital_ok": True,
            "deploy_fraction": _num(guards.get("deploy_fraction")) or 0.0,
            "per_trade_equity_cap": _num(guards.get("equity_cap")) or 0.0,
            "per_trade_risk_rs": _num(guards.get("per_trade_risk")) or 0.0,
            "max_lots": _int(guards.get("max_lots")),
        },
        "health": {
            "clock_drift_sec": None,
            "broker_connected": str(raw.get("state") or "RUNNING").upper() == "RUNNING",
            "broker_client_id": None,
            "api": _api(raw),
            "contracts_loaded": 0,
            "telemetry_dropped": 0,
            "last_error": None,
        },
    }
    return snap


def live_snapshot_key(db, fleet) -> str:
    """Which algorithm's snapshot the desk shows.

    The built-in's while it runs, or when nothing runs — as before. Otherwise
    the running algorithm that published most recently, so a desk running only
    an uploaded program shows that program's numbers instead of the built-in's
    last, stale ones.
    """
    from shared.db import DEFAULT_ALGO, K_SNAPSHOT, snapshot_key

    if fleet is None:
        return K_SNAPSHOT
    running = [aid for aid, sup in fleet.all().items() if sup.state.running]
    if not running or DEFAULT_ALGO in running:
        return K_SNAPSHOT
    best, best_ts = None, ""
    for aid in running:
        snap = db.kv_get(snapshot_key(aid), None) or {}
        ts = str(snap.get("ts") or "")
        if ts > best_ts:
            best, best_ts = aid, ts
    return snapshot_key(best) if best else K_SNAPSHOT
