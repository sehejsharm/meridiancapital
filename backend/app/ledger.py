"""Every real-money figure, reconciled to Angel One's own ledger.

A trade's P&L before charges comes from Angel's position book and is exact. Its
charges did not match: programs up to v7 booked them from Angel's brokerage
calculator, which disagrees with what Angel actually takes (Rs 80 against
Rs 73.68 on 12 Oct 2026), and v7's equity left charges out altogether. Angel's
truth is its balance. Two readings of it are on record for every session the
program ran:

* the opening balance, read from Angel when the session started (what the
  Angel app calls the "Opening Ledger Balance"); every equity sample carries it
  as ``equity - day_pl``;
* from v8, the trading balance itself, and for every closed trade the charges
  that balance actually lost (the program's money log).

The change from one session's opening balance to the next is, to the paisa,
what the account made or lost in between, after every charge on the contract
note. With one trade a day, that is the trade's net; its charges are its P&L
before charges minus that. A change that cannot be charges (a deposit or a
withdrawal between the two sessions) is never booked as charges: the trade
keeps its recorded figures and the gap is reported instead.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from app.programs import PROGRAM_DIR

LEDGER = "Angel ledger"          # next session's opening balance
BALANCE = "Angel balance"        # v8: the trading balance right after the trade
RECORDED = "recorded"            # neither available yet: the program's own figure
TWO = Decimal("0.01")


def _d(v) -> Decimal | None:
    if v is None:
        return None
    try:
        return Decimal(str(v)).quantize(TWO)
    except (ArithmeticError, ValueError):
        return None


def _f(v: Decimal | None) -> float | None:
    return None if v is None else float(v)


def plausible_charges(c: Decimal, turnover: float) -> bool:
    """Charges, not a deposit or a withdrawal: at least a rupee, at most 0.6% of
    turnover + Rs 150 (Angel charges about Rs 70 on a 1-lot round trip)."""
    return Decimal("1.00") <= c <= Decimal(str(0.006 * turnover + 150.0))


def openings(db, algo_id: str) -> dict[str, Decimal]:
    """Each session's opening balance as the program read it from Angel."""
    # One pass over the (algo_id, session_date, id) index for each session's
    # first sample. A subquery per row made this quadratic in the samples.
    with db.conn() as c:
        rows = c.execute(
            """SELECT e.session_date, e.equity, e.day_pl
               FROM equity_samples e
               JOIN (SELECT MIN(id) AS id FROM equity_samples
                     WHERE algo_id = ? GROUP BY session_date) first ON first.id = e.id
               ORDER BY e.session_date""",
            (algo_id,),
        ).fetchall()
    out = {}
    for r in rows:
        if r["equity"] is None or r["day_pl"] is None:
            continue
        out[r["session_date"]] = _d(Decimal(str(r["equity"])) - Decimal(str(r["day_pl"])))
    return out


def money_log(algo_id: str, base: Path | None = None) -> list[dict]:
    """The v8 program's own per-trade money records (live only)."""
    path = (base or PROGRAM_DIR) / algo_id / "gk50k_money.jsonl"
    out = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for line in lines:
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict) and rec.get("mode", "live") == "live":
            out.append(rec)
    return out


def reconcile(db, trades: list[dict], base: Path | None = None) -> list[dict]:
    """The trades with Angel's figures beside the recorded ones.

    Adds ``charges_angel``, ``net_angel`` and ``reconciled`` ("Angel ledger",
    "Angel balance" or "recorded"), and on a session where the balance moved by
    more than charges can explain, ``ledger_gap`` — the amount that moved
    outside trading. Paper trades are returned as they are.
    """
    out = [dict(t) for t in trades]
    live = [t for t in out if t.get("mode") == "live" and t.get("exit_ts")]
    by_algo: dict[str, list[dict]] = {}
    for t in live:
        by_algo.setdefault(t.get("algo_id") or "", []).append(t)
    for algo_id, rows in by_algo.items():
        opens = openings(db, algo_id)
        days = sorted(opens)
        logged = {(r.get("entry_ts") or "")[:16]: r for r in money_log(algo_id, base)}
        by_day: dict[str, list[dict]] = {}
        for t in rows:
            by_day.setdefault(t["session_date"], []).append(t)
        for day, ts in by_day.items():
            gross = sum((_d(t.get("gross")) or Decimal(0)) for t in ts)
            turnover = sum(float((t.get("entry_prem") or 0) + (t.get("exit_prem") or 0)) * float(t.get("qty") or 0)
                           for t in ts)
            nxt = next((d for d in days if d > day), None)
            if day in opens and nxt is not None:
                change = opens[nxt] - opens[day]
                charges = gross - change
                if plausible_charges(charges, turnover):
                    _split(ts, charges, LEDGER)
                    continue
                for t in ts:
                    t["ledger_gap"] = _f(change - (gross - sum((_d(x.get("charges")) or Decimal(0)) for x in ts)))
            for t in ts:
                rec = logged.get((t.get("entry_ts") or "")[:16])
                if rec and rec.get("charges_angel_balance") is not None:
                    c = _d(rec["charges_angel_balance"])
                    t["charges_angel"], t["net_angel"] = _f(c), _f((_d(t.get("gross")) or Decimal(0)) - c)
                    t["reconciled"] = BALANCE
                else:
                    t["charges_angel"], t["net_angel"], t["reconciled"] = t.get("charges"), t.get("net"), RECORDED
    for t in out:
        if t.get("mode") != "live" or not t.get("exit_ts"):
            t.setdefault("charges_angel", t.get("charges"))
            t.setdefault("net_angel", t.get("net"))
            t.setdefault("reconciled", RECORDED)
    return out


def _split(ts: list[dict], charges: Decimal, how: str) -> None:
    """One session's charges across its trades: all of it with one trade (always,
    for a one-trade-a-day strategy), else in proportion to the recorded charges."""
    if len(ts) == 1:
        shares = [charges]
    else:
        rec = [(_d(t.get("charges")) or Decimal(0)) for t in ts]
        total = sum(rec) or Decimal(len(ts))
        weights = rec if sum(rec) else [Decimal(1)] * len(ts)
        shares = [(charges * w / total).quantize(TWO) for w in weights]
        shares[-1] = charges - sum(shares[:-1])
    for t, c in zip(ts, shares):
        g = _d(t.get("gross")) or Decimal(0)
        t["charges_angel"], t["net_angel"], t["reconciled"] = _f(c), _f(g - c), how


def stats(trades: list[dict]) -> dict:
    """The blotter's headline figures over real-money trades, from Angel's numbers."""
    closed = [t for t in trades if t.get("mode") == "live" and t.get("exit_ts")]
    nets = [(_d(t.get("net_angel")) if t.get("net_angel") is not None else _d(t.get("net"))) for t in closed]
    nets = [n for n in nets if n is not None]
    gross = sum((_d(t.get("gross")) or Decimal(0)) for t in closed)
    charges = sum((_d(t.get("charges_angel")) or Decimal(0)) for t in closed)
    wins = sum(1 for n in nets if n > 0)
    holds = [float(t["hold_min"]) for t in closed if t.get("hold_min") is not None]
    return {
        "n": len(closed),
        "wins": wins,
        "losses": len(closed) - wins,
        "win_rate": (wins / len(closed)) if closed else 0.0,
        "net": _f(sum(nets, Decimal(0))),
        "gross": _f(gross),
        "charges": _f(charges),
        "best": _f(max(nets)) if nets else 0.0,
        "worst": _f(min(nets)) if nets else 0.0,
        "avg_hold": (sum(holds) / len(holds)) if holds else 0.0,
        "reconciled": sum(1 for t in closed if t.get("reconciled") in (LEDGER, BALANCE)),
        "scope": "real money",
    }


def live_algo(db) -> str | None:
    """The algorithm trading real money: the one with the latest real-money trade,
    else the one that wrote the latest equity sample."""
    with db.conn() as c:
        row = c.execute("SELECT algo_id FROM trades WHERE mode = 'live' ORDER BY id DESC LIMIT 1").fetchone()
        if row and row["algo_id"]:
            return row["algo_id"]
        row = c.execute("SELECT algo_id FROM equity_samples ORDER BY id DESC LIMIT 1").fetchone()
    return row["algo_id"] if row else None


def closing_balances(db, algo_id: str, live_balance: float | None = None,
                     today: str | None = None) -> list[dict]:
    """Each session's closing balance at Angel: the next session's opening
    balance; for the latest session, the live trading balance (v8) when known."""
    opens = openings(db, algo_id)
    days = sorted(opens)
    out = []
    for i, d in enumerate(days):
        if i + 1 < len(days):
            out.append({"session_date": d, "equity": _f(opens[days[i + 1]]), "source": LEDGER})
        elif live_balance is not None and (today is None or d == today):
            out.append({"session_date": d, "equity": _f(_d(live_balance)), "source": BALANCE})
        else:
            last = _last_sample(db, algo_id, d)
            if last is not None:
                out.append({"session_date": d, "equity": _f(last), "source": RECORDED})
    return out


def _last_sample(db, algo_id: str, day: str) -> Decimal | None:
    with db.conn() as c:
        row = c.execute(
            "SELECT equity FROM equity_samples WHERE algo_id = ? AND session_date = ? ORDER BY id DESC LIMIT 1",
            (algo_id, day),
        ).fetchone()
    return _d(row["equity"]) if row and row["equity"] is not None else None
