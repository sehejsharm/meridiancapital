"""How reliably the desk ran: crash loops, Angel API trouble, feed divergence, uptime.

All of it was already in the database and none of it was on screen. On 21 Oct
2026 the live program exited with code 1 seven times between 09:05 and 09:49
while it held real money, and the only way to find out was to read the run
history line by line. Angel's read timeouts, rate limits and "invalid response
from upstream server" turned up most days; the one hint was "241 errors logged"
in a report's drawdown tile. And "price stream vs Angel's candles: closes
differ by up to 371.1 pts" sat in the journal like any other line.

This reads the run history and the journal and returns:

* crash loops: an engine exiting non-zero ``LOOP_THRESHOLD`` or more times with
  no more than ``LOOP_GAP_MIN`` minutes between exits — the desk raises a banner
  for one until the operator acknowledges it;
* Angel API errors per day by kind (timeouts, rate limits, upstream errors) and
  the trend week on week;
* every feed-divergence event with its size;
* each trading day's engine uptime over market hours.

An exit the operator or scheduler asked for (``stopped on request``) is never a
crash, whatever its exit code.
"""

from __future__ import annotations

import re
import threading
import time
from datetime import date, datetime, timedelta

from engine.clock import now_ist

LOOP_GAP_MIN = 30        # exits this close together belong to one episode
LOOP_THRESHOLD = 3       # this many abnormal exits in one episode is a crash loop
ALERT_HOURS = 18         # an episode stays on the banner this long unless acknowledged
MARKET_OPEN, MARKET_CLOSE = (9, 15), (15, 30)
ACK_KEY = "reliability:acks"

TIMEOUT = re.compile(r"timed?[ -]?out|ReadTimeout|ConnectTimeout|timeout", re.I)
RATE_LIMIT = re.compile(r"exceed\w* access rate|too many requests|\b429\b|rate[ -]?limit", re.I)
UPSTREAM = re.compile(r"invalid response from upstream|upstream server|bad gateway|service unavailable|"
                      r"gateway time-?out|\b50[234]\b", re.I)
# The Angel SDK's own line for a request that failed below HTTP (connection reset, pool exhausted, DNS): it was
# "other errors" on the page, though every one of them is Angel's API not answering.
CONNECTION = re.compile(r"HTTPSConnectionPool|Max retries exceeded|Connection (?:aborted|reset|refused)|"
                        r"RemoteDisconnected|NameResolutionError|Error occurred while making a (?:GET|POST|PUT|DELETE) request",
                        re.I)
# The program is flat by 15:10; what its feed check reports after that touches no trade.
AFTER_CLOSE = "15:10"
DIVERGENCE = re.compile(r"price stream vs Angel'?s candles:.*?differ by up to\s+([\d.]+)\s*pts", re.I)
# The desk's own housekeeping is not the engine talking to Angel.
_EXCLUDED_SOURCES = ("api", "supervisor")


def _ts(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        d = datetime.fromisoformat(str(s).replace(" ", "T")[:19])
    except ValueError:
        return None
    return d.replace(tzinfo=None)


def _now() -> datetime:
    return now_ist().replace(tzinfo=None)


# ── runs ─────────────────────────────────────────────────────────────────────

def abnormal(run: dict) -> bool:
    """Exited on its own with a non-zero code: a crash, not a stop."""
    code = run.get("exit_code")
    if code in (None, 0) or not run.get("stopped_ts"):
        return False
    return not str(run.get("reason") or "").startswith("stopped on request")


def crash_episodes(runs: list[dict], gap_min: int = LOOP_GAP_MIN, threshold: int = LOOP_THRESHOLD) -> dict:
    """Abnormal exits grouped per algorithm into episodes; loops and single crashes."""
    by_algo: dict[str, list[dict]] = {}
    for r in runs:
        if abnormal(r):
            by_algo.setdefault(r.get("algo_id") or "", []).append(r)
    loops, singles = [], []
    for algo_id, rs in by_algo.items():
        rs.sort(key=lambda r: str(r["stopped_ts"]))
        cluster: list[dict] = []

        def flush():
            if not cluster:
                return
            item = {
                "algo_id": algo_id,
                "key": f"{algo_id}:{cluster[0]['stopped_ts']}",
                "first_exit": cluster[0]["stopped_ts"],
                "last_exit": cluster[-1]["stopped_ts"],
                "exits": len(cluster),
                "codes": [r.get("exit_code") for r in cluster],
                "mode": cluster[-1].get("mode"),
            }
            (loops if len(cluster) >= threshold else singles).append(item)

        for r in rs:
            t = _ts(r["stopped_ts"])
            prev = _ts(cluster[-1]["stopped_ts"]) if cluster else None
            if cluster and (t is None or prev is None or (t - prev) > timedelta(minutes=gap_min)):
                flush()
                cluster = []
            cluster.append(r)
        flush()
    loops.sort(key=lambda e: str(e["last_exit"]), reverse=True)
    singles.sort(key=lambda e: str(e["last_exit"]), reverse=True)
    return {"loops": loops, "crashes": singles}


def runs_since(db, since: str) -> list[dict]:
    with db.conn() as c:
        rows = c.execute(
            "SELECT * FROM engine_runs WHERE started_ts >= ? ORDER BY id", (since,)
        ).fetchall()
    return [dict(r) for r in rows]


def _window(day: date) -> tuple[datetime, datetime]:
    return (datetime.combine(day, datetime.min.time()).replace(hour=MARKET_OPEN[0], minute=MARKET_OPEN[1]),
            datetime.combine(day, datetime.min.time()).replace(hour=MARKET_CLOSE[0], minute=MARKET_CLOSE[1]))


def _merge(spans: list[tuple[datetime, datetime]]) -> list[list[datetime]]:
    merged: list[list[datetime]] = []
    for a, b in sorted(spans):
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return merged


def _overlap(a: list[list[datetime]], b: list[list[datetime]]) -> float:
    return sum(max(0.0, (min(y1, y2) - max(x1, x2)).total_seconds()) for x1, y1 in a for x2, y2 in b)


def uptime(runs: list[dict], algo_id: str, now: datetime | None = None) -> dict:
    """Each day the algorithm ran: the share of market hours (09:15-15:30) a process was up when it was meant
    to be. After a stop that was asked for (the operator's, or the schedule's at the session's end) the engine is
    meant to be down until it is started again, so that time is not counted against it; a crash still is.
    25 Nov 2026: a manual stop at 11:01 after the day's one trade, and the 15:25 session end every day, made the
    page say 88.3% and red."""
    now = now or _now()
    spans: dict[date, list[tuple[datetime, datetime]]] = {}
    off: dict[date, list[tuple[datetime, datetime]]] = {}

    def clip(store, start, stop):
        day = start.date()
        while day <= stop.date():
            lo, hi = _window(day)
            a, b = max(start, lo), min(stop, hi, now)
            if b > a:
                store.setdefault(day, []).append((a, b))
            day += timedelta(days=1)

    mine = sorted((r for r in runs if (r.get("algo_id") or "") == algo_id and _ts(r.get("started_ts"))),
                  key=lambda r: _ts(r.get("started_ts")))
    for i, r in enumerate(mine):
        start = _ts(r.get("started_ts"))
        stop = _ts(r.get("stopped_ts")) or now
        clip(spans, start, stop)
        if r.get("stopped_ts") and str(r.get("reason") or "").startswith("stopped on request"):
            nxt = _ts(mine[i + 1].get("started_ts")) if i + 1 < len(mine) else None
            end = min(nxt, now) if nxt else now
            end = min(end, datetime.combine(stop.date(), datetime.min.time()).replace(
                hour=MARKET_CLOSE[0], minute=MARKET_CLOSE[1]))                  # an asked-for stop covers its own day
            if end > stop:
                clip(off, stop, end)
    daily = []
    for day in sorted(spans):
        lo, hi = _window(day)
        hi = min(hi, now)
        if hi <= lo:
            continue
        merged = _merge(spans[day])
        up = sum((b - a).total_seconds() for a, b in merged)
        stopped = _merge(off.get(day, []))
        meant_off = sum((b - a).total_seconds() for a, b in stopped) - _overlap(stopped, merged)
        total = (hi - lo).total_seconds() - max(0.0, meant_off)
        if total <= 0:
            continue
        daily.append({"date": day.isoformat(), "pct": round(min(100.0, 100 * up / total), 1),
                      "down_min": round(max(0.0, total - up) / 60, 1), "off_min": round(max(0.0, meant_off) / 60, 1)})
    overall = round(sum(d["pct"] for d in daily) / len(daily), 1) if daily else None
    return {"daily": daily, "overall_pct": overall}


# ── journal ──────────────────────────────────────────────────────────────────

def classify_api(message: str) -> str | None:
    """Which kind of Angel API trouble a journal line reports, if any."""
    if RATE_LIMIT.search(message):
        return "rate_limits"
    if UPSTREAM.search(message):
        return "upstream"
    if TIMEOUT.search(message):
        return "timeouts"
    if CONNECTION.search(message):
        return "connection"
    return None


def journal_since(db, since: str, algo_id: str | None = None) -> list[dict]:
    sql = ("SELECT id, ts, level, source, message, algo_id FROM events WHERE ts >= ? "
           "AND (level IN ('warn','error','critical') OR message LIKE '%differ by up to%')")
    args: list = [since]
    if algo_id:
        sql += " AND algo_id = ?"
        args.append(algo_id)
    with db.conn() as c:
        return [dict(r) for r in c.execute(sql + " ORDER BY id", args).fetchall()]


def api_errors(events: list[dict], days: list[str]) -> dict:
    blank = {"timeouts": 0, "rate_limits": 0, "upstream": 0, "connection": 0, "other_errors": 0}
    per_day = {d: dict(blank) for d in days}
    for e in events:
        if e.get("source") in _EXCLUDED_SOURCES:
            continue
        day = str(e.get("ts") or "")[:10]
        if day not in per_day:
            continue
        msg = e.get("message") or ""
        if DIVERGENCE.search(msg):
            continue
        kind = classify_api(msg)
        if kind:
            per_day[day][kind] += 1
        elif e.get("level") in ("error", "critical"):
            per_day[day]["other_errors"] += 1
    daily = [{"date": d, **v, "total": sum(v.values())} for d, v in per_day.items()]
    weeks: dict[str, dict] = {}
    for row in daily:
        d = date.fromisoformat(row["date"])
        wk = (d - timedelta(days=d.weekday())).isoformat()
        w = weeks.setdefault(wk, {"week_of": wk, **blank, "total": 0})
        for k in (*blank, "total"):
            w[k] += row[k]
    weekly = sorted(weeks.values(), key=lambda w: w["week_of"])
    totals = {k: sum(r[k] for r in daily) for k in (*blank, "total")}
    trend = None
    if len(weekly) >= 2:
        last, prev = weekly[-1]["total"], weekly[-2]["total"]
        trend = {"this_week": last, "last_week": prev,
                 "change_pct": round(100 * (last - prev) / prev, 1) if prev else None}
    return {"daily": daily, "weekly": weekly, "totals": totals, "trend": trend}


def divergences(events: list[dict]) -> dict:
    """Every stream-vs-candle disagreement, marked when it came after the 15:10 close. Every one logged up to
    26 Nov 2026 came between 15:21 and 15:25, with the program flat: the feed's last minutes, never a trade's."""
    out = []
    for e in events:
        m = DIVERGENCE.search(e.get("message") or "")
        if m:
            ts = str(e.get("ts") or "")
            out.append({"ts": e.get("ts"), "pts": float(m.group(1)), "algo_id": e.get("algo_id"),
                        "after_close": ts[11:16] >= AFTER_CLOSE})
    live = [x for x in out if not x["after_close"]]
    return {"events": out, "count": len(out), "max_pts": max((x["pts"] for x in out), default=None),
            "in_session_count": len(live), "in_session_max_pts": max((x["pts"] for x in live), default=None)}


# ── the report and the banner ────────────────────────────────────────────────

def acks(db) -> set[str]:
    return set(db.kv_get(ACK_KEY, None) or [])


def acknowledge(db, key: str) -> None:
    keep = sorted(acks(db) | {key})[-200:]
    db.kv_set(ACK_KEY, keep)


def report(db, days: int = 30, algo_id: str | None = None) -> dict:
    now = _now()
    first = (now.date() - timedelta(days=days - 1))
    since = first.isoformat()
    runs = runs_since(db, since)
    names = {a["id"]: a.get("name") or a["id"] for a in db.algos()}
    episodes = crash_episodes(runs)
    for e in episodes["loops"] + episodes["crashes"]:
        e["name"] = names.get(e["algo_id"], e["algo_id"])
    journal = journal_since(db, since, algo_id)
    day_list = [(first + timedelta(days=i)).isoformat() for i in range(days)]
    if algo_id is None:
        from app import ledger
        algo_id = ledger.live_algo(db)
    up = uptime(runs, algo_id, now) if algo_id else {"daily": [], "overall_pct": None}
    return {
        "days": days,
        "since": since,
        "algo_id": algo_id,
        "algo_name": names.get(algo_id, algo_id) if algo_id else None,
        "api": api_errors(journal, day_list),
        "divergence": divergences(journal),
        "runs": {**episodes, "abnormal_exits": sum(1 for r in runs if abnormal(r)), "total_runs": len(runs)},
        "uptime": up,
        "alerts": active_alerts(db, episodes, now),
        "thresholds": {"loop_gap_min": LOOP_GAP_MIN, "loop_threshold": LOOP_THRESHOLD},
    }


def active_alerts(db, episodes: dict, now: datetime | None = None) -> list[dict]:
    now = now or _now()
    seen = acks(db)
    out = []
    for e in episodes["loops"]:
        last = _ts(e["last_exit"])
        if last and now - last <= timedelta(hours=ALERT_HOURS) and e["key"] not in seen:
            out.append({"kind": "crash_loop", **e})
    return out


_cache: dict[str, tuple[float, list]] = {}
_lock = threading.Lock()


def banner_alerts(db, ttl: float = 10.0) -> list[dict]:
    """The banner's alerts, cached: the live feed asks for status every second."""
    key = str(getattr(db, "path", id(db)))
    with _lock:
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < ttl:
            return hit[1]
    since = (_now() - timedelta(hours=ALERT_HOURS + 2)).strftime("%Y-%m-%dT%H:%M:%S")
    episodes = crash_episodes(runs_since(db, since[:10]))
    names = {a["id"]: a.get("name") or a["id"] for a in db.algos()}
    alerts = active_alerts(db, episodes)
    for a in alerts:
        a["name"] = names.get(a["algo_id"], a["algo_id"])
    with _lock:
        _cache[key] = (time.time(), alerts)
    return alerts


def clear_cache() -> None:
    with _lock:
        _cache.clear()
