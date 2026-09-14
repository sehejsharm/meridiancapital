"""Downloadable session reports, in CSV and PDF.

The report is built once as a payload and then rendered into either format, so
the two can never disagree about the numbers. Everything the results page
shows is in here: the summary, the per-trade blotter, the equity marks and the
event log, because a report that omits the losing detail is not a record.

The PDF is written directly rather than through a reporting library. It is a
small, fixed layout — a cover block and monospaced tables — and hand-writing it
keeps the VM install to the three packages it already needs instead of pulling
in a rendering stack for one page of text.
"""

from __future__ import annotations

import csv
import io
from datetime import datetime

from engine.clock import now_ist


def build_payload(db, algo_id: str | None, start: str, end: str, title: str) -> dict:
    trades = [
        t for t in db.trades(limit=5000, algo_id=algo_id)
        if start <= (t.get("session_date") or "") <= end
    ]
    closed = [t for t in trades if t.get("exit_ts")]
    nets = [float(t.get("net") or 0.0) for t in closed]
    wins = [n for n in nets if n > 0]
    losses = [n for n in nets if n < 0]

    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    equity = [
        e for e in db.equity_curve(limit=5000, algo_id=algo_id)
        if start <= (e.get("ts") or "")[:10] <= end
    ]

    peak = 0.0
    max_dd = 0.0
    for point in equity:
        value = float(point.get("equity") or 0.0)
        peak = max(peak, value)
        if peak > 0:
            max_dd = max(max_dd, (peak - value) / peak)

    events = [
        e for e in db.events(limit=2000, algo_id=algo_id)
        if start <= (e.get("ts") or "")[:10] <= end
    ]

    summary = {
        "trades": len(closed),
        "open_trades": len(trades) - len(closed),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": round(100.0 * len(wins) / len(closed), 2) if closed else 0.0,
        "net_pnl": round(sum(nets), 2),
        "gross_profit": round(gross_win, 2),
        "gross_loss": round(gross_loss, 2),
        "profit_factor": round(gross_win / gross_loss, 3) if gross_loss else None,
        "largest_win": round(max(wins), 2) if wins else 0.0,
        "largest_loss": round(min(losses), 2) if losses else 0.0,
        "average_trade": round(sum(nets) / len(nets), 2) if nets else 0.0,
        "total_charges": round(sum(float(t.get("charges") or 0.0) for t in closed), 2),
        "max_drawdown_pct": round(100.0 * max_dd, 2),
        "errors": sum(1 for e in events if e.get("level") in ("error", "critical")),
    }

    return {
        "title": title,
        "algo_id": algo_id or "all",
        "start": start,
        "end": end,
        "generated_at": now_ist().isoformat(timespec="seconds"),
        "summary": summary,
        "trades": trades,
        "equity": equity,
        "events": events,
    }


TRADE_COLUMNS = [
    "session_date", "algo_id", "mode", "entry_ts", "exit_ts", "view", "side", "tsym",
    "strike", "expiry", "lots", "qty", "entry_prem", "exit_prem", "spot_entry",
    "spot_exit", "peak_pct", "gross", "charges", "net", "reason", "hold_min",
    "equity", "pnl_source",
]


def to_csv(payload: dict) -> str:
    """One file carrying the summary, the blotter, the equity marks and the log."""
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")

    w.writerow([payload["title"]])
    w.writerow(["Algorithm", payload["algo_id"]])
    w.writerow(["Period", f"{payload['start']} to {payload['end']}"])
    w.writerow(["Generated", payload["generated_at"]])
    w.writerow([])

    w.writerow(["SUMMARY"])
    for key, value in payload["summary"].items():
        w.writerow([key.replace("_", " ").title(), "" if value is None else value])
    w.writerow([])

    w.writerow(["TRADES"])
    w.writerow([c.replace("_", " ").title() for c in TRADE_COLUMNS])
    for t in payload["trades"]:
        w.writerow([t.get(c, "") for c in TRADE_COLUMNS])
    w.writerow([])

    w.writerow(["EQUITY MARKS"])
    w.writerow(["Timestamp", "Equity", "Realised Today", "Peak Equity", "Day P&L"])
    for e in payload["equity"]:
        w.writerow([e.get("ts"), e.get("equity"), e.get("realised_today"),
                    e.get("peak_equity"), e.get("day_pl")])
    w.writerow([])

    w.writerow(["EVENT LOG"])
    w.writerow(["Timestamp", "Level", "Source", "Message"])
    for e in payload["events"]:
        w.writerow([e.get("ts"), e.get("level"), e.get("source"), e.get("message")])

    return buf.getvalue()


# ── minimal PDF writer ───────────────────────────────────────────────────────
PAGE_W, PAGE_H = 595, 842  # A4 points
MARGIN = 40
LINE = 12
FONT = 8.5
LINES_PER_PAGE = int((PAGE_H - 2 * MARGIN) / LINE)


def _esc(text: str) -> str:
    return text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


def _fit(text: str, width: int) -> str:
    text = str(text) if text is not None else ""
    return text if len(text) <= width else text[: width - 1] + "…"


def _money(v) -> str:
    try:
        return f"{float(v):,.2f}"
    except (TypeError, ValueError):
        return "-"


def _report_lines(payload: dict) -> list[str]:
    s = payload["summary"]
    profit_factor = f"{s['profit_factor']:.3f}" if s["profit_factor"] is not None else "n/a"
    lines = [
        payload["title"],
        "=" * 92,
        f"Algorithm : {payload['algo_id']}",
        f"Period    : {payload['start']} to {payload['end']}",
        f"Generated : {payload['generated_at']} IST",
        "",
        "SUMMARY",
        "-" * 92,
        f"Trades closed   {s['trades']:>10}     Win rate      {s['win_rate_pct']:>10.2f} %",
        f"Wins            {s['wins']:>10}     Losses        {s['losses']:>10}",
        f"Net P&L         {_money(s['net_pnl']):>10}     Avg trade     {_money(s['average_trade']):>10}",
        f"Gross profit    {_money(s['gross_profit']):>10}     Gross loss    {_money(s['gross_loss']):>10}",
        f"Largest win     {_money(s['largest_win']):>10}     Largest loss  {_money(s['largest_loss']):>10}",
        f"Profit factor   {profit_factor:>10}     Max drawdown  {s['max_drawdown_pct']:>10.2f} %",
        f"Charges paid    {_money(s['total_charges']):>10}     Errors logged {s['errors']:>10}",
        "",
        "TRADES",
        "-" * 92,
        f"{'Date':<11}{'Mode':<6}{'Side':<5}{'Contract':<22}{'Lots':>5}{'Entry':>9}{'Exit':>9}{'Net':>12}{'Reason':>12}",
    ]
    for t in payload["trades"]:
        lines.append(
            f"{_fit(t.get('session_date'), 10):<11}"
            f"{_fit(t.get('mode'), 5):<6}"
            f"{_fit(t.get('view'), 4):<5}"
            f"{_fit(t.get('tsym'), 21):<22}"
            f"{_fit(t.get('lots'), 4):>5}"
            f"{_fit(_money(t.get('entry_prem')), 8):>9}"
            f"{_fit(_money(t.get('exit_prem')), 8):>9}"
            f"{_fit(_money(t.get('net')), 11):>12}"
            f"{_fit(t.get('reason'), 11):>12}"
        )
    if not payload["trades"]:
        lines.append("(no trades in this period)")

    lines += ["", "EVENT LOG", "-" * 92]
    for e in payload["events"][:400]:
        lines.append(
            f"{_fit(e.get('ts'), 19):<20}{_fit(e.get('level'), 8):<9}{_fit(e.get('message'), 62)}"
        )
    if not payload["events"]:
        lines.append("(no events in this period)")
    return lines


def to_pdf(payload: dict) -> bytes:
    """Render the payload as a paginated, monospaced PDF."""
    lines = _report_lines(payload)
    pages = [lines[i : i + LINES_PER_PAGE] for i in range(0, len(lines), LINES_PER_PAGE)] or [[]]

    objects: list[bytes] = []

    def add(obj: bytes) -> int:
        objects.append(obj)
        return len(objects)

    font_id = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier >>")
    page_ids: list[int] = []
    content_ids: list[int] = []
    for page in pages:
        text = [b"BT", f"/F1 {FONT} Tf".encode(), f"{LINE} TL".encode(),
                f"1 0 0 1 {MARGIN} {PAGE_H - MARGIN} Tm".encode()]
        for line in page:
            text.append(b"(" + _esc(line).encode("latin-1", "replace") + b") Tj T*")
        text.append(b"ET")
        stream = b"\n".join(text)
        content_ids.append(add(b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream"))

    pages_id = len(objects) + len(pages) + 1
    for content_id in content_ids:
        page_ids.append(add(
            f"<< /Type /Page /Parent {pages_id} 0 R /MediaBox [0 0 {PAGE_W} {PAGE_H}] "
            f"/Resources << /Font << /F1 {font_id} 0 R >> >> /Contents {content_id} 0 R >>".encode()
        ))
    kids = " ".join(f"{pid} 0 R" for pid in page_ids)
    actual_pages_id = add(f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>".encode())
    catalog_id = add(f"<< /Type /Catalog /Pages {actual_pages_id} 0 R >>".encode())

    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for i, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"

    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for off in offsets[1:]:
        out += f"{off:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root {catalog_id} 0 R >>\n"
        f"startxref\n{xref_at}\n%%EOF\n"
    ).encode()
    return bytes(out)


def filename(payload: dict, ext: str) -> str:
    stamp = datetime.fromisoformat(payload["generated_at"]).strftime("%Y%m%d-%H%M")
    return f"meridian-{payload['algo_id']}-{payload['start']}-to-{payload['end']}-{stamp}.{ext}"
