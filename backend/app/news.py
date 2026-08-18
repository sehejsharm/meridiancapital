"""Market news.

Headlines are fetched here rather than in the browser: the feeds do not send
CORS headers, so a page on vercel.app cannot read them directly, and doing it
server-side means one fetch serves every connected device instead of one per
tab.

Nothing here is allowed to affect trading. The fetch runs on a worker thread
with a short timeout, every failure degrades to the last good cache, and an
empty list is a perfectly acceptable answer — a news panel is not worth a
single missed fill.
"""
from __future__ import annotations

import logging
import re
import threading
import time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Optional

log = logging.getLogger("meridian.news")

# Indian market feeds, most specific first. Several are listed so one source
# going down does not empty the panel.
FEEDS: list[tuple[str, str]] = [
    ("Moneycontrol", "https://www.moneycontrol.com/rss/marketreports.xml"),
    ("Moneycontrol Business", "https://www.moneycontrol.com/rss/business.xml"),
    ("Economic Times", "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms"),
    ("Business Standard", "https://www.business-standard.com/rss/markets-106.rss"),
]

TTL_SECONDS = 300           # five minutes; headlines do not move faster than that
FETCH_TIMEOUT = 6           # a slow feed must not hold a request open
MAX_ITEMS = 40

_lock = threading.Lock()
_cache: dict[str, Any] = {"items": [], "fetched_at": 0.0, "sources": [], "error": None}

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _clean(text: Optional[str]) -> str:
    """Feed descriptions arrive as HTML fragments; the panel wants a sentence."""
    if not text:
        return ""
    out = _TAG_RE.sub(" ", text)
    out = (out.replace("&nbsp;", " ").replace("&amp;", "&")
              .replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"')
              .replace("&#039;", "'").replace("&apos;", "'"))
    return _WS_RE.sub(" ", out).strip()


def _parse_when(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    try:
        dt = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone().isoformat(timespec="seconds")


def _fetch_one(source: str, url: str) -> list[dict]:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "MeridianCapital/1.0 (+dashboard news panel)"},
    )
    with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as resp:
        raw = resp.read()

    root = ET.fromstring(raw)
    items = []
    for node in root.iter("item"):
        title = _clean(node.findtext("title"))
        if not title:
            continue
        items.append({
            "title": title,
            "url": (node.findtext("link") or "").strip(),
            "summary": _clean(node.findtext("description"))[:260],
            "published": _parse_when(node.findtext("pubDate")),
            "source": source,
        })
    return items


def _refresh() -> dict:
    items: list[dict] = []
    sources: list[str] = []
    errors: list[str] = []

    for source, url in FEEDS:
        try:
            got = _fetch_one(source, url)
            if got:
                items.extend(got)
                sources.append(source)
        except Exception as exc:                     # noqa: BLE001 - never propagate
            errors.append(f"{source}: {type(exc).__name__}")
            log.debug("news feed failed (%s): %s", source, exc)

    # Same story syndicated by two outlets should appear once.
    seen: set[str] = set()
    unique = []
    for it in sorted(items, key=lambda i: i.get("published") or "", reverse=True):
        key = _WS_RE.sub(" ", it["title"].lower())[:90]
        if key in seen:
            continue
        seen.add(key)
        unique.append(it)

    return {
        "items": unique[:MAX_ITEMS],
        "fetched_at": time.time(),
        "sources": sources,
        "error": "; ".join(errors) if errors and not unique else None,
    }


def headlines(force: bool = False) -> dict:
    """Cached headlines. Safe to call from a request handler."""
    with _lock:
        fresh = (time.time() - _cache["fetched_at"]) < TTL_SECONDS
        if fresh and not force and _cache["items"]:
            cached = dict(_cache)
            cached["cached"] = True
            return cached

    result = _refresh()

    with _lock:
        # A failed refresh keeps whatever was last known good rather than
        # blanking a panel that was working a minute ago.
        if not result["items"] and _cache["items"]:
            stale = dict(_cache)
            stale["cached"] = True
            stale["stale"] = True
            stale["error"] = result["error"]
            return stale
        _cache.update(result)
        out = dict(_cache)

    out["cached"] = False
    return out


def age_seconds() -> Optional[int]:
    if not _cache["fetched_at"]:
        return None
    return int(time.time() - _cache["fetched_at"])
