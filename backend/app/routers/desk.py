"""Everything the deck renders that is not an algorithm: health, news, ticker,
and the downloadable reports.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import PlainTextResponse

from app import health, reports
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
    return _news.get(force=force)


@router.get("/ticker")
async def ticker() -> dict:
    """Latest spot, taken from whichever engine most recently published one.

    The engines already poll Angel for the index; re-polling it here would
    spend rate-limit budget the trading loop needs, so the ticker reads their
    published snapshots instead.
    """
    c = ctx()
    best = None
    for algo in c.db.algos():
        from shared.db import snapshot_key

        snap = c.db.kv_get(snapshot_key(algo["id"]), None)
        if not snap:
            continue
        market = snap.get("market") or {}
        if market.get("spot") is None:
            continue
        if best is None or (snap.get("ts") or "") > (best.get("ts") or ""):
            best = {**market, "ts": snap.get("ts"), "algo_id": algo["id"]}

    return {
        "spot": (best or {}).get("spot"),
        "bar_close": (best or {}).get("bar_close"),
        "channel_high": (best or {}).get("channel_high"),
        "channel_low": (best or {}).get("channel_low"),
        "ts": (best or {}).get("ts"),
        "source_algo": (best or {}).get("algo_id"),
        "server_time": now_ist().isoformat(timespec="seconds"),
        "live": best is not None,
    }


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
