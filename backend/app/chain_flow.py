"""Option flow: what the chain did through the day, minute by minute.

The chain is a snapshot (price, the day's volume and open interest for each
contract). Kept once a minute, the snapshots show the flow:

* **money flow** — rupees of premium traded each minute in calls and in puts
  (the minute's new volume times its price);
* **positioning by strike** over the last few minutes — the standard reading
  of price and open interest together:

  =================  ===========  ======================================
  premium            open int.    reading
  =================  ===========  ======================================
  up                 up           long build-up (new buyers)
  down               up           short build-up (new writers)
  up                 down         short covering (writers leaving)
  down               down         long unwinding (buyers leaving)
  =================  ===========  ======================================

Display only. It reads the chain the desk already has and decides nothing.
"""

from __future__ import annotations

K_FLOW = "market:chain:flow:{day}"
MAX_MINUTES = 400          # a whole session of one-minute snapshots
WINDOW_MIN = 15            # positioning is read over this many minutes


def _key(day: str) -> str:
    return K_FLOW.format(day=day)


def record(db, chain: dict | None) -> bool:
    """Keep the chain's minute snapshot (the latest one in each minute). True when stored."""
    if not chain or not chain.get("ts") or not chain.get("rows"):
        return False
    ts = str(chain["ts"])
    day, hhmm = ts[:10], ts[11:16]
    snap, meta = {}, {}
    for row in chain["rows"]:
        for side in ("ce", "pe"):
            q = row.get(side) or {}
            tok = q.get("token")
            if not tok or q.get("ltp") is None:
                continue
            snap[tok] = [q.get("ltp"), q.get("volume"), q.get("oi")]
            meta[tok] = [row.get("strike"), side.upper()]
    if not snap:
        return False
    data = db.kv_get(_key(day), None) or {"minutes": [], "meta": {}}
    minutes = data["minutes"]
    if minutes and minutes[-1][0] == hhmm:
        if minutes[-1][1] == snap:
            return False
        minutes[-1] = [hhmm, snap]
    elif minutes and minutes[-1][0] > hhmm:
        return False                       # an older chain than the last one kept
    else:
        minutes.append([hhmm, snap])
    data["minutes"] = minutes[-MAX_MINUTES:]
    data["meta"].update(meta)
    db.kv_set(_key(day), data)
    return True


def reading(dp: float | None, doi: float | None) -> str:
    if dp is None or doi is None or dp == 0 or doi == 0:
        return "quiet"
    if doi > 0:
        return "long_buildup" if dp > 0 else "short_buildup"
    return "short_covering" if dp > 0 else "long_unwinding"


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None


def flow(data: dict | None, window: int = WINDOW_MIN) -> dict:
    """The day's money flow and the last ``window`` minutes' positioning by strike."""
    if not data or len(data.get("minutes") or []) < 2:
        return {"available": False, "minutes": len((data or {}).get("minutes") or []),
                "reason": "Option flow needs two minutes of the chain. It builds up through the session."}
    minutes, meta = data["minutes"], data.get("meta") or {}
    timeline = []
    for (t0, a), (t1, b) in zip(minutes, minutes[1:]):
        ce = pe = 0.0
        for tok, (ltp, vol, _oi) in b.items():
            prev = a.get(tok)
            v1, v0, px = _num(vol), _num(prev[1]) if prev else None, _num(ltp)
            if v1 is None or v0 is None or px is None or v1 < v0:
                continue
            rupees = (v1 - v0) * px
            if (meta.get(tok) or [None, ""])[1] == "CE":
                ce += rupees
            else:
                pe += rupees
        timeline.append({"t": t1, "ce": round(ce, 2), "pe": round(pe, 2)})

    last_t, last = minutes[-1]
    # The snapshot `window` minutes back, or the first one kept.
    def mins(hhmm: str) -> int:
        return int(hhmm[:2]) * 60 + int(hhmm[3:5])
    base_t, base = minutes[0]
    for t, s in minutes:
        if mins(last_t) - mins(t) >= window:
            base_t, base = t, s
    by_strike: dict[int, dict] = {}
    for tok, (ltp, vol, oi) in last.items():
        strike, side = (meta.get(tok) or [None, None])
        if strike is None:
            continue
        prev = base.get(tok) or [None, None, None]
        p1, p0 = _num(ltp), _num(prev[0])
        v1, v0 = _num(vol), _num(prev[1])
        o1, o0 = _num(oi), _num(prev[2])
        dp = (p1 - p0) if p1 is not None and p0 is not None else None
        doi = (o1 - o0) if o1 is not None and o0 is not None else None
        dv = (v1 - v0) if v1 is not None and v0 is not None and v1 >= v0 else None
        row = by_strike.setdefault(int(strike), {"strike": int(strike)})
        row[side.lower()] = {
            "volume": dv, "oi_change": doi, "price_change": round(dp, 2) if dp is not None else None,
            "turnover": round(dv * p1, 2) if dv is not None and p1 is not None else None,
            "reading": reading(dp, doi),
        }
    ce_total = sum(x["ce"] for x in timeline)
    pe_total = sum(x["pe"] for x in timeline)
    return {
        "available": True,
        "since": minutes[0][0], "to": last_t, "window_from": base_t,
        "timeline": timeline,
        "strikes": sorted(by_strike.values(), key=lambda r: r["strike"]),
        "totals": {"ce": round(ce_total, 2), "pe": round(pe_total, 2),
                   "put_call_ratio": round(pe_total / ce_total, 3) if ce_total else None},
    }


def for_day(db, day: str) -> dict:
    return flow(db.kv_get(_key(day), None))
