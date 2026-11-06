"""A financial year's trading P&L, laid out for a chartered accountant.

Real-money closed trades only, each with Angel's own charges where its ledger
or balance confirms them, assigned to the financial year (April to March) in
which the position was closed. It is a cross-check and a working file for the
CA: Angel's Tax P&L statement and contract notes stay the legal record, and the
notes in every export say so.

Turnover follows the ICAI Guidance Note on Tax Audit (2022 revision): for
options, the sum of each trade's absolute profit or loss; the premium on sale
is not added. Nothing here estimates tax: slab, other income and set-offs are
the CA's.
"""

from __future__ import annotations

import csv
import io
import re
from datetime import date, timedelta

from app import ledger, pdf
from engine.clock import now_ist

# Advance-tax instalments: the share of the year's tax due by each date.
INSTALMENTS = (("15 Jun", 6, 15, 15), ("15 Sep", 9, 15, 45), ("15 Dec", 12, 15, 75), ("15 Mar", 3, 15, 100))

_FY = re.compile(r"^(\d{4})-(\d{2})$")


def fy_of(day: str) -> str:
    """'2026-11-06' -> '2026-27'."""
    d = date.fromisoformat(day[:10])
    y = d.year if d.month >= 4 else d.year - 1
    return f"{y}-{(y + 1) % 100:02d}"


def fy_bounds(fy: str) -> tuple[str, str]:
    """'2026-27' -> ('2026-04-01', '2027-03-31'). Raises ValueError for anything else."""
    m = _FY.match(fy or "")
    if not m or (int(m.group(1)) + 1) % 100 != int(m.group(2)):
        raise ValueError("financial year must look like 2026-27")
    y = int(m.group(1))
    return f"{y}-04-01", f"{y + 1}-03-31"


def _f(v) -> float | None:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def _row(t: dict) -> dict:
    qty = _f(t.get("qty")) or 0.0
    entry, exit_ = _f(t.get("entry_prem")), _f(t.get("exit_prem"))
    gross = _f(t.get("gross"))
    if gross is None and entry is not None and exit_ is not None:
        gross = (exit_ - entry) * qty
    gross = gross or 0.0
    charges = _f(t.get("charges_angel"))
    if charges is None:
        charges = _f(t.get("charges")) or 0.0
    net = _f(t.get("net_angel"))
    if net is None:
        net = gross - charges
    return {
        "entry_date": (t.get("entry_ts") or "")[:10],
        "exit_date": (t.get("exit_ts") or "")[:10],
        "entry_ts": t.get("entry_ts"),
        "exit_ts": t.get("exit_ts"),
        "algo_id": t.get("algo_id"),
        "contract": t.get("tsym") or f"NIFTY {t.get('strike') or ''} {t.get('side') or ''}".strip(),
        "side": t.get("side"),
        "strike": t.get("strike"),
        "expiry": t.get("expiry"),
        "qty": int(qty) if qty == int(qty) else qty,
        "buy_price": entry,
        "buy_value": round(entry * qty, 2) if entry is not None else None,
        "sell_price": exit_,
        "sell_value": round(exit_ * qty, 2) if exit_ is not None else None,
        "gross": round(gross, 2) + 0.0,
        "charges": round(charges, 2) + 0.0,
        "net": round(net, 2) + 0.0,
        "turnover": round(abs(gross), 2),
        "charges_source": t.get("reconciled") or ledger.RECORDED,
        "match": t.get("match"),
    }


def _totals(rows: list[dict]) -> dict:
    gross = [r["gross"] for r in rows]
    return {
        "trades": len(rows),
        "wins": sum(1 for r in rows if r["net"] > 0),
        "losses": sum(1 for r in rows if r["net"] < 0),
        "gross_profit": round(sum(g for g in gross if g > 0), 2) + 0.0,
        "gross_loss": round(-sum(g for g in gross if g < 0), 2) + 0.0,
        "gross_pnl": round(sum(gross), 2) + 0.0,
        "charges": round(sum(r["charges"] for r in rows), 2) + 0.0,
        "net_pnl": round(sum(r["net"] for r in rows), 2) + 0.0,
        "turnover": round(sum(r["turnover"] for r in rows), 2) + 0.0,
        "buy_value": round(sum(r["buy_value"] or 0.0 for r in rows), 2) + 0.0,
        "sell_value": round(sum(r["sell_value"] or 0.0 for r in rows), 2) + 0.0,
    }


def build(db, fy: str) -> dict:
    """The year's figures: totals, by month, by advance-tax date, and every trade."""
    start, end = fy_bounds(fy)
    today = now_ist().date().isoformat()
    all_live = [t for t in db.trades(limit=1_000_000) if t.get("mode") == "live"]
    # A position belongs to the year it was closed in; one still open is listed as a count only.
    closed = [t for t in all_live if t.get("exit_ts") and start <= (t["exit_ts"] or "")[:10] <= end]
    still_open = [t for t in all_live if not t.get("exit_ts") and start <= (t.get("session_date") or "") <= end]
    rows = [_row(t) for t in ledger.reconcile(db, closed)]
    rows.sort(key=lambda r: (r["exit_ts"] or "").replace(" ", "T"))

    months: dict[str, list[dict]] = {}
    for r in rows:
        months.setdefault(r["exit_date"][:7], []).append(r)

    y = int(start[:4])
    instalments, cum = [], 0.0
    prev = start
    for label, month, day, pct in INSTALMENTS:
        due = date(y + 1 if month < 4 else y, month, day).isoformat()
        window = [r for r in rows if prev <= r["exit_date"] <= due]
        cum += sum(r["net"] for r in window)
        instalments.append({
            "label": label, "due": due, "cumulative_pct": pct, "passed": due < today,
            "net_in_window": round(sum(r["net"] for r in window), 2) + 0.0,
            "net_to_date": round(cum, 2) + 0.0,
        })
        prev = (date.fromisoformat(due) + timedelta(days=1)).isoformat()

    totals = _totals(rows)
    recorded = sum(1 for r in rows if r["charges_source"] == ledger.RECORDED)
    transfers = sum(1 for r in rows if r["match"] == ledger.FUND_TRANSFER)
    notes = [
        "Source: real-money trades as Meridian recorded them, with Angel One's own charges where its ledger "
        "or balance confirms them. Angel's Tax P&L statement and contract notes are the legal record; use "
        "this to cross-check them.",
        "Index options traded on NSE are generally treated as non-speculative business income "
        "(Income-tax Act s.43(5)). Confirm the treatment with your CA.",
        "Turnover is the sum of each trade's absolute profit or loss (ICAI Guidance Note on Tax Audit, "
        "2022 revision); premium on sale is not added. It is the figure the tax-audit threshold is tested on.",
        "Charges are Angel's total per trade: brokerage, STT, exchange and SEBI fees, GST and stamp duty.",
        "Each trade falls in the year it was closed (sold).",
        "Advance tax: 15% of the year's tax by 15 Jun, 45% by 15 Sep, 75% by 15 Dec, 100% by 15 Mar. "
        "Realised P&L to each date is shown to help estimate them; it is not the tax due.",
    ]
    if recorded:
        notes.append(f"{recorded} trade(s) carry the charges the program recorded, not Angel's; check them "
                     "against the contract notes.")
    if transfers:
        notes.append(f"{transfers} trade(s) fell on a day money was paid in or taken out; their P&L is "
                     "the trade's own, not the balance change.")
    if still_open:
        notes.append(f"{len(still_open)} position(s) opened this year are still open and are not included.")
    if fy_of(today) == fy:
        notes.append(f"The year is not over: figures to {today}.")

    return {
        "fy": fy,
        "start": start,
        "end": end,
        "generated_at": now_ist().isoformat(timespec="seconds"),
        "totals": totals,
        "open_positions": len(still_open),
        "charges_from_program": recorded,
        "months": [{"month": m, **_totals(rs)} for m, rs in sorted(months.items())],
        "instalments": instalments,
        "trades": rows,
        "notes": notes,
        "years": years(db),
    }


def years(db) -> list[str]:
    """Every financial year with a real-money trade, newest first; the current one always."""
    with db.conn() as c:
        days = [r[0] for r in c.execute(
            "SELECT DISTINCT substr(COALESCE(exit_ts, session_date), 1, 10) FROM trades WHERE mode = 'live'")]
    out = {fy_of(now_ist().date().isoformat())}
    for d in days:
        try:
            out.add(fy_of(d))
        except (TypeError, ValueError):
            continue
    return sorted(out, reverse=True)


CSV_COLUMNS = [
    ("entry_date", "Buy date"), ("exit_date", "Sell date"), ("contract", "Contract"), ("side", "Type"),
    ("strike", "Strike"), ("expiry", "Expiry"), ("qty", "Quantity"), ("buy_price", "Buy price"),
    ("buy_value", "Buy value"), ("sell_price", "Sell price"), ("sell_value", "Sell value"),
    ("gross", "Gross P&L"), ("charges", "Charges"), ("net", "Net P&L"), ("turnover", "Turnover (abs P&L)"),
    ("charges_source", "Charges from"), ("algo_id", "Algorithm"),
]

TOTAL_LABELS = [
    ("trades", "Trades closed"), ("wins", "Profitable"), ("losses", "Loss-making"),
    ("gross_profit", "Gross profit"), ("gross_loss", "Gross loss"), ("gross_pnl", "Gross P&L"),
    ("charges", "Charges"), ("net_pnl", "Net P&L"), ("turnover", "Turnover"),
    ("buy_value", "Total buy value"), ("sell_value", "Total sell value (premium on sale)"),
]


def to_csv(report: dict) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow([f"F&O trading P&L, financial year {report['fy']} ({report['start']} to {report['end']})"])
    w.writerow(["Broker", "Angel One", "Segment", "NSE index options (NIFTY)"])
    w.writerow(["Generated", report["generated_at"]])
    w.writerow([])
    w.writerow(["SUMMARY (Rs)"])
    for key, label in TOTAL_LABELS:
        w.writerow([label, report["totals"][key]])
    w.writerow([])
    w.writerow(["BY MONTH (Rs)"])
    w.writerow(["Month", "Trades", "Gross P&L", "Charges", "Net P&L", "Turnover"])
    for m in report["months"]:
        w.writerow([m["month"], m["trades"], m["gross_pnl"], m["charges"], m["net_pnl"], m["turnover"]])
    w.writerow([])
    w.writerow(["ADVANCE TAX DATES (Rs, realised net P&L)"])
    w.writerow(["Instalment", "Due", "Share of year's tax due", "Net since last date", "Net for the year to date"])
    for i in report["instalments"]:
        w.writerow([i["label"], i["due"], f"{i['cumulative_pct']}%", i["net_in_window"], i["net_to_date"]])
    w.writerow([])
    w.writerow(["TRADES"])
    w.writerow([label for _, label in CSV_COLUMNS])
    for r in report["trades"]:
        w.writerow(["" if r.get(k) is None else r.get(k) for k, _ in CSV_COLUMNS])
    w.writerow([])
    w.writerow(["NOTES"])
    for n in report["notes"]:
        w.writerow([n])
    return buf.getvalue()


def to_pdf(report: dict) -> bytes:
    c = pdf.Canvas()
    left, right = 40, 555
    y = 50.0

    def header() -> float:
        c.text(left, 40, "MERIDIAN CAPITAL", 8, "bold", pdf.GOLD)
        c.text(right, 40, f"FY {report['fy']} | trading P&L", 8, "mono", pdf.MUTED, align="right")
        c.line(left, 48, right, 48)
        return 66.0

    def need(height: float) -> None:
        nonlocal y
        if y + height > 800:
            c.new_page()
            y = header()

    header()
    y = 78
    c.text(left, y, f"F&O trading P&L, FY {report['fy']}", 18, "bold")
    y += 16
    c.text(left, y, f"{report['start']} to {report['end']} | Angel One | NSE index options | real money only", 9,
           color=pdf.MUTED)
    y += 26

    t = report["totals"]
    tiles = [
        ("Net P&L", pdf.money(t["net_pnl"], sign=True), pdf.PROFIT if t["net_pnl"] > 0 else pdf.LOSS if t["net_pnl"] < 0 else pdf.INK),
        ("Turnover (abs P&L)", pdf.money(t["turnover"]), pdf.INK),
        ("Charges", pdf.money(t["charges"]), pdf.INK),
        ("Trades closed", f"{t['trades']} ({t['wins']} profit, {t['losses']} loss)", pdf.INK),
        ("Gross profit", pdf.money(t["gross_profit"]), pdf.INK),
        ("Gross loss", pdf.money(-t["gross_loss"]), pdf.INK),
        ("Total sell value", pdf.money(t["sell_value"]), pdf.INK),
        ("Total buy value", pdf.money(t["buy_value"]), pdf.INK),
    ]
    tw = (right - left - 3 * 8) / 4
    for i, (label, value, color) in enumerate(tiles):
        x = left + (i % 4) * (tw + 8)
        ty = y + (i // 4) * 52
        c.rect(x, ty, tw, 44, stroke=pdf.HAIRLINE)
        c.text(x + 8, ty + 14, label.upper(), 6.5, "bold", pdf.MUTED)
        c.text(x + 8, ty + 32, value, 10 if len(value) > 18 else 12, "bold", color)
    y += 2 * 52 + 14

    def table(title: str, cols: list[tuple[str, int, str]], rows: list[list[str]]) -> None:
        nonlocal y
        need(40)
        c.text(left, y, title.upper(), 8, "bold", pdf.GOLD)
        y += 14
        x = left
        for label, width, align in cols:
            c.text(x + (width if align == "right" else 0), y, label, 7.5, "mono-bold", pdf.MUTED, align=align)
            x += width
        y += 4
        c.line(left, y, right, y)
        y += 11
        for r in rows:
            need(12)
            x = left
            for (label, width, align), cell in zip(cols, r):
                c.text(x + (width if align == "right" else 0), y, cell, 7.5, "mono", align=align)
                x += width
            y += 11
        y += 10

    table("By month", [("Month", 80, "left"), ("Trades", 60, "right"), ("Gross P&L", 95, "right"),
                       ("Charges", 85, "right"), ("Net P&L", 95, "right"), ("Turnover", 100, "right")],
          [[m["month"], str(m["trades"]), pdf.money(m["gross_pnl"]), pdf.money(m["charges"]),
            pdf.money(m["net_pnl"]), pdf.money(m["turnover"])] for m in report["months"]]
          or [["(no closed trades)", "", "", "", "", ""]])

    table("Advance-tax dates (realised net P&L)",
          [("Instalment", 80, "left"), ("Due", 90, "left"), ("Tax due by then", 100, "right"),
           ("Net since last", 110, "right"), ("Net, year to date", 135, "right")],
          [[i["label"], i["due"] + ("" if i["passed"] else " *"), f"{i['cumulative_pct']}%",
            pdf.money(i["net_in_window"]), pdf.money(i["net_to_date"])] for i in report["instalments"]])
    c.text(left, y - 6, "* not yet due", 7, color=pdf.MUTED)
    y += 6

    need(30)
    c.text(left, y, "NOTES", 8, "bold", pdf.GOLD)
    y += 13
    for n in report["notes"]:
        for i, line in enumerate(pdf.wrap(n, 140)):
            need(11)
            c.text(left + (0 if i == 0 else 8), y, ("- " if i == 0 else "") + line, 7.5, color=pdf.INK)
            y += 10
        y += 2
    y += 10

    table("Trades (by sell date)",
          [("Sell date", 62, "left"), ("Contract", 128, "left"), ("Qty", 32, "right"), ("Buy", 50, "right"),
           ("Sell", 50, "right"), ("Gross", 64, "right"), ("Charges", 56, "right"), ("Net", 73, "right")],
          [[r["exit_date"], pdf.latin(r["contract"])[:24], str(r["qty"]),
            f"{r['buy_price']:.2f}" if r["buy_price"] is not None else "-",
            f"{r['sell_price']:.2f}" if r["sell_price"] is not None else "-",
            f"{r['gross']:,.2f}", f"{r['charges']:,.2f}", f"{r['net']:,.2f}"] for r in report["trades"]]
          or [["(none)", "", "", "", "", "", "", ""]])

    c.footer(f"Generated {report['generated_at']} IST. A working file for your CA; Angel's Tax P&L "
             "statement is the legal record.")
    return c.render()


def filename(report: dict, ext: str) -> str:
    return f"meridian-tax-pnl-FY{report['fy']}.{ext}"
