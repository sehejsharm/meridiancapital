"""Everything the deck renders that is not an algorithm: health, news, ticker,
and the downloadable reports.
"""

from __future__ import annotations

import asyncio

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import PlainTextResponse

from app import health, logs, market, reports
from app.deps import ctx
from app.feeds import NewsFeed
from app.security import require_auth
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
    return market.option_chain(ctx().db)


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

    for algo in c.db.algos():
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


def _payload(algo_id: str | None, start: str, end: str, title: str) -> dict:
    if start > end:
        raise HTTPException(status_code=400, detail="start date must not be after end date")
    return reports.build_payload(ctx().db, algo_id, start, end, title)


@router.get("/reports")
async def report_json(
    start: str, end: str, algo_id: str | None = None, title: str = "Meridian session report"
) -> dict:
    """The same payload the CSV and PDF are rendered from, for the results page."""
    return _payload(algo_id, start, end, title)


@router.get("/reports.csv", response_class=PlainTextResponse)
async def report_csv(
    start: str, end: str, algo_id: str | None = None, title: str = "Meridian session report"
) -> Response:
    payload = _payload(algo_id, start, end, title)
    return Response(
        content=reports.to_csv(payload),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{reports.filename(payload, "csv")}"'},
    )


@router.get("/reports.pdf")
async def report_pdf(
    start: str, end: str, algo_id: str | None = None, title: str = "Meridian session report"
) -> Response:
    payload = _payload(algo_id, start, end, title)
    return Response(
        content=reports.to_pdf(payload),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{reports.filename(payload, "pdf")}"'},
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
