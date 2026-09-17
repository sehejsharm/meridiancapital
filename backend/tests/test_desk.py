"""Deck feeds: health, news, ticker and the downloadable reports."""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from app.security import hash_password
from tests.test_algos import FakeSupervisor  # noqa: F401 - reused test double

PASSWORD = "meridian-test-password"
os.environ.setdefault("MERIDIAN_PASSWORD_HASH", hash_password(PASSWORD, rounds=1000))


@pytest.fixture
def client(monkeypatch, tmp_path):
    from app import deps
    from app.config import settings
    from tests.test_algos import FakeSupervisor as FS

    monkeypatch.setattr(settings, "db_path", tmp_path / "desk.db")
    monkeypatch.setattr(settings, "password_hash", os.environ["MERIDIAN_PASSWORD_HASH"])
    monkeypatch.setattr(deps, "Supervisor", FS)

    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture
def auth(client):
    r = client.post("/api/auth/login", json={"password": PASSWORD})
    return {"Authorization": f"Bearer {r.json()['token']}"}


@pytest.fixture
def seeded(client, auth):
    from app.deps import ctx

    db = ctx().db
    for i in range(4):
        db.add_trade({
            "entry_ts": f"2026-09-0{i + 1}T10:20", "exit_ts": f"2026-09-0{i + 1}T11:00",
            "session_date": f"2026-09-0{i + 1}", "mode": "paper", "view": "C",
            "tsym": f"NIFTY10SEP2624{i}00CE", "lots": 2, "qty": 150,
            "entry_prem": 140.0, "exit_prem": 170.0 if i % 2 else 110.0,
            "net": 2000.0 if i % 2 else -1200.0, "charges": 80.0,
            "reason": "TARGET" if i % 2 else "STOP", "algo_id": "gk50k",
        })
    db.add_equity_sample("2026-09-01", 50000, 0, 50000, 0, algo_id="gk50k")
    db.add_equity_sample("2026-09-02", 46000, -4000, 50000, -4000, algo_id="gk50k")
    db.add_event("error", "broker timeout", source="engine", algo_id="gk50k")
    return db


# ── auth ─────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("path", ["/api/health/detail", "/api/news", "/api/ticker"])
def test_desk_feeds_need_a_token(client, path):
    assert client.get(path).status_code == 401


# ── health ───────────────────────────────────────────────────────────────────
def test_health_detail_reports_every_component(client, auth):
    body = client.get("/api/health/detail", headers=auth).json()
    assert body["state"] in ("ok", "warning", "critical", "unknown")
    keys = {c["key"] for c in body["checks"]}
    assert {"memory", "disk", "database", "engines"} <= keys
    assert body["uptime_seconds"] >= 0


def test_health_grades_thresholds_in_both_directions():
    from app.health import _grade

    assert _grade(2500, 1024, 2048, higher_is_worse=True) == "critical"
    assert _grade(1500, 1024, 2048, higher_is_worse=True) == "warning"
    assert _grade(200, 1024, 2048, higher_is_worse=True) == "ok"
    # Disk is the other way round: small is bad.
    assert _grade(100, 1024, 256, higher_is_worse=False) == "critical"
    assert _grade(800, 1024, 256, higher_is_worse=False) == "warning"
    assert _grade(9000, 1024, 256, higher_is_worse=False) == "ok"
    assert _grade(None, 1, 2, higher_is_worse=True) == "unknown"


# ── news ─────────────────────────────────────────────────────────────────────
def test_news_never_raises_when_every_feed_is_down(client, auth, monkeypatch):
    from app import feeds

    def boom(url):
        raise OSError("network unreachable")

    monkeypatch.setattr(feeds, "_fetch", boom)
    fresh = feeds.NewsFeed()
    body = fresh.get(force=True)
    assert body["items"] == []
    assert body["errors"], "a total outage should be reported, not hidden"


def test_news_parses_rss_and_drops_incomplete_items():
    from app.feeds import _parse_rss

    xml = b"""<?xml version="1.0"?><rss version="2.0"><channel>
    <item><title>Nifty ends higher</title><link>https://e.test/a</link>
      <description>&lt;p&gt;Markets   rose&lt;/p&gt;</description>
      <pubDate>Mon, 14 Sep 2026 10:30:00 +0530</pubDate></item>
    <item><title>Missing link</title></item>
    <item><link>https://e.test/c</link></item>
    </channel></rss>"""
    items = _parse_rss("Test", xml)
    assert len(items) == 1
    assert items[0]["summary"] == "Markets rose"  # tags stripped, whitespace collapsed
    assert items[0]["published"].startswith("2026-09-14T05:00")


def test_news_deduplicates_across_sources(monkeypatch):
    from app import feeds

    def two_sources(url):
        return b"""<?xml version="1.0"?><rss version="2.0"><channel>
        <item><title>Same headline everywhere</title><link>https://e.test/x</link>
        <pubDate>Mon, 14 Sep 2026 10:30:00 +0530</pubDate></item>
        </channel></rss>"""

    monkeypatch.setattr(feeds, "_fetch", two_sources)
    feed = feeds.NewsFeed(feeds=(("A", "https://a.test"), ("B", "https://b.test")))
    body = feed.get(force=True)
    assert len(body["items"]) == 1, "the same headline from two sources must appear once"


def test_news_keeps_the_last_good_payload_when_a_refresh_fails(monkeypatch):
    from app import feeds

    good = b"""<?xml version="1.0"?><rss version="2.0"><channel>
    <item><title>Cached headline</title><link>https://e.test/1</link>
    <pubDate>Mon, 14 Sep 2026 10:30:00 +0530</pubDate></item></channel></rss>"""
    monkeypatch.setattr(feeds, "_fetch", lambda url: good)
    feed = feeds.NewsFeed(feeds=(("A", "https://a.test"),))
    assert feed.get(force=True)["items"]

    # Age the cache past the TTL so the next call is a genuine refresh rather
    # than one the force-throttle serves from cache.
    feed._cache.fetched_at -= feed.ttl + 1

    monkeypatch.setattr(feeds, "_fetch", lambda url: (_ for _ in ()).throw(OSError("down")))
    after = feed.get(force=True)
    assert after["items"], "a failed refresh must not blank the panel"
    assert after["errors"]


# ── ticker ───────────────────────────────────────────────────────────────────
def test_ticker_is_empty_before_any_engine_publishes(client, auth):
    body = client.get("/api/ticker", headers=auth).json()
    assert body["live"] is False and body["spot"] is None
    assert body["server_time"]


def test_ticker_reads_the_latest_engine_snapshot(client, auth):
    from app.deps import ctx
    from shared.db import snapshot_key

    ctx().db.kv_set(snapshot_key("gk50k"), {
        "ts": "2026-09-14T11:00:00",
        "market": {"spot": 24981.5, "channel_high": 25100.0, "channel_low": 24800.0},
    })
    body = client.get("/api/ticker", headers=auth).json()
    assert body["spot"] == 24981.5 and body["live"] is True
    assert body["source_algo"] == "gk50k"


# ── reports ──────────────────────────────────────────────────────────────────
def test_report_summary_maths(client, auth, seeded):
    body = client.get(
        "/api/reports", params={"start": "2026-09-01", "end": "2026-09-30"}, headers=auth
    ).json()
    s = body["summary"]
    assert s["trades"] == 4 and s["wins"] == 2 and s["losses"] == 2
    assert s["net_pnl"] == pytest.approx(1600.0)          # 2*2000 - 2*1200
    assert s["profit_factor"] == pytest.approx(4000 / 2400, rel=1e-3)
    assert s["max_drawdown_pct"] == pytest.approx(8.0)     # 50000 -> 46000
    assert s["errors"] == 1


def test_report_respects_the_date_window(client, auth, seeded):
    body = client.get(
        "/api/reports", params={"start": "2026-09-01", "end": "2026-09-02"}, headers=auth
    ).json()
    assert body["summary"]["trades"] == 2


def test_an_inverted_date_range_is_refused(client, auth):
    r = client.get("/api/reports", params={"start": "2026-09-30", "end": "2026-09-01"}, headers=auth)
    assert r.status_code == 400


def test_csv_download_carries_every_section(client, auth, seeded):
    r = client.get("/api/reports.csv", params={"start": "2026-09-01", "end": "2026-09-30"}, headers=auth)
    assert r.status_code == 200
    assert "attachment;" in r.headers["content-disposition"]
    assert r.headers["content-disposition"].endswith('.csv"')
    for section in ("SUMMARY", "TRADES", "EQUITY MARKS", "EVENT LOG"):
        assert section in r.text
    assert "broker timeout" in r.text


def test_pdf_download_is_a_real_pdf(client, auth, seeded):
    r = client.get("/api/reports.pdf", params={"start": "2026-09-01", "end": "2026-09-30"}, headers=auth)
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert r.content.startswith(b"%PDF-1.4")
    assert r.content.rstrip().endswith(b"%%EOF")


def test_pdf_paginates_a_long_report(client, auth):
    from app.deps import ctx
    from app import reports as R

    db = ctx().db
    for i in range(300):
        db.add_trade({"entry_ts": "2026-09-01T10:00", "exit_ts": "2026-09-01T10:05",
                      "session_date": "2026-09-01", "mode": "paper", "tsym": f"SYM{i}",
                      "net": 10.0, "algo_id": "gk50k"})
    payload = R.build_payload(db, "gk50k", "2026-09-01", "2026-09-30", "Long")
    pdf = R.to_pdf(payload)
    assert pdf.count(b"/Type /Page\n") >= 0
    assert b"/Count " in pdf
    # More rows than fit on one page must produce more than one page object.
    assert pdf.count(b"/Type /Page ") > 1


def test_reports_are_scoped_to_one_algo(client, auth, seeded):
    from app.deps import ctx

    ctx().db.add_trade({"entry_ts": "2026-09-01T10:20", "session_date": "2026-09-01",
                        "exit_ts": "2026-09-01T11:00", "mode": "paper", "net": 99999.0,
                        "algo_id": "other"})
    mine = client.get("/api/reports", params={"start": "2026-09-01", "end": "2026-09-30",
                                              "algo_id": "gk50k"}, headers=auth).json()
    assert mine["summary"]["net_pnl"] == pytest.approx(1600.0), "another algo's P&L leaked in"


def test_a_forced_refresh_cannot_hammer_the_publishers(monkeypatch):
    """Otherwise ?force=true is an open tap pointed at someone else's server."""
    from app import feeds

    fetches = {"n": 0}

    def counted(url):
        fetches["n"] += 1
        return b"""<?xml version="1.0"?><rss version="2.0"><channel>
        <item><title>Headline</title><link>https://e.test/1</link>
        <pubDate>Mon, 14 Sep 2026 10:30:00 +0530</pubDate></item></channel></rss>"""

    monkeypatch.setattr(feeds, "_fetch", counted)
    feed = feeds.NewsFeed(feeds=(("A", "https://a.test"),))
    feed.get(force=True)
    first = fetches["n"]
    assert first == 1

    for _ in range(20):
        feed.get(force=True)
    assert fetches["n"] == first, "repeated forced refreshes must be served from cache"
