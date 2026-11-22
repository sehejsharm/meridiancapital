"""Everything the deck renders that is not an algorithm: health, news, ticker,
and the downloadable reports.
"""

from __future__ import annotations

import asyncio
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from app import health, logs, market, reports, research, tax, tearsheet
from app.deps import ctx
from app.feeds import NewsFeed
from app.security import Principal, client_ip, require_auth
from engine.clock import now_ist

router = APIRouter(prefix="/api", tags=["desk"], dependencies=[Depends(require_auth)])

_news = NewsFeed()


@router.get("/health/detail")
async def health_detail() -> dict:
    c = ctx()
    return health.collect(c.db, c.fleet)


@router.get("/news")
async def news(force: bool = Query(default=False)) -> dict:
    return await asyncio.to_thread(_news.get, force)


@router.get("/market/nifty")
async def market_nifty() -> dict:
    """Today's one-minute NIFTY candles for the deck's chart."""
    import asyncio

    return await asyncio.to_thread(market.nifty_chart, ctx().db)


@router.get("/market/chain")
async def market_chain() -> dict:
    """The option chain around the traded contract, with volume, OI and Greeks."""
    c = ctx()
    out = market.option_chain(c.db)
    running = [aid for aid, sup in c.fleet.all().items()
               if sup.state.running and getattr(sup, "program_path", None) is not None]
    if running and (not out.get("available") or out.get("stale")):
        # "Start an engine" is wrong advice while a program is trading, and a
        # chain fetched separately would spend the rate budget it trades on.
        # What the panel can show for free is the program's own contract.
        from shared.db import snapshot_key

        snaps = [s for s in (c.db.kv_get(snapshot_key(a), None) for a in running) if s]
        snap = max(snaps, key=lambda s: s.get("ts") or "", default=None)
        holidays = {h["day"] for h in c.db.holidays()}
        out["program"] = market.program_contract(snap, holidays, now_ist()) if snap else None
        # A chain saved by an engine that has since stopped is a record, not a
        # quote; the program's own live contract takes its place.
        out["available"] = False
        out["reason"] = (
            "Your program trades through its own Angel session, so the full chain is not "
            "fetched: it would spend the request budget the program trades on."
        )
    return out


@router.get("/desk/metrics")
async def desk_metrics() -> dict:
    """The Deck's metrics panel: performance, the strategy against NIFTY, the market."""
    from app import desk_metrics as dm

    return await asyncio.to_thread(dm.build, ctx().db)


@router.get("/market/flow")
def market_flow() -> dict:
    """The day's option flow: premium traded per minute in calls and puts, and positioning by strike."""
    from app import chain_flow, market

    c = ctx()
    chain = c.db.kv_get(market.K_CHAIN, None)
    day = str((chain or {}).get("ts") or now_ist().isoformat())[:10]
    return chain_flow.for_day(c.db, day)


@router.get("/market/expiry-outlook")
async def market_expiry_outlook(algo: str | None = Query(None, max_length=80)) -> dict:
    """Whether the program can trade today and on the coming days, given its own expiry window."""
    from app import ledger, program_config

    c = ctx()
    algo_id = algo or next((aid for aid, sup in c.fleet.all().items() if sup.state.running), None) or ledger.live_algo(c.db)
    row = c.db.algo(algo_id) if algo_id else None
    if not row or row.get("kind") == "builtin":
        return {"available": False, "reason": "no uploaded program to read the expiry window from"}
    running = program_config.running_config(c.db, algo_id) if c.fleet.is_running(algo_id) else None
    code = program_config.code_config(c.db, row)
    values = {**((code or {}).get("values") or {}), **((running or {}).get("values") or {})}
    lo, hi = values.get("MIN_DTE"), values.get("MAX_DTE")
    if lo is None or hi is None:
        return {"available": False, "reason": "this program's expiry window could not be read"}
    holidays = {h["day"] for h in c.db.holidays()}
    out = market.expiry_outlook(now_ist().date(), holidays, int(lo), int(hi))
    return {"available": True, "algo_id": algo_id, "name": row.get("name") or algo_id,
            "from": "the running program" if running and "MIN_DTE" in (running.get("values") or {}) else "the active version's code",
            **out}


@router.get("/ticker")
async def ticker() -> dict:
    """Latest NIFTY spot for the deck's strip.

    First choice is whichever engine most recently published one: the engines
    already poll Angel for the index, and re-polling here would spend rate-limit
    budget the trading loop needs. The engine publishes the index under
    ``signal`` (the older ``market`` key is still read, for snapshots written
    before). With no engine running the strip falls back to the chart's public
    feed, labelled as delayed — never used for a trading decision.
    """
    import asyncio

    c = ctx()
    best = None
    from shared.db import snapshot_key

    # Only a running algorithm's price is live: a snapshot a few seconds old
    # from one that has just stopped is a record, not a feed.
    running = {aid for aid, sup in c.fleet.all().items() if sup.state.running}
    for algo in c.db.algos():
        if algo["id"] not in running:
            continue
        snap = c.db.kv_get(snapshot_key(algo["id"]), None)
        if not snap:
            continue
        sig = snap.get("signal") or {}
        legacy = snap.get("market") or {}
        spot = sig.get("spot") if sig.get("spot") is not None else legacy.get("spot")
        if spot is None:
            continue
        if best is None or (snap.get("ts") or "") > (best.get("ts") or ""):
            pick = sig if sig.get("spot") is not None else legacy
            best = {"spot": spot, "bar_close": pick.get("bar_close"),
                    "channel_high": pick.get("channel_high"), "channel_low": pick.get("channel_low"),
                    "ts": snap.get("ts"), "algo_id": algo["id"]}

    fresh = best is not None and (market._age(best.get("ts")) or 0) < TICKER_FRESH_SEC
    if fresh:
        return {**{k: best.get(k) for k in ("spot", "bar_close", "channel_high", "channel_low", "ts")},
                "source_algo": best["algo_id"], "source": "engine", "delayed": False,
                "server_time": now_ist().isoformat(timespec="seconds"), "live": True}

    # Nothing live: the last price on the chart's public feed, clearly labelled.
    chart = await asyncio.to_thread(market.nifty_chart, c.db)
    bars = chart.get("bars") or []
    last = bars[-1] if bars else None
    return {
        "spot": last["c"] if last else (best or {}).get("spot"),
        "bar_close": None, "channel_high": None, "channel_low": None,
        "ts": (best or {}).get("ts"),
        "source_algo": None,
        "source": chart.get("source") if last else None,
        "delayed": bool(last),
        "label": chart.get("label") if last else None,
        "server_time": now_ist().isoformat(timespec="seconds"),
        "live": False,
    }


# A published spot older than this is not shown as live.
TICKER_FRESH_SEC = 90.0


# Points of the equity curve sent to the results page's chart.
REPORT_CHART_POINTS = 3000
# Log lines sent to the results page (it shows this many; the downloads carry all).
REPORT_PAGE_EVENTS = 300


async def _payload(algo_id: str | None, start: str, end: str, title: str,
                   max_points: int | None = None, with_equity: bool = True) -> dict:
    if start > end:
        raise HTTPException(status_code=400, detail="start date must not be after end date")
    # Off the event loop: a long period reads every sample in it, and the live
    # feed and every other request would wait for it.
    return await asyncio.to_thread(
        reports.build_payload, ctx().db, algo_id, start, end, title, max_points, with_equity
    )


@router.get("/reports")
async def report_json(
    start: str, end: str, algo_id: str | None = None, title: str = "Meridian session report"
) -> dict:
    """The same payload the CSV and PDF are rendered from, for the results page,
    with the curve thinned to what a chart can show."""
    # No balance marks: the page draws pnl_curve. They stay in the CSV download.
    payload = await _payload(algo_id, start, end, title, REPORT_CHART_POINTS, with_equity=False)
    # The page lists the newest REPORT_PAGE_EVENTS log lines and says how many there
    # are; the full log is in the CSV and PDF. 2,000 lines were 300 KB a visit.
    payload["events_total"] = len(payload["events"])
    payload["events"] = payload["events"][:REPORT_PAGE_EVENTS]
    return payload


@router.get("/reports.csv", response_class=PlainTextResponse)
async def report_csv(
    start: str, end: str, algo_id: str | None = None, title: str = "Meridian session report"
) -> Response:
    payload = await _payload(algo_id, start, end, title)
    return Response(
        content=reports.to_csv(payload),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{reports.filename(payload, "csv")}"'},
    )


@router.get("/reports.pdf")
async def report_pdf(
    start: str, end: str, algo_id: str | None = None, title: str = "Meridian session report"
) -> Response:
    payload = await _payload(algo_id, start, end, title)
    content = await asyncio.to_thread(reports.to_pdf, payload)
    return Response(
        content=content,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{reports.filename(payload, "pdf")}"'},
    )


# ── tax report for the CA ────────────────────────────────────────────────────
async def _tax(fy: str | None) -> dict:
    fy = fy or tax.fy_of(now_ist().date().isoformat())
    try:
        tax.fy_bounds(fy)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return await asyncio.to_thread(tax.build, ctx().db, fy)


@router.get("/tax")
async def tax_report(fy: str | None = Query(None, max_length=7)) -> dict:
    """A financial year's real-money F&O P&L: totals, months, advance-tax dates, trades."""
    return await _tax(fy)


@router.get("/tax.csv", response_class=PlainTextResponse)
async def tax_csv(fy: str | None = Query(None, max_length=7)) -> Response:
    report = await _tax(fy)
    return Response(
        content=tax.to_csv(report),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{tax.filename(report, "csv")}"'},
    )


@router.get("/tax.pdf")
async def tax_pdf(fy: str | None = Query(None, max_length=7)) -> Response:
    report = await _tax(fy)
    content = await asyncio.to_thread(tax.to_pdf, report)
    return Response(
        content=content,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{tax.filename(report, "pdf")}"'},
    )


# ── weekly / monthly tear sheet ──────────────────────────────────────────────
async def _sheet(period: str, day: str | None, algo_id: str | None) -> dict:
    day = day or now_ist().date().isoformat()
    try:
        tearsheet.period_bounds(period, day)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"{exc}" if "period" in str(exc) else "date must be an ISO date")
    return await asyncio.to_thread(tearsheet.build, ctx().db, algo_id, period, day)


@router.get("/tearsheet")
async def tearsheet_json(period: str = Query("week", max_length=5), date: str | None = Query(None, max_length=10),
                         algo_id: str | None = Query(None, max_length=80)) -> dict:
    return await _sheet(period, date, algo_id)


@router.get("/tearsheet.pdf")
async def tearsheet_pdf(period: str = Query("week", max_length=5), date: str | None = Query(None, max_length=10),
                        algo_id: str | None = Query(None, max_length=80)) -> Response:
    sheet = await _sheet(period, date, algo_id)
    content = await asyncio.to_thread(tearsheet.to_pdf, sheet)
    return Response(
        content=content,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{tearsheet.filename(sheet)}"'},
    )


# ── daily log export ─────────────────────────────────────────────────────────
def _log_payload(day: str, algo_id: str | None):
    try:
        date.fromisoformat(day)
    except ValueError:
        raise HTTPException(status_code=400, detail="day must be an ISO date, e.g. 2026-09-22")
    return logs.day_events(ctx().db, day, algo_id)


@router.get("/logs")
async def log_summary(day: str | None = None, algo_id: str | None = None) -> dict:
    """What a day's log holds, so the page can show it before downloading."""
    day = day or now_ist().strftime("%Y-%m-%d")
    events = _log_payload(day, algo_id)
    return {"day": day, "algo_id": algo_id or "all", **logs.summarise(events)}


@router.get("/logs.csv", response_class=PlainTextResponse)
async def log_csv(day: str | None = None, algo_id: str | None = None) -> Response:
    day = day or now_ist().strftime("%Y-%m-%d")
    events = _log_payload(day, algo_id)
    return Response(
        content=logs.to_csv(day, events, algo_id),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{logs.filename(day, algo_id, "csv")}"'
        },
    )


@router.get("/logs.txt", response_class=PlainTextResponse)
async def log_text(day: str | None = None, algo_id: str | None = None) -> Response:
    day = day or now_ist().strftime("%Y-%m-%d")
    events = _log_payload(day, algo_id)
    return Response(
        content=logs.to_text(day, events, algo_id),
        media_type="text/plain; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{logs.filename(day, algo_id, "txt")}"'
        },
    )


# ── research recorder ────────────────────────────────────────────────────────

def _research_trades(db, algo: str | None, mode: str) -> list[dict]:
    from app import ledger

    algo_id = algo or ledger.live_algo(db)
    rows = db.trades(limit=3000, algo_id=algo_id) if algo_id else []
    rows = [t for t in rows if t.get("exit_ts") and (mode == "all" or t.get("mode") == mode)]
    rows.sort(key=lambda t: str(t.get("entry_ts") or ""))
    return ledger.reconcile(db, rows)


@router.get("/research")
def research_summary() -> dict:
    """How much has been recorded: price paths, ticks, days of one-minute contract prices."""
    return research.summary(ctx().db)


@router.get("/research/execution")
async def research_execution(
    algo: str | None = Query(None, max_length=80),
    mode: str = Query("live", pattern="^(live|paper|all)$"),
) -> dict:
    """Each exit fill against the last price the program saw and against that minute's close."""
    db = ctx().db
    return await asyncio.to_thread(lambda: research.execution_report(db, _research_trades(db, algo, mode)))


@router.get("/research/rules")
async def research_rules(
    algo: str | None = Query(None, max_length=80),
    mode: str = Query("live", pattern="^(live|paper|all)$"),
) -> dict:
    """The v8, v9 and v10 exit rule sets replayed over each recorded trade's own price path."""
    db = ctx().db
    return await asyncio.to_thread(lambda: research.rules_report(db, _research_trades(db, algo, mode)))


@router.get("/research/ticks.csv", response_class=PlainTextResponse)
async def research_ticks_csv(
    start: date = Query(..., alias="from"), end: date = Query(..., alias="to"),
) -> PlainTextResponse:
    if end < start:
        raise HTTPException(status_code=400, detail="'to' is before 'from'")
    body = await asyncio.to_thread(research.ticks_csv, ctx().db, start.isoformat(), end.isoformat())
    return PlainTextResponse(body, media_type="text/csv", headers={
        "Content-Disposition": f'attachment; filename="meridian-price-paths-{start}-to-{end}.csv"'})


@router.get("/research/candles.csv", response_class=PlainTextResponse)
async def research_candles_csv(day: date = Query(...)) -> PlainTextResponse:
    body = await asyncio.to_thread(research.candles_csv, ctx().db, day.isoformat())
    if body is None:
        raise HTTPException(status_code=404, detail=f"no one-minute contract prices were kept for {day}")
    return PlainTextResponse(body, media_type="text/csv", headers={
        "Content-Disposition": f'attachment; filename="meridian-contract-prices-{day}.csv"'})


@router.get("/trades/{trade_id}/detail")
async def trade_detail(trade_id: int) -> dict:
    """One trade on one page: its price path, the journal around it, Angel's order ids,
    the fill against the price before it, and what each exit rule set would have done."""
    db = ctx().db
    found = await asyncio.to_thread(research.trade_detail, db, trade_id)
    if found is None:
        raise HTTPException(status_code=404, detail="no such trade")
    return found


# ── live results against what the backtest led us to expect ──────────────────
K_EXPECTATION = "research:expectation"


class ExpectationRequest(BaseModel):
    """What one trade is expected to make and how much it typically swings, from the backtest."""
    mean: float = Field(ge=-20_000, le=20_000)
    sd: float = Field(ge=50, le=100_000)
    since: date
    label: str = Field(default="", max_length=80)


def _expectation_view(db) -> dict:
    from app import ledger

    exp = db.kv_get(K_EXPECTATION, None)
    trades = []
    if exp:
        with db.conn() as c:
            rows = [dict(r) for r in c.execute(
                "SELECT * FROM trades WHERE mode = 'live' AND exit_ts IS NOT NULL AND session_date >= ? ORDER BY entry_ts",
                (exp["since"],))]
        for t in ledger.reconcile(db, rows):
            net = t.get("net_angel") if t.get("net_angel") is not None else t.get("net")
            if net is not None:
                trades.append({"day": t["session_date"], "net": round(float(net), 2), "lots": t.get("lots") or 1})
    return {"expectation": exp, "trades": trades}


@router.get("/research/expectation")
async def research_expectation() -> dict:
    """The expectation on record and the real-money trades since its start date."""
    return _expectation_view(ctx().db)


@router.post("/research/expectation")
async def set_research_expectation(body: ExpectationRequest, request: Request,
                                   principal: Principal = Depends(require_auth)) -> dict:
    c = ctx()
    c.db.kv_set(K_EXPECTATION, {"mean": body.mean, "sd": body.sd, "since": body.since.isoformat(),
                                "label": body.label.strip(), "set_by": principal.subject,
                                "set_at": now_ist().isoformat(timespec="seconds")})
    c.db.audit(principal.subject, "research.expectation", f"mean={body.mean} sd={body.sd} since={body.since}",
               client_ip(request))
    return _expectation_view(c.db)
