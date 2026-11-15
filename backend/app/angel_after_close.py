"""Read-only questions to Angel after the close, in a process of their own.

    python -m app.angel_after_close --day 2026-11-10 --out result.json --dir /var/lib/meridian/research

Started by the after-close check (``app.close_check``) once nothing is running.
It logs in with the credentials in the server's environment, reads the position
book and the trade book, and keeps the day's one-minute prices for research:
the index, every contract traded, and the contracts a breakout could have
bought in the two nearest weekly expiries. It places nothing and changes
nothing; the only Angel calls are ``position``, ``tradeBook`` and
``getCandleData``.

The result goes to ``--out`` as JSON, never to standard output (Angel's library
prints there). Credentials are read from the environment and appear in neither.
"""

from __future__ import annotations

import argparse
import gzip
import json
import math
import sys
import time
from datetime import date, datetime
from pathlib import Path

STRIKE_STEP = 50
PAD_STRIKES = 2          # strikes beyond the day's range, each side
MAX_CONTRACTS = 90
MAX_EXPIRIES = 2
CANDLE_GAP_SEC = 0.4     # Angel's historical API allows 3 a second


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def fills_from(rows: list[dict]) -> list[dict]:
    """Angel's trade book rows in the desk's shape (no secrets in any field)."""
    out = []
    for r in rows or []:
        price, qty = _num(r.get("fillprice") or r.get("tradeprice")), _num(r.get("fillsize") or r.get("quantity"))
        if not price or not qty:
            continue
        out.append({
            "tsym": str(r.get("tradingsymbol") or ""),
            "side": str(r.get("transactiontype") or "").upper(),
            "qty": int(qty), "price": price,
            "time": str(r.get("filltime") or ""),
            "order_id": str(r.get("orderid") or ""),
            "fill_id": str(r.get("fillid") or ""),
            "exchange": str(r.get("exchange") or ""),
            "product": str(r.get("producttype") or ""),
        })
    return out


def positions_from(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows or []:
        out.append({
            "tsym": str(r.get("tradingsymbol") or ""),
            "token": str(r.get("symboltoken") or ""),
            "exchange": str(r.get("exchange") or ""),
            "net_qty": int(_num(r.get("netqty")) or 0),
            "buy_qty": int(_num(r.get("buyqty")) or 0),
            "sell_qty": int(_num(r.get("sellqty")) or 0),
            "buy_avg": _num(r.get("totalbuyavgprice") or r.get("buyavgprice")),
            "sell_avg": _num(r.get("totalsellavgprice") or r.get("sellavgprice")),
            "realised": _num(r.get("realised")),
        })
    return out


def strikes_for(low: float, high: float, step: int = STRIKE_STEP, pad: int = PAD_STRIKES) -> list[int]:
    """Every strike a breakout could have bought while the index was between ``low`` and ``high``.

    The program buys one strike in the money of the nearest strike, so the range
    is widened by ``pad`` strikes each side.
    """
    lo = int(math.floor(low / step) * step) - pad * step
    hi = int(math.ceil(high / step) * step) + pad * step
    return list(range(lo, hi + step, step))


def pick_contracts(table: dict, day: date, low: float, high: float,
                   expiries: int = MAX_EXPIRIES, limit: int = MAX_CONTRACTS) -> list[dict]:
    """The contracts to keep prices for: the two nearest expiries, around the day's range."""
    upcoming = sorted({exp for (exp, _k, _r) in table if exp >= day})[:expiries]
    wanted = strikes_for(low, high)
    out = []
    for exp in upcoming:
        for strike in wanted:
            for right in ("CE", "PE"):
                hit = table.get((exp, strike, right))
                if hit:
                    out.append({"tsym": hit[0], "token": hit[1], "expiry": exp.isoformat(), "strike": strike,
                                "right": right, "dte": (exp - day).days})
    return out[:limit]


def bars_from(rows: list) -> list[list]:
    """Angel's candle rows as [HH:MM, open, high, low, close, volume]."""
    out = []
    for r in rows or []:
        try:
            hhmm = str(r[0])[11:16]
            out.append([hhmm, float(r[1]), float(r[2]), float(r[3]), float(r[4]), int(float(r[5] or 0))])
        except (IndexError, TypeError, ValueError):
            continue
    return out


def candles(broker, exchange: str, token: str, day: str) -> list[list]:
    broker.rl.acquire("candle")
    resp = broker.api.getCandleData({
        "exchange": exchange, "symboltoken": str(token), "interval": "ONE_MINUTE",
        "fromdate": f"{day} 09:15", "todate": f"{day} 15:30",
    }) or {}
    time.sleep(CANDLE_GAP_SEC)
    return bars_from(resp.get("data") if isinstance(resp, dict) else None)


def collect(broker, day: str, traded: list[str], folder: Path, table: dict | None = None) -> dict:
    """Keep the day's one-minute prices. Returns a short description of what was kept."""
    from engine.config import INDEX_EXCH, INDEX_TOKEN

    index = candles(broker, INDEX_EXCH, INDEX_TOKEN, day)
    if not index:
        return {"kept": False, "reason": "Angel returned no index candles (Historical Data API not enabled?)"}
    low, high = min(b[3] for b in index), max(b[2] for b in index)
    table = table if table is not None else broker.option_table()
    picked = pick_contracts(table, date.fromisoformat(day), low, high)
    by_sym = {c["tsym"].upper(): c for c in picked}
    for (exp, strike, right), (sym, token, _lot) in table.items():
        if sym.upper() in {t.upper() for t in traded} and sym.upper() not in by_sym:
            picked.append({"tsym": sym, "token": token, "expiry": exp.isoformat(), "strike": strike,
                           "right": right, "dte": (exp - date.fromisoformat(day)).days})
    contracts, empty = [], 0
    for c in picked:
        try:
            bars = candles(broker, "NFO", c["token"], day)
        except Exception:  # one contract failing must not lose the rest
            bars = []
        if not bars:
            empty += 1
            continue
        contracts.append({**c, "traded": c["tsym"].upper() in {t.upper() for t in traded}, "bars": bars})
    payload = {"day": day, "fetched_at": datetime.now().isoformat(timespec="seconds"), "source": "Angel getCandleData",
               "index": [b[:5] for b in index], "contracts": contracts}
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"candles-{day}.json.gz"
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        json.dump(payload, fh, separators=(",", ":"))
    return {"kept": True, "file": path.name, "contracts": len(contracts), "without_data": empty,
            "index_bars": len(index), "expiries": sorted({c["expiry"] for c in contracts})}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--dir", required=True)
    ap.add_argument("--no-candles", action="store_true")
    args = ap.parse_args(argv)
    result: dict = {"ok": False, "day": args.day}
    try:
        from engine.broker import Broker, Credentials

        try:
            creds = Credentials.from_env()
        except RuntimeError:
            result["reason"] = "the Angel credentials are not in the server's settings"
            return 0
        try:
            broker = Broker(creds, dry_run=True, log=lambda *_a, **_k: None)
        except Exception as e:
            # Angel's own refusal text (e.g. "Invalid totp"); it carries no credential.
            result["reason"] = f"Angel login failed: {str(e)[:160]}"
            return 0
        broker.rl.acquire("position")
        pos = broker.api.position()
        if not (isinstance(pos, dict) and pos.get("status")):
            result["reason"] = "Angel's position book could not be read"
            return 0
        broker.rl.acquire("tradebook")
        book = broker.api.tradeBook()
        if not (isinstance(book, dict) and book.get("status")):
            result["reason"] = "Angel's trade book could not be read"
            return 0
        result["positions"] = positions_from(pos.get("data") or [])
        result["fills"] = fills_from(book.get("data") or [])
        result["ok"] = True
        if not args.no_candles:
            try:
                traded = sorted({f["tsym"] for f in result["fills"] if f.get("exchange") in ("NFO", "")})
                result["research"] = collect(broker, args.day, traded, Path(args.dir))
            except Exception as e:  # the check stands even if the prices could not be kept
                result["research"] = {"kept": False, "reason": f"{type(e).__name__}"}
        return 0
    except Exception as e:
        result["reason"] = f"{type(e).__name__}"
        return 0
    finally:
        try:
            Path(args.out).write_text(json.dumps(result), encoding="utf-8")
        except OSError:
            return 1


if __name__ == "__main__":
    sys.exit(main())
