"""The week in one message, sent after the last session of the week closes.

Real-money trades only, with the figures the blotter shows: each trade's net as
reconciled against Angel's ledger (``ledger.reconcile``), so the message and the
dashboard can never give two different numbers for the same week.
"""

from __future__ import annotations

from datetime import date, timedelta

from shared.market_calendar import is_trading_day

SEND_AFTER = (15, 45)


def week_bounds(d: date) -> tuple[date, date]:
    """Monday and Friday of the week ``d`` falls in."""
    monday = d - timedelta(days=d.weekday())
    return monday, monday + timedelta(days=4)


def week_key(d: date) -> str:
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def last_trading_day_of_week(d: date, holidays: set[str]) -> date | None:
    """The last day the exchange trades in ``d``'s week (None if it never opens)."""
    monday, friday = week_bounds(d)
    day = friday
    while day >= monday:
        if is_trading_day(day, holidays):
            return day
        day -= timedelta(days=1)
    return None


def due(now, holidays: set[str]) -> bool:
    """After the close on the week's last trading day (a Thursday if Friday is a holiday)."""
    last = last_trading_day_of_week(now.date(), holidays)
    return last == now.date() and (now.hour, now.minute) >= SEND_AFTER


def _net(t: dict) -> float:
    v = t.get("net_angel")
    if v is None:
        v = t.get("net")
    if v is None:
        v = t.get("gross") or 0.0
    return float(v)


def summary(db, day: date) -> dict:
    """The week ``day`` falls in: real-money trades closed Monday to Friday."""
    from app import ledger

    monday, friday = week_bounds(day)
    lo, hi = monday.isoformat(), friday.isoformat()
    with db.conn() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT * FROM trades WHERE session_date >= ? AND session_date <= ? AND exit_ts IS NOT NULL "
            "ORDER BY entry_ts", (lo, hi))]
    live = [t for t in ledger.reconcile(db, rows) if t.get("mode") == "live"]
    paper = [t for t in rows if t.get("mode") != "live"]
    nets = [_net(t) for t in live]
    charges = sum(float(t.get("charges_angel") if t.get("charges_angel") is not None else (t.get("charges") or 0.0))
                  for t in live)
    wins = sum(1 for n in nets if n > 0)
    names = {a["id"]: a.get("name") or a["id"] for a in db.algos(include_retired=True)}
    algos = sorted({names.get(t.get("algo_id"), t.get("algo_id") or "") for t in live})
    return {
        "week": week_key(day), "from": lo, "to": hi,
        "trades": len(live), "wins": wins, "losses": len(live) - wins,
        "win_rate": round(wins / len(live), 4) if live else None,
        "net": round(sum(nets), 2), "charges": round(charges, 2),
        "gross": round(sum(float(t.get("gross") or 0.0) for t in live), 2),
        "best": round(max(nets), 2) if nets else None,
        "worst": round(min(nets), 2) if nets else None,
        "paper_trades": len(paper),
        "algos": algos,
    }


def _rupees(v: float, sign: bool = False) -> str:
    body = f"₹{abs(v):,.2f}"
    if v < 0:
        return "−" + body
    return ("+" + body) if sign and v > 0 else body


def _span(lo: str, hi: str) -> str:
    a, b = date.fromisoformat(lo), date.fromisoformat(hi)
    if a.month == b.month:
        return f"{a.day}–{b.day} {b:%b %Y}"
    return f"{a.day} {a:%b} – {b.day} {b:%b %Y}"


def text(s: dict, dashboard: str = "") -> str:
    """The message body. ``dashboard`` is the desk's address, for the tear-sheet link."""
    lines = [f"Week of {_span(s['from'], s['to'])}" + (f" · {', '.join(s['algos'])}" if s["algos"] else "")]
    if s["trades"]:
        lines.append(f"Net {_rupees(s['net'], sign=True)} after charges of {_rupees(s['charges'])}")
        lines.append(f"{s['trades']} real-money trade{'s' if s['trades'] != 1 else ''}: "
                     f"{s['wins']} won, {s['losses']} lost ({round(100 * s['win_rate'])}% win rate)")
        lines.append(f"Best {_rupees(s['best'], sign=True)} · worst {_rupees(s['worst'], sign=True)}")
    else:
        lines.append("No real-money trade closed this week.")
    if s["paper_trades"]:
        lines.append(f"Paper: {s['paper_trades']} trade{'s' if s['paper_trades'] != 1 else ''} (not in the figures above)")
    if dashboard:
        lines.append(f"Tear sheet: {dashboard.rstrip('/')}/reports")
    return "\n".join(lines)
