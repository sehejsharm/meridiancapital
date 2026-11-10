"""A one-page tear sheet for a week or a month: the numbers, the curve, the trades.

Built on the session report's payload (reports.build_payload), so its figures
are the Reports page's figures: one account's trades, Angel's charges and net
where reconciled, drawdown on trading alone. On top of that it adds the daily
view a weekly review wants: each session's result, the best and worst day,
Sharpe and Sortino on daily results, and why trades closed.
"""

from __future__ import annotations

import calendar
import math
from collections import Counter
from datetime import date, timedelta

from app import pdf, reports

PERIODS = ("week", "month")
TRADING_DAYS = 252
# Below this many sessions the ratios are printed with a warning beside them.
RATIO_MIN_DAYS = 20


def period_bounds(period: str, anchor: str) -> tuple[str, str, str]:
    """The week (Monday to Sunday) or calendar month containing ``anchor``, and its label."""
    d = date.fromisoformat(anchor)
    if period == "week":
        start = d - timedelta(days=d.weekday())
        end = start + timedelta(days=6)
        return start.isoformat(), end.isoformat(), f"Week of {start.day} {start:%b %Y}"
    if period == "month":
        last = calendar.monthrange(d.year, d.month)[1]
        return date(d.year, d.month, 1).isoformat(), date(d.year, d.month, last).isoformat(), f"{d:%B %Y}"
    raise ValueError("period must be week or month")


def ratios(day_nets: list[float]) -> dict:
    """Annualised Sharpe and Sortino on daily results (scale-free: rupees or returns give the same)."""
    n = len(day_nets)
    out = {"days": n, "sharpe": None, "sortino": None, "few_days": n < RATIO_MIN_DAYS}
    if n < 2:
        return out
    mean = sum(day_nets) / n
    var = sum((x - mean) ** 2 for x in day_nets) / (n - 1)
    if var > 0:
        out["sharpe"] = round(mean / math.sqrt(var) * math.sqrt(TRADING_DAYS), 2)
    downside = math.sqrt(sum(min(x, 0.0) ** 2 for x in day_nets) / n)
    if downside > 0:
        out["sortino"] = round(mean / downside * math.sqrt(TRADING_DAYS), 2)
    return out


def build(db, algo_id: str | None, period: str, anchor: str) -> dict:
    start, end, label = period_bounds(period, anchor)
    title = f"{'Weekly' if period == 'week' else 'Monthly'} tear sheet"
    p = reports.build_payload(db, algo_id, start, end, title, with_equity=False)
    s = p["summary"]
    live = s.get("scope") == "real money"
    closed = [t for t in p["trades"] if t.get("exit_ts") and (t.get("mode") == "live") == live]
    closed.sort(key=lambda t: (t.get("exit_ts") or "").replace(" ", "T"))

    by_day: dict[str, float] = {}
    for t in closed:
        day = (t.get("exit_ts") or "")[:10]
        by_day[day] = by_day.get(day, 0.0) + float(t.get("net") or 0.0)
    daily = [{"day": d, "net": round(v, 2) + 0.0} for d, v in sorted(by_day.items())]
    nets = [float(t.get("net") or 0.0) for t in closed]
    wins = [x for x in nets if x > 0]
    losses = [x for x in nets if x < 0]
    holds = [float(t["hold_min"]) for t in closed if t.get("hold_min") is not None]
    gross = sum(float(t.get("gross") or 0.0) for t in closed)

    algo = db.algo(algo_id) if algo_id else None
    extra = {
        "avg_win": round(sum(wins) / len(wins), 2) if wins else None,
        "avg_loss": round(sum(losses) / len(losses), 2) if losses else None,
        "payoff": round((sum(wins) / len(wins)) / abs(sum(losses) / len(losses)), 2) if wins and losses else None,
        "green_days": sum(1 for d in daily if d["net"] > 0),
        "red_days": sum(1 for d in daily if d["net"] < 0),
        "best_day": max(daily, key=lambda d: d["net"]) if daily else None,
        "worst_day": min(daily, key=lambda d: d["net"]) if daily else None,
        "avg_hold_min": round(sum(holds) / len(holds), 1) if holds else None,
        "charges_pct_of_gross": round(100.0 * s["total_charges"] / gross, 1) if gross > 0 else None,
        "return_pct": round(100.0 * s["net_pnl"] / s["start_balance"], 2) if s.get("start_balance") else None,
        "exit_reasons": [{"reason": r or "unknown", "n": n} for r, n in Counter(t.get("reason") for t in closed).most_common()],
    }
    return {
        "title": title,
        "period": period,
        "label": label,
        "start": start,
        "end": end,
        "algo_id": algo_id or "all",
        "name": (algo or {}).get("name") or ("All algorithms" if not algo_id else algo_id),
        "generated_at": p["generated_at"],
        "summary": s,
        "extra": extra,
        "ratios": ratios([d["net"] for d in daily]),
        "daily": daily,
        "curve": p["pnl_curve"],
        "trades": closed,
    }


# ── PDF ──────────────────────────────────────────────────────────────────────
LEFT, RIGHT = 40, 555


def _tone(v) -> tuple[float, float, float]:
    if v is None:
        return pdf.INK
    return pdf.PROFIT if v > 0 else pdf.LOSS if v < 0 else pdf.INK


def _pct(v, digits: int = 1) -> str:
    return "-" if v is None else f"{v:.{digits}f}%"


def _num(v) -> str:
    return "-" if v is None else f"{v:.2f}"


def _chart_frame(c: pdf.Canvas, y: float, h: float, title: str, lo: float, hi: float) -> tuple[float, float]:
    """A titled frame with its scale; returns the (top, bottom) of the plot area."""
    c.text(LEFT, y, title.upper(), 8, "bold", pdf.GOLD)
    top, bottom = y + 10, y + 10 + h
    c.rect(LEFT, top, RIGHT - LEFT, h, stroke=pdf.HAIRLINE)
    c.text(LEFT + 4, top + 9, pdf.money(hi), 6.5, "mono", pdf.MUTED)
    c.text(LEFT + 4, bottom - 4, pdf.money(lo), 6.5, "mono", pdf.MUTED)
    return top, bottom


def _scale(lo: float, hi: float, top: float, bottom: float):
    pad = (hi - lo) * 0.08 or 1.0
    lo, hi = lo - pad, hi + pad

    def y(v: float) -> float:
        return bottom - (v - lo) / (hi - lo) * (bottom - top)
    return y, lo, hi


def to_pdf(sheet: dict) -> bytes:
    c = pdf.Canvas()
    s, e, r = sheet["summary"], sheet["extra"], sheet["ratios"]

    def header() -> None:
        c.text(LEFT, 40, "MERIDIAN CAPITAL", 8, "bold", pdf.GOLD)
        c.text(RIGHT, 40, f"{sheet['title']} | {sheet['label']}", 8, "mono", pdf.MUTED, align="right")
        c.line(LEFT, 48, RIGHT, 48)

    header()
    c.text(LEFT, 78, sheet["name"], 18, "bold")
    scope = s.get("scope") or "paper"
    c.text(LEFT, 94, f"{sheet['label']} | {sheet['start']} to {sheet['end']} | {scope}"
           + (f" | {s['not_counted']} other trade(s) not counted" if s.get("not_counted") else ""), 9, color=pdf.MUTED)

    ratio_note = " *" if r["few_days"] and r["days"] >= 2 else ""
    dd = pdf.money(-s["max_drawdown_rs"]) if s.get("max_drawdown_rs") else pdf.money(0)
    if s.get("max_drawdown_pct") is not None:
        dd += f" ({s['max_drawdown_pct']:.1f}%)"
    tiles = [
        ("Net P&L", pdf.money(s["net_pnl"], sign=True), _tone(s["net_pnl"])),
        ("Return on opening balance", _pct(e["return_pct"], 2), _tone(e["return_pct"])),
        ("Trades | win rate", f"{s['trades']} | {s['win_rate_pct']:.0f}%", pdf.INK),
        ("Profit factor", _num(s.get("profit_factor")), pdf.INK),
        ("Max drawdown", dd, pdf.LOSS if s.get("max_drawdown_rs") else pdf.INK),
        ("Sharpe | Sortino" + ratio_note, f"{_num(r['sharpe'])} | {_num(r['sortino'])}", pdf.INK),
        ("Avg win | avg loss", f"{pdf.money(e['avg_win'])} | {pdf.money(e['avg_loss'])}", pdf.INK),
        ("Largest win | loss", f"{pdf.money(s['largest_win'])} | {pdf.money(s['largest_loss'])}", pdf.INK),
        ("Green | red days", f"{e['green_days']} | {e['red_days']}", pdf.INK),
        ("Charges", pdf.money(s["total_charges"]) + (f" ({e['charges_pct_of_gross']:.0f}% of gross)" if e["charges_pct_of_gross"] is not None else ""), pdf.INK),
        ("Best day", f"{pdf.money(e['best_day']['net'], sign=True)} {e['best_day']['day'][5:]}" if e["best_day"] else "-", pdf.INK),
        ("Worst day", f"{pdf.money(e['worst_day']['net'], sign=True)} {e['worst_day']['day'][5:]}" if e["worst_day"] else "-", pdf.INK),
    ]
    tw = (RIGHT - LEFT - 3 * 8) / 4
    y0 = 112
    for i, (label, value, color) in enumerate(tiles):
        x = LEFT + (i % 4) * (tw + 8)
        ty = y0 + (i // 4) * 50
        c.rect(x, ty, tw, 42, stroke=pdf.HAIRLINE)
        c.text(x + 8, ty + 13, label.upper(), 6.2, "bold", pdf.MUTED)
        c.text(x + 8, ty + 31, value, 9 if len(value) > 20 else 11.5, "bold", color)
    y = y0 + 3 * 50 + 12

    # Cumulative trading P&L after each closed trade.
    curve = [0.0] + [float(p["equity"]) for p in sheet["curve"]]
    lo, hi = min(curve), max(curve)
    top, bottom = _chart_frame(c, y, 140, "Cumulative trading P&L, trade by trade", lo, hi)
    yv, lo, hi = _scale(lo, hi, top + 12, bottom - 8)
    if lo < 0 < hi:
        c.line(LEFT, yv(0), RIGHT, yv(0), 0.5, pdf.MUTED, dash=True)
    if len(curve) > 1:
        step = (RIGHT - LEFT - 70) / (len(curve) - 1)
        c.polyline([(LEFT + 60 + i * step, yv(v)) for i, v in enumerate(curve)], 1.4, pdf.GOLD)
    else:
        c.text(LEFT + 60, (top + bottom) / 2, "No closed trades in this period.", 9, color=pdf.MUTED)
    y = bottom + 24

    # Each session's result.
    days = sheet["daily"]
    vals = [d["net"] for d in days] or [0.0]
    lo, hi = min(0.0, *vals), max(0.0, *vals)
    top, bottom = _chart_frame(c, y, 100, "Result by session", lo, hi)
    yv, lo, hi = _scale(lo, hi, top + 12, bottom - 14)
    c.line(LEFT, yv(0), RIGHT, yv(0), 0.5, pdf.MUTED)
    if days:
        slot = (RIGHT - LEFT - 70) / len(days)
        bw = max(2.0, min(28.0, slot * 0.6))
        for i, d in enumerate(days):
            x = LEFT + 60 + i * slot + (slot - bw) / 2
            c.rect(x, yv(max(d["net"], 0)), bw, abs(yv(d["net"]) - yv(0)), fill=_tone(d["net"]))
            if len(days) <= 12 or i % max(1, len(days) // 10) == 0:
                c.text(x, bottom - 4, d["day"][8:] + "/" + d["day"][5:7], 6, "mono", pdf.MUTED)
    y = bottom + 24

    reasons = ", ".join(f"{x['reason']} {x['n']}" for x in e["exit_reasons"]) or "-"
    hold = f"{e['avg_hold_min']:.0f} min" if e["avg_hold_min"] is not None else "-"
    c.text(LEFT, y, f"Exits: {reasons}   |   Average hold: {hold}   |   Payoff ratio: {_num(e['payoff'])}", 8,
           color=pdf.INK)
    y += 22

    cols = [("Closed", 92, "left"), ("Contract", 130, "left"), ("Lots", 30, "right"), ("Entry", 55, "right"),
            ("Exit", 55, "right"), ("Net", 80, "right"), ("Reason", 73, "right")]

    def table_head(y: float) -> float:
        c.text(LEFT, y, "TRADES", 8, "bold", pdf.GOLD)
        y += 14
        x = LEFT
        for label, w, align in cols:
            c.text(x + (w if align == "right" else 0), y, label, 7.5, "mono-bold", pdf.MUTED, align=align)
            x += w
        c.line(LEFT, y + 4, RIGHT, y + 4)
        return y + 15

    y = table_head(y)
    for t in sheet["trades"]:
        if y > 800:
            c.new_page()
            header()
            y = table_head(70)
        net = float(t.get("net") or 0.0)
        cells = [
            (t.get("exit_ts") or "")[:16], pdf.latin(t.get("tsym") or f"{t.get('side') or ''} {t.get('strike') or ''}")[:21],
            str(t.get("lots") or "-"),
            f"{float(t['entry_prem']):.2f}" if t.get("entry_prem") is not None else "-",
            f"{float(t['exit_prem']):.2f}" if t.get("exit_prem") is not None else "-",
            f"{net:+,.2f}", pdf.latin(t.get("reason") or "")[:12],
        ]
        x = LEFT
        for (label, w, align), cell in zip(cols, cells):
            c.text(x + (w if align == "right" else 0), y, cell, 7.5, "mono",
                   _tone(net) if label == "Net" else pdf.INK, align=align)
            x += w
        y += 11
    if not sheet["trades"]:
        c.text(LEFT, y, "(no closed trades in this period)", 8, color=pdf.MUTED)

    foot = f"Generated {sheet['generated_at']} IST. Net figures are Angel One's where reconciled; drawdown is on trading alone."
    if ratio_note:
        foot += f" * {r['days']} sessions: too few for the ratios to mean much."
    c.footer(foot)
    return c.render()


def filename(sheet: dict) -> str:
    return f"meridian-tearsheet-{sheet['period']}-{sheet['start']}-{sheet['algo_id']}.pdf"
