"""The research recorder: each trade's price path, kept for study and for the trade page.

The exit studies ran on two years of historical one-minute prices. What they
could not see is the live program's own trades: how each one's premium moved
between entry and exit, what the fill was against the price just before it, and
what the other exit rule sets would have done on that exact path. Three sources,
none of which costs an Angel request during market hours:

* **ticks** — the open position's premium as the program itself read it, taken
  from the status it writes every five seconds (``position_ticks``). Recorded
  while a position is open, for real-money and paper runs alike.
* **candles** — after the close, the day's one-minute prices of the contracts
  traded and of the contracts a breakout could have bought in the two nearest
  expiries (``app.angel_after_close``; needs the Angel credentials in the
  server's settings). These continue past the live exit, which is what a
  comparison of exit rules needs.
* **fills** — Angel's order ids and fill prices for the day (same job).

Everything here is read-only and for display; it decides nothing about a trade.
"""

from __future__ import annotations

import csv
import gzip
import io
import json
from pathlib import Path

from app import exit_rules

TICK_GAP_SEC = 15.0
LAST_BAR = "15:09"


def folder(db) -> Path:
    return Path(db.path).parent / "research"


# ── recording ────────────────────────────────────────────────────────────────

class TickRecorder:
    """Writes the open position's premium at most every ``TICK_GAP_SEC``, and always
    the first reading of a new minute, a new best price or a moved stop."""

    def __init__(self, db):
        self.db = db
        self._last: dict[str, dict] = {}

    def record(self, algo_id: str, snap: dict, mode: str | None, now: float) -> bool:
        pos = snap.get("position") if isinstance(snap, dict) else None
        if not isinstance(pos, dict) or pos.get("live_premium") is None or not pos.get("opened_ts"):
            self._last.pop(algo_id, None)
            return False
        ts = str(snap.get("ts") or "")
        if len(ts) < 16:
            return False
        last = self._last.get(algo_id)
        key = (pos["opened_ts"], ts[:16], pos.get("peak_pct"), pos.get("stop_price"))
        if last and last["opened"] == pos["opened_ts"]:
            same = last["key"] == key
            if same and now - last["at"] < TICK_GAP_SEC:
                return False
            if last["ts"] == ts:
                return False
        self.db.add_tick({
            "ts": ts[:19], "session_date": ts[:10], "algo_id": algo_id, "mode": mode,
            "tsym": pos.get("tsym"), "side": pos.get("side"), "strike": pos.get("strike"),
            "expiry": pos.get("expiry"), "opened_ts": str(pos["opened_ts"]),
            "entry": pos.get("entry_premium"), "premium": float(pos["live_premium"]),
            "peak_pct": pos.get("peak_pct"), "stop_price": pos.get("stop_price") or None,
            "spot": (snap.get("signal") or {}).get("spot"),
        })
        self._last[algo_id] = {"opened": pos["opened_ts"], "key": key, "at": now, "ts": ts}
        return True


# ── reading ──────────────────────────────────────────────────────────────────

def _minute(ts) -> str:
    """'2026-11-10T11:20:07' -> '11:20'."""
    return str(ts or "")[11:16]


def minute_closes(ticks: list[dict]) -> list[tuple[str, float]]:
    """The last premium recorded in each minute, in order."""
    out: dict[str, float] = {}
    for t in ticks:
        out[_minute(t["ts"])] = float(t["premium"])
    return sorted(out.items())


def candles(db, day: str) -> dict | None:
    """The day's kept one-minute prices (after-close job), or None."""
    path = folder(db) / f"candles-{day}.json.gz"
    try:
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError, EOFError):
        return None
    return data if isinstance(data, dict) else None


def candle_days(db) -> list[str]:
    try:
        return sorted(p.name[8:18] for p in folder(db).glob("candles-*.json.gz"))
    except OSError:
        return []


def contract_bars(day_candles: dict | None, tsym: str | None) -> list[list] | None:
    if not day_candles or not tsym:
        return None
    for c in day_candles.get("contracts") or []:
        if str(c.get("tsym") or "").upper() == tsym.upper():
            return c.get("bars") or None
    return None


def trade_ticks(db, trade: dict) -> list[dict]:
    """The ticks recorded for a trade. The program stamps the position's opening
    time itself; the trade log's entry time is the same moment to the minute."""
    algo, entry = trade.get("algo_id") or "", str(trade.get("entry_ts") or "")
    exact = db.ticks(algo, entry)
    if exact:
        return exact
    for row in db.tick_trades(limit=4000):
        if row["algo_id"] == algo and str(row["opened_ts"])[:16].replace(" ", "T") == entry[:16].replace(" ", "T"):
            return db.ticks(algo, row["opened_ts"])
    return []


def opened_index(db) -> dict[tuple[str, str], str]:
    """(algo, entry minute) -> the program's own opening timestamp, for every recorded path. Built once per
    report: looked up per trade, it was a scan of every tick for each trade whose times differ by seconds."""
    return {(r["algo_id"], str(r["opened_ts"])[:16].replace(" ", "T")): r["opened_ts"] for r in db.tick_trades(limit=4000)}


_DIGESTS: dict[tuple, list[tuple]] = {}
DIGEST_CACHE_TRADES = 800          # about three years of trades; ~130 readings each


def _digest(rows: list[tuple], exit_ts: str) -> list[tuple]:
    """The readings the reports can tell apart: the last of each minute, and the last at or before the exit.
    minute_closes() and execution() give exactly the same answers on this as on every reading."""
    last_of_min: dict[str, tuple] = {}
    at_exit = None
    for r in rows:
        last_of_min[str(r[0])[:16]] = r
        if exit_ts and str(r[0]) <= exit_ts:
            at_exit = r
    keep = set(last_of_min.values())
    if at_exit is not None:
        keep.add(at_exit)
    return sorted(keep, key=lambda r: str(r[0]))


def lean_ticks(db, trade: dict, index: dict | None = None) -> list[dict]:
    """A trade's path with the three fields the reports read (ts, premium, stop_price), oldest first,
    reduced to what they can tell apart. A closed trade from an earlier day never changes, so its
    reduced path is kept in memory: the Analytics page no longer re-reads a year of readings each visit."""
    from engine.clock import now_ist

    algo, entry = trade.get("algo_id") or "", str(trade.get("entry_ts") or "")
    exit_ts = str(trade.get("exit_ts") or "").replace(" ", "T")[:19]
    settled = bool(exit_ts) and str(trade.get("session_date") or "") < now_ist().date().isoformat()
    key = (str(getattr(db, "path", id(db))), algo, entry, exit_ts)
    if settled and key in _DIGESTS:
        rows = _DIGESTS[key]
    else:
        rows = db.tick_path(algo, entry)
        if not rows:
            opened = (index if index is not None else opened_index(db)).get((algo, entry[:16].replace(" ", "T")))
            rows = db.tick_path(algo, opened) if opened else []
        rows = _digest(rows, exit_ts)
        if settled and rows:
            if len(_DIGESTS) >= DIGEST_CACHE_TRADES:
                _DIGESTS.pop(next(iter(_DIGESTS)))
            _DIGESTS[key] = rows
    return [{"ts": ts, "premium": prem, "stop_price": stop} for ts, prem, stop in rows]


def path(db, trade: dict, ticks: list[dict] | None = None, day_candles: dict | None = None,
         index: dict | None = None) -> dict:
    """A trade's minute-by-minute path after its entry minute.

    From Angel's candles when they were kept (the whole day, so it runs past the
    live exit), else from the recorded ticks (which stop at the live exit).
    """
    entry_min = _minute(trade.get("entry_ts"))
    day = str(trade.get("session_date") or "")
    bars = contract_bars(day_candles if day_candles is not None else candles(db, day), trade.get("tsym"))
    if bars:
        after = [(b[0], float(b[4])) for b in bars if entry_min < b[0] <= LAST_BAR]
        if after:
            return {"source": "Angel one-minute candles", "complete": True, "bars": after}
    ticks = ticks if ticks is not None else lean_ticks(db, trade, index)
    after = [(m, p) for m, p in minute_closes(ticks) if entry_min < m <= LAST_BAR]
    return {"source": "the program's own readings" if after else None, "complete": False, "bars": after}


# ── execution quality ────────────────────────────────────────────────────────

def execution(db, trade: dict, ticks: list[dict] | None = None, day_candles: dict | None = None) -> dict | None:
    """The exit fill against the last price the program saw and against that minute's close.

    Negative is worse than the reference (sold lower). None when nothing was
    recorded for the trade.
    """
    if not trade.get("exit_ts") or trade.get("exit_prem") is None:
        return None
    ticks = ticks if ticks is not None else trade_ticks(db, trade)
    qty = int(trade.get("qty") or 0)
    fill = float(trade["exit_prem"])
    exit_ts = str(trade["exit_ts"]).replace(" ", "T")[:19]
    before = [t for t in ticks if str(t["ts"]) <= exit_ts] or ticks
    last = before[-1] if before else None
    bars = contract_bars(day_candles if day_candles is not None else candles(db, str(trade.get("session_date"))),
                         trade.get("tsym"))
    exit_min, entry_min = _minute(exit_ts), _minute(trade.get("entry_ts"))
    by_min = {b[0]: float(b[4]) for b in bars} if bars else {}
    minute_close = by_min.get(exit_min)
    entry_close = by_min.get(entry_min)
    if last is None and minute_close is None:
        return None
    out = {
        "trade_id": trade.get("id"), "session_date": trade.get("session_date"), "tsym": trade.get("tsym"),
        "reason": trade.get("reason"), "qty": qty, "exit_fill": fill, "exit_time": exit_ts[11:19],
        "last_seen": float(last["premium"]) if last else None,
        "last_seen_time": str(last["ts"])[11:19] if last else None,
        "stop_then": last.get("stop_price") if last else None,
        "minute_close": minute_close,
        "vs_last_seen": None, "vs_last_seen_rs": None, "vs_minute_close": None, "vs_minute_close_rs": None,
        "entry_fill": trade.get("entry_prem"), "entry_minute_close": entry_close,
        "entry_vs_minute_close": None, "entry_vs_minute_close_rs": None,
    }
    if last:
        d = fill - float(last["premium"])
        out["vs_last_seen"], out["vs_last_seen_rs"] = round(d, 2), round(d * qty, 2)
    if minute_close is not None:
        d = fill - minute_close
        out["vs_minute_close"], out["vs_minute_close_rs"] = round(d, 2), round(d * qty, 2)
    if entry_close is not None and trade.get("entry_prem") is not None:
        # Paying more than the minute's close is worse: shown negative, like the exit.
        d = entry_close - float(trade["entry_prem"])
        out["entry_vs_minute_close"], out["entry_vs_minute_close_rs"] = round(d, 2), round(d * qty, 2)
    return out


def execution_report(db, trades: list[dict]) -> dict:
    rows = []
    cache: dict[str, dict | None] = {}
    index = opened_index(db)
    for t in trades:
        day = str(t.get("session_date") or "")
        if day not in cache:
            cache[day] = candles(db, day)
        e = execution(db, t, ticks=lean_ticks(db, t, index), day_candles=cache[day])
        if e:
            rows.append(e)

    def total(key):
        vals = [r[key] for r in rows if r.get(key) is not None]
        return {"trades": len(vals), "total_rs": round(sum(vals), 2),
                "average_rs": round(sum(vals) / len(vals), 2) if vals else None}

    by_reason: dict[str, list[float]] = {}
    for r in rows:
        if r.get("vs_last_seen_rs") is not None:
            by_reason.setdefault(str(r.get("reason") or "?"), []).append(r["vs_last_seen_rs"])
    return {
        "rows": rows,
        "exit_vs_last_seen": total("vs_last_seen_rs"),
        "exit_vs_minute_close": total("vs_minute_close_rs"),
        "entry_vs_minute_close": total("entry_vs_minute_close_rs"),
        "by_reason": [{"reason": k, "trades": len(v), "average_rs": round(sum(v) / len(v), 2),
                       "total_rs": round(sum(v), 2)} for k, v in sorted(by_reason.items())],
        "trades_without_a_path": len(trades) - len(rows),
    }


# ── the exit rule sets on live trades ────────────────────────────────────────

def rules_on_trade(db, trade: dict, rulesets=exit_rules.STANDARD, day_candles: dict | None = None,
                   index: dict | None = None) -> dict:
    p = path(db, trade, day_candles=day_candles, index=index)
    entry = float(trade.get("entry_prem") or 0.0)
    qty = int(trade.get("qty") or exit_rules.LOT) or exit_rules.LOT
    out = {"trade_id": trade.get("id"), "session_date": trade.get("session_date"), "tsym": trade.get("tsym"),
           "mode": trade.get("mode"), "entry": entry, "entry_time": _minute(trade.get("entry_ts")),
           "actual": {"exit": trade.get("exit_prem"), "reason": trade.get("reason"),
                      "exit_time": _minute(trade.get("exit_ts")), "net": trade.get("net")},
           "path": {"source": p["source"], "complete": p["complete"], "minutes": len(p["bars"])},
           "rules": {}}
    if not p["bars"] or entry <= 0:
        return out
    last_min = p["bars"][-1][0]
    for rs in rulesets:
        r = exit_rules.replay(rs, entry, out["entry_time"], p["bars"])
        if r is None:
            continue
        if qty != exit_rules.LOT:
            gross = (r["exit"] - entry) * qty
            ch = exit_rules.charges(entry, r["exit"], qty)
            r = {**r, "gross": round(gross, 2), "charges": round(ch, 2), "net": round(gross - ch, 2)}
        # A path that stops at the live exit cannot say what a rule still holding would have done.
        r["decided"] = p["complete"] or not (r["reason"] == "EOD" and last_min < LAST_BAR)
        out["rules"][rs.name] = r
    return out


def rules_report(db, trades: list[dict], rulesets=exit_rules.STANDARD) -> dict:
    """Each rule set on every recorded trade. Totals count only trades every rule set
    could be decided on, so the rule sets are compared on the same trades."""
    cache: dict[str, dict | None] = {}
    rows = []
    index = opened_index(db)
    for t in trades:
        if not t.get("exit_ts"):
            continue
        day = str(t.get("session_date") or "")
        if day not in cache:
            cache[day] = candles(db, day)
        rows.append(rules_on_trade(db, t, rulesets, cache[day], index))
    names = [rs.name for rs in rulesets]
    comparable = [r for r in rows if all(n in r["rules"] and r["rules"][n]["decided"] for n in names)]
    totals = {n: {"net": round(sum(r["rules"][n]["net"] for r in comparable), 2),
                  "wins": sum(1 for r in comparable if r["rules"][n]["net"] > 0)} for n in names}
    actual = [r["actual"]["net"] for r in comparable if r["actual"]["net"] is not None]
    return {
        "rules": names,
        "rows": rows,
        "comparable_trades": len(comparable),
        "recorded_trades": sum(1 for r in rows if r["path"]["minutes"]),
        "trades": len(rows),
        "totals": totals,
        "actual_net": round(sum(actual), 2) if actual else None,
        "enough": len(comparable) >= 100,
        "note": ("Rule sets are compared on trades whose whole path is on record. The first 100 such trades "
                 "decide whether v10 stays: if it trails v9 on them, drop it."),
    }


# ── summary and export ───────────────────────────────────────────────────────

def summary(db) -> dict:
    recorded = db.tick_trades(limit=4000)
    days = candle_days(db)
    return {
        "paths": len(recorded),
        "real_money_paths": sum(1 for r in recorded if r.get("mode") == "live"),
        "first_path": min((r["session_date"] for r in recorded), default=None),
        "last_path": max((r["session_date"] for r in recorded), default=None),
        "ticks": sum(int(r["ticks"]) for r in recorded),
        "candle_days": len(days),
        "first_candle_day": days[0] if days else None,
        "last_candle_day": days[-1] if days else None,
    }


TICK_COLUMNS = ("ts", "session_date", "algo_id", "mode", "tsym", "side", "strike", "expiry", "opened_ts",
                "entry", "premium", "gain_pct", "peak_pct", "stop_price", "spot")


def ticks_csv(db, date_from: str, date_to: str) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(TICK_COLUMNS)
    for t in db.ticks_between(date_from, date_to):
        entry = t.get("entry")
        gain = round(100 * (float(t["premium"]) / float(entry) - 1), 2) if entry else ""
        w.writerow([t.get(k) if k != "gain_pct" else gain for k in TICK_COLUMNS])
    return buf.getvalue()


def candles_csv(db, day: str) -> str | None:
    """One day's kept contract prices as rows: contract, expiry, strike, right, days to expiry, minute, OHLCV."""
    data = candles(db, day)
    if not data:
        return None
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(("day", "tsym", "expiry", "strike", "right", "dte", "traded", "minute", "open", "high", "low",
                "close", "volume"))
    for b in data.get("index") or []:
        w.writerow((day, "NIFTY", "", "", "", "", "", b[0], b[1], b[2], b[3], b[4], ""))
    for c in data.get("contracts") or []:
        for b in c.get("bars") or []:
            w.writerow((day, c.get("tsym"), c.get("expiry"), c.get("strike"), c.get("right"), c.get("dte"),
                        "yes" if c.get("traded") else "", *b[:6]))
    return buf.getvalue()


# ── one trade, everything about it ───────────────────────────────────────────

MAX_DETAIL_TICKS = 900
JOURNAL_PAD_MIN = 3


def _thin(ticks: list[dict], limit: int = MAX_DETAIL_TICKS) -> list[dict]:
    if len(ticks) <= limit:
        return ticks
    step = len(ticks) / limit
    picked = [ticks[int(i * step)] for i in range(limit)]
    if picked[-1] is not ticks[-1]:
        picked.append(ticks[-1])
    return picked


def trade_detail(db, trade_id: int) -> dict | None:
    from datetime import datetime, timedelta

    from app import ledger

    raw = db.trade_by_id(trade_id)
    if raw is None:
        return None
    trade = ledger.reconcile(db, [raw])[0]
    day = str(trade.get("session_date") or "")
    day_candles = candles(db, day)
    ticks = trade_ticks(db, trade)
    bars = contract_bars(day_candles, trade.get("tsym"))
    events = []
    try:
        lo = datetime.fromisoformat(str(trade["entry_ts"]).replace(" ", "T")[:19]) - timedelta(minutes=JOURNAL_PAD_MIN)
        end = trade.get("exit_ts") or trade["entry_ts"]
        hi = datetime.fromisoformat(str(end).replace(" ", "T")[:19]) + timedelta(minutes=JOURNAL_PAD_MIN)
        for e in reversed(db.events(limit=4000, algo_id=trade.get("algo_id"), day=day)):
            ts = str(e.get("ts") or "")[:19]
            if lo.isoformat() <= ts <= hi.isoformat():
                events.append({k: e.get(k) for k in ("id", "ts", "level", "source", "message")})
    except (ValueError, KeyError):
        pass
    fills = [f for f in (db.kv_get(f"angel:fills:{day}", None) or [])
             if str(f.get("tsym") or "").upper() == str(trade.get("tsym") or "").upper()]
    algo = db.algo(trade.get("algo_id") or "") or {}
    from app import trade_greeks

    return {
        "trade": trade,
        "greeks": trade_greeks.for_trade(db, trade),
        "algo_name": algo.get("name") or trade.get("algo_id"),
        "ticks": [{"ts": t["ts"], "premium": t["premium"], "peak_pct": t.get("peak_pct"),
                   "stop_price": t.get("stop_price"), "spot": t.get("spot")} for t in _thin(ticks)],
        "tick_count": len(ticks),
        "bars": bars,
        "bars_source": "Angel one-minute candles, kept after the close" if bars else None,
        "events": events[-400:],
        "fills": fills,
        "fills_checked": db.kv_get(f"closecheck:{day}", None) is not None,
        "execution": execution(db, trade, ticks=ticks, day_candles=day_candles),
        "rules": rules_on_trade(db, trade, day_candles=day_candles) if trade.get("exit_ts") else None,
    }
