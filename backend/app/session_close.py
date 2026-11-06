"""The last closed session of a standalone program, as the desk's end-of-day record.

The built-in engine writes its end-of-day record into the database as it closes
the session (``report:eod:...``); the journal's "Last end-of-day report" card and
the deck's closing figures read it. A standalone program only *prints* its end
of day — a block headed ``END OF DAY · <banner> · <date>`` — which the output
follower files line by line in the journal. Nothing turned that block into the
record, so with only a program trading the card said "No session has been closed
out yet" a few lines below the program's own complete report for the day
(6 Nov 2026), and the deck had nothing to fall back on once the program stopped.

This reads the block back out of the journal, takes the trades for that session
from the trades table (booked to the paisa from the program's trade log), and
stores the same record the built-in writes. It is the one place "the most recent
closed session" of a program is worked out; the journal card and the deck's
figures both read what it stores.
"""

from __future__ import annotations

import re
from typing import Any

from shared.db import snapshot_key

# "END OF DAY  ·  गणेश कवच  ·  GANESH KAVACH 50K  ·  Config #5 v4  ·  2026-11-06"
EOD_HEADER = re.compile(r"END OF DAY\s+·\s+(?P<banner>.*?)\s*·\s*(?P<date>\d{4}-\d{2}-\d{2})\s*$")
_PREFIX = re.compile(r"^\[\d{2}:\d{2}(?::\d{2})?\]\s*")
_KV = re.compile(r"^(?P<k>[A-Za-z][A-Za-z&/() ]*?)\s{2,}(?P<v>\S.*)$")
# "Rs 45,875", "-Rs 1,234" (the program's money()), "Rs -5,931.250" (its 3-decimal figures)
_MONEY = re.compile(r"(?P<pre>-)?\s*Rs\.?\s*(?P<sign>-)?(?P<n>[\d,]+(?:\.\d+)?)")
_PCT = re.compile(r"(?P<n>[+-]?\d+(?:\.\d+)?)\s*%")
_INT = re.compile(r"-?\d+")
# The block's last key: after it come the per-trade table and the closing bar.
_LAST_KEYS = ("Drawdown from peak", "Day P&L")
MAX_BLOCK_LINES = 40
REPORT_SOURCE = "program end-of-day report"


def _money(text: str) -> float | None:
    m = _MONEY.search(text or "")
    if not m:
        return None
    n = float(m.group("n").replace(",", ""))
    return -n if (m.group("pre") or m.group("sign")) else n


def _monies(text: str) -> list[float]:
    out = []
    for m in _MONEY.finditer(text or ""):
        n = float(m.group("n").replace(",", ""))
        out.append(-n if (m.group("pre") or m.group("sign")) else n)
    return out


def parse_eod_block(lines: list[str]) -> dict[str, Any]:
    """The figures in a program's printed end-of-day block, as far as it got.

    ``lines`` start just after the header. Money the program prints to the rupee
    ("Rs 45,875") comes back as that; the trades table has the paisa.
    """
    out: dict[str, Any] = {}
    for raw in lines[:MAX_BLOCK_LINES]:
        line = _PREFIX.sub("", (raw or "").strip())
        m = _KV.match(line)
        if not m:
            continue
        k, v = m.group("k").strip(), m.group("v").strip()
        if k == "Trades":
            nums = [int(x) for x in _INT.findall(v)]
            if nums:
                out["trades"] = nums[0]
            w = re.search(r"(\d+)\s*win", v)
            lo = re.search(r"(\d+)\s*loss", v)
            if w:
                out["wins"] = int(w.group(1))
            if lo:
                out["losses"] = int(lo.group(1))
        elif k == "Trades today":
            nums = [int(x) for x in _INT.findall(v)]
            out["trades"] = nums[0] if nums else 0
            out.setdefault("wins", 0)
            out.setdefault("losses", 0)
        elif k == "Win rate":
            p = _PCT.search(v)
            if p:
                out["win_rate"] = round(float(p.group("n")) / 100, 4)
        elif k.startswith("P&L"):
            out["gross"] = _money(v)
        elif k.startswith("Charges"):
            out["charges"] = _money(v)
            out["charges_pending"] = "pending" in v
        elif k == "Net P&L":
            out["net"] = _money(v)
        elif k == "Best / worst trade":
            vals = _monies(v)
            if len(vals) >= 2:
                out["best"], out["worst"] = vals[0], vals[1]
        elif k == "Avg hold":
            nums = _INT.findall(v)
            if nums:
                out["avg_hold_min"] = float(nums[0])
        elif k == "Start equity":
            out["start_equity"] = _money(v)
        elif k == "End equity":
            out["end_equity"] = _money(v)
        elif k == "Day P&L":
            out["day_pl"] = _money(v)
        elif k == "Drawdown from peak":
            p = _PCT.search(v)
            if p:
                out["drawdown_pct"] = round(float(p.group("n")) / 100, 4)
        if k in _LAST_KEYS:
            out["complete"] = True
            break
    return out


def _closed_trades(db, algo_id: str, session_date: str) -> list[dict]:
    rows = db.trades(limit=200, session_date=session_date, algo_id=algo_id)
    return [r for r in rows if r.get("exit_ts")]


def _round(x: float | None, nd: int = 2) -> float | None:
    return None if x is None else round(float(x), nd)


def build_report(db, algo_id: str, header: dict, block: dict[str, Any]) -> dict | None:
    """The end-of-day record for the session ``header`` opens."""
    m = EOD_HEADER.search(header.get("message") or "")
    if not m:
        return None
    day = m.group("date")
    rows = _closed_trades(db, algo_id, day)
    snap = db.kv_get(snapshot_key(algo_id), None) or {}
    acct = snap.get("account") or {}
    money = acct.get("money") or {}
    same_day = str(snap.get("ts") or "")[:10] == day

    if rows:
        nets = [float(r.get("net") if r.get("net") is not None else (r.get("gross") or 0.0)) for r in rows]
        wins = [n for n in nets if n > 0]
        trades, n_wins = len(rows), len(wins)
        gross = sum(float(r.get("gross") or 0.0) for r in rows)
        charges = sum(float(r.get("charges") or 0.0) for r in rows)
        net = sum(nets)
        best, worst = max(nets), min(nets)
        holds = [float(r["hold_min"]) for r in rows if r.get("hold_min") is not None]
        avg_hold = sum(holds) / len(holds) if holds else float(block.get("avg_hold_min") or 0.0)
        mode = rows[-1].get("mode") or (snap.get("engine") or {}).get("mode") or "live"
        booked = next((float(r["equity"]) for r in sorted(rows, key=lambda r: str(r.get("exit_ts")), reverse=True)
                       if r.get("equity") is not None), None)
    else:
        trades = int(block.get("trades") or 0)
        n_wins = int(block.get("wins") or 0)
        gross = float(block.get("gross") or 0.0)
        charges = float(block.get("charges") or 0.0)
        net = float(block["net"]) if block.get("net") is not None else float(block.get("day_pl") or 0.0)
        best = float(block.get("best") or 0.0)
        worst = float(block.get("worst") or 0.0)
        avg_hold = float(block.get("avg_hold_min") or 0.0)
        mode = (snap.get("engine") or {}).get("mode") or "live"
        booked = None

    def positive(x):
        try:
            return float(x) if x is not None and float(x) > 0 else None
        except (TypeError, ValueError):
            return None

    # The closing figure, most exact first: what the session's last sale left
    # (booked to the paisa), Angel's balance on the program's last flat status,
    # the program's equity on it, and the block's own figure (to the rupee).
    end = (booked
           or (positive(money.get("balance")) if same_day and snap.get("position") is None else None)
           or (positive(acct.get("equity")) if same_day else None)
           or positive(block.get("end_equity")))
    start = ((positive(money.get("opening_balance")) if same_day else None)
             or (positive(acct.get("start_equity")) if same_day else None))
    if start is None:
        printed = positive(block.get("start_equity"))
        # The block prints the opening to the rupee; with the close and the net
        # to the paisa, the opening they imply is the exact one when it agrees.
        if end is not None and (printed is None or abs((end - net) - printed) < 1.0):
            start = end - net
        else:
            start = printed
    day_pl = (end - start) if (end is not None and start is not None) else net

    return {
        "session_date": day,
        "algo_id": algo_id,
        "mode": mode,
        "trades": trades,
        "wins": n_wins,
        "losses": trades - n_wins,
        "win_rate": (n_wins / trades) if trades else 0.0,
        "gross": _round(gross),
        "charges": _round(charges),
        "net": _round(net),
        "best": _round(best),
        "worst": _round(worst),
        "avg_hold_min": round(avg_hold, 1),
        "start_equity": _round(start) or 0.0,
        "end_equity": _round(end) or 0.0,
        "day_pl": _round(day_pl),
        "drawdown_pct": float(block.get("drawdown_pct") or 0.0),
        "charges_pending": bool(block.get("charges_pending")),
        "blotter": rows,
        "source": REPORT_SOURCE,
        "header_event_id": header.get("id"),
        "closed_ts": header.get("ts"),
    }


def store_report(db, report: dict) -> None:
    """Kept as the built-in keeps its own: per algorithm, and the desk's latest."""
    day, algo = report["session_date"], report["algo_id"]
    db.kv_set(f"report:eod:{day}:{algo}", report)
    db.kv_set(f"report:eod:latest:{algo}", report)
    latest = db.kv_get("report:eod:latest", None) or {}
    if str(latest.get("session_date") or "") <= day:
        db.kv_set(f"report:eod:{day}", report)
        db.kv_set("report:eod:latest", report)


def repair_snapshot(db, algo_id: str, report: dict) -> bool:
    """Give a figureless last snapshot the session's closing figures.

    A program writes a last status on its way out that carries no money at all
    (``state: STOPPED``). Until 6 Nov 2026 the follower published it like any
    other, turning the missing figures into Rs 0.00: the deck read "Equity
    ₹0.00 · Day's trading P&L ₹0.00" after a +₹3,847.64 day. The follower no
    longer publishes such a status; this mends a record one already overwrote.
    """
    key = snapshot_key(algo_id)
    snap = db.kv_get(key, None)
    if not isinstance(snap, dict):
        return False
    acct = snap.get("account") or {}
    money = acct.get("money") or {}
    has_money = isinstance(money, dict) and money.get("balance") not in (None, 0, 0.0)
    if acct.get("equity") not in (None, 0, 0.0) or has_money:
        return False
    if str(snap.get("ts") or "")[:10] > report["session_date"] or not report.get("end_equity"):
        return False
    start, end = report.get("start_equity") or 0.0, report["end_equity"]
    day = report.get("day_pl") if report.get("day_pl") is not None else report.get("net") or 0.0
    acct.update(
        equity=end, start_equity=start, day_pl=day,
        day_pl_pct=round(100 * day / start, 2) if start else 0.0,
        realised_today=report.get("gross") or 0.0,
        peak_equity=max(float(acct.get("peak_equity") or 0.0), end),
    )
    acct["money"] = None
    snap["account"] = acct
    snap["position"] = None
    snap["recovered_from"] = REPORT_SOURCE
    db.kv_set(key, snap)
    return True


def latest_header(db, algo_id: str) -> dict | None:
    hits = db.find_events(algo_id, "END OF DAY", limit=5)
    return next((e for e in hits if EOD_HEADER.search(e.get("message") or "")), None)


def refresh(db, algo_id: str, *, force: bool = False) -> dict | None:
    """Rebuild the program's last end-of-day record if its journal has a newer one.

    Returns the record when one was (re)stored, else None. Cheap to call: one
    indexed lookup when nothing is new.
    """
    header = latest_header(db, algo_id)
    if header is None:
        return None
    stored = db.kv_get(f"report:eod:latest:{algo_id}", None) or {}
    if not force and stored.get("header_event_id") == header.get("id"):
        return None
    following = db.events_following(algo_id, header["id"], limit=MAX_BLOCK_LINES)
    block = parse_eod_block([e.get("message") or "" for e in following])
    report = build_report(db, algo_id, header, block)
    if report is None:
        return None
    # A session already on record from the built-in or a later block wins.
    if stored and str(stored.get("session_date") or "") > report["session_date"]:
        return None
    store_report(db, report)
    repair_snapshot(db, algo_id, report)
    return report
