"""Outward-facing feeds the dashboard needs: market news and a spot ticker.

Both are cached and served from the control plane rather than fetched by the
browser. Three reasons: the dashboard holds no third-party credentials, the
publishers see one polite caller instead of one per open tab, and a feed being
slow or down degrades into stale data rather than a broken page.

News is public RSS. No key, no account, and nothing here republishes an
article — the panel shows headline, source, time and a link out, which is what
an RSS feed is published for.
"""

from __future__ import annotations

import re
import threading
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

USER_AGENT = "MeridianCapitalDashboard/1.0 (+private trading desk)"
FETCH_TIMEOUT = 8.0
NEWS_TTL_SEC = 180.0
MAX_ITEMS = 40

FEEDS: tuple[tuple[str, str], ...] = (
    ("Moneycontrol", "https://www.moneycontrol.com/rss/marketreports.xml"),
    ("Moneycontrol Business", "https://www.moneycontrol.com/rss/business.xml"),
    ("Economic Times Markets", "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms"),
    ("Business Standard Markets", "https://www.business-standard.com/rss/markets-106.rss"),
    ("The Hindu BusinessLine", "https://www.thehindubusinessline.com/markets/feeder/default.rss"),
)

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _clean(text: str | None, limit: int = 300) -> str:
    if not text:
        return ""
    out = _WS_RE.sub(" ", _TAG_RE.sub(" ", text)).strip()
    return out[:limit]


def _parse_when(raw: str | None) -> str | None:
    if not raw:
        return None
    try:
        dt = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def _fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as resp:  # noqa: S310 - fixed https list
        return resp.read()


def _parse_rss(source: str, payload: bytes) -> list[dict]:
    items: list[dict] = []
    try:
        root = ET.fromstring(payload)
    except ET.ParseError:
        return items

    # RSS 2.0 (<item>) and Atom (<entry>) both appear among Indian market feeds.
    nodes = root.iter("item")
    for node in nodes:
        title = _clean(node.findtext("title"), 220)
        link = (node.findtext("link") or "").strip()
        if not title or not link:
            continue
        items.append(
            {
                "source": source,
                "title": title,
                "link": link,
                "summary": _clean(node.findtext("description"), 280),
                "published": _parse_when(node.findtext("pubDate")),
            }
        )
    return items


@dataclass
class _Cache:
    items: list[dict] = field(default_factory=list)
    fetched_at: float = 0.0
    errors: list[str] = field(default_factory=list)


class NewsFeed:
    """Polls a fixed set of public market RSS feeds, deduped and time-ordered."""

    def __init__(self, feeds=FEEDS, ttl: float = NEWS_TTL_SEC):
        self.feeds = feeds
        self.ttl = ttl
        self._cache = _Cache()
        self._lock = threading.Lock()

    def _refresh(self) -> None:
        items: list[dict] = []
        errors: list[str] = []
        for source, url in self.feeds:
            try:
                items.extend(_parse_rss(source, _fetch(url)))
            except (urllib.error.URLError, OSError, ValueError) as exc:
                errors.append(f"{source}: {type(exc).__name__}")

        seen: set[str] = set()
        deduped = []
        for item in items:
            key = item["title"].lower()[:90]
            if key in seen:
                continue
            seen.add(key)
            deduped.append(item)

        deduped.sort(key=lambda i: i.get("published") or "", reverse=True)

        if not deduped and self._cache.items:
            # Every source failed. Keep what was last known good and say so —
            # per-source failures are handled above, so this method does not
            # raise, and replacing the cache here would blank the panel.
            self._cache = _Cache(
                items=self._cache.items,
                fetched_at=time.time(),
                errors=errors or ["every source failed"],
            )
            return

        self._cache = _Cache(items=deduped[:MAX_ITEMS], fetched_at=time.time(), errors=errors)

    def get(self, force: bool = False) -> dict:
        with self._lock:
            stale = time.time() - self._cache.fetched_at > self.ttl
            if force or stale or not self._cache.items:
                # A failed refresh keeps whatever was cached; an empty panel is
                # worse than a slightly old one.
                previous = self._cache
                try:
                    self._refresh()
                except Exception as exc:  # never let the news take down a request
                    self._cache = _Cache(
                        items=previous.items,
                        fetched_at=time.time(),
                        errors=[f"refresh failed: {type(exc).__name__}: {exc}"],
                    )
            cache = self._cache
        age = time.time() - cache.fetched_at
        return {
            "items": cache.items,
            "fetched_at": datetime.fromtimestamp(cache.fetched_at, timezone.utc).isoformat()
            if cache.fetched_at
            else None,
            "age_seconds": round(age, 1),
            "stale": age > self.ttl * 2,
            "sources": [name for name, _ in self.feeds],
            "errors": cache.errors,
        }
