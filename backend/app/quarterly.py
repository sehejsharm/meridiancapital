"""The quarterly study: one page on how live trading compares with the plan.

Read-only. It gathers what the desk already records and prints it as plain text,
so the same questions are asked the same way every quarter:

1. What did the real-money trades make, and are wins still smaller than losses?
2. On the trades with a recorded price path, which exit rule set did best?
3. What did the fills cost against the price last seen and the minute's close?
4. Are the results inside the range the backtest allows for?

It changes nothing and reaches no program. Run it on the server:

    sudo -u meridian /opt/meridian/backend/.venv/bin/python backend/scripts/quarterly_study.py
"""

from __future__ import annotations

import math
from datetime import date, timedelta

from app import ledger, research

K_EXPECTATION = "research:expectation"


def _rs(v: float | None) -> str:
    return "n/a" if v is None else f"{'-' if v < 0 else '+'}Rs {abs(v):,.0f}"


def period_trades(db, date_from: str, date_to: str, mode: str = "live", algo: str | None = None) -> list[dict]:
    algo_id = algo or ledger.live_algo(db)
    rows = db.trades(limit=5000, algo_id=algo_id) if algo_id else []
    rows = [t for t in rows if t.get("exit_ts") and t.get("mode") == mode
            and date_from <= str(t.get("session_date") or "") <= date_to]
    rows.sort(key=lambda t: str(t.get("entry_ts") or ""))
    return ledger.reconcile(db, rows)


def _net(t: dict) -> float | None:
    v = t.get("net_angel") if t.get("net_angel") is not None else t.get("net")
    return None if v is None else float(v)


def band(db, trades: list[dict]) -> dict | None:
    """Where the running total sits against the backtest's two-sigma band (the Analytics card's sums)."""
    exp = db.kv_get(K_EXPECTATION, None)
    if not exp:
        return None
    rows = [t for t in trades if str(t.get("session_date") or "") >= exp["since"] and _net(t) is not None]
    actual = sum(_net(t) for t in rows)
    expected = sum(max(1, int(t.get("lots") or 1)) * exp["mean"] for t in rows)
    two = 2 * math.sqrt(sum((max(1, int(t.get("lots") or 1)) * exp["sd"]) ** 2 for t in rows))
    verdict = ("too few trades to judge" if len(rows) < 10 else "BELOW the band" if actual < expected - two
               else "above the band" if actual > expected + two else "inside the band")
    return {"trades": len(rows), "actual": actual, "expected": expected, "low": expected - two, "high": expected + two,
            "verdict": verdict, "label": exp.get("label") or "backtest", "since": exp["since"]}


def study_text(db, date_from: str, date_to: str, mode: str = "live", algo: str | None = None) -> str:
    trades = period_trades(db, date_from, date_to, mode, algo)
    nets = [n for n in (_net(t) for t in trades) if n is not None]
    wins, losses = [n for n in nets if n > 0], [n for n in nets if n <= 0]
    out = [f"QUARTERLY STUDY  {date_from} to {date_to}  ({'real money' if mode == 'live' else mode})", ""]

    out.append("1. Results")
    if not nets:
        out.append("   No closed trade in this period.")
    else:
        aw = sum(wins) / len(wins) if wins else 0.0
        al = sum(losses) / len(losses) if losses else 0.0
        out += [f"   Trades {len(nets)}, won {len(wins)} ({100 * len(wins) / len(nets):.0f}%), net {_rs(sum(nets))}",
                f"   Average win {_rs(aw)}, average loss {_rs(al)}"
                + (f", win/loss ratio {aw / -al:.2f}" if wins and losses and al else ""),
                f"   Best {_rs(max(nets))}, worst {_rs(min(nets))}"]

    rules = research.rules_report(db, trades)
    out += ["", "2. Exit rule sets on the trades with a recorded price path"]
    if not rules["comparable_trades"]:
        out.append("   No trade in this period has a whole price path on record.")
    else:
        out.append(f"   {rules['comparable_trades']} comparable trade(s) of {rules['trades']}; as traded {_rs(rules['actual_net'])}")
        for name in rules["rules"]:
            t = rules["totals"][name]
            out.append(f"   {name:<42} {_rs(t['net']):>14}   won {t['wins']}")
        out.append("   " + ("100 or more trades: enough for a verdict." if rules["enough"]
                            else "Fewer than 100 trades: a direction, not a verdict."))

    ex = research.execution_report(db, trades)
    out += ["", "3. Fill quality (negative = the fill was worse)"]
    for label, key in (("Exit against the price last seen", "exit_vs_last_seen"),
                       ("Exit against that minute's close", "exit_vs_minute_close"),
                       ("Entry against that minute's close", "entry_vs_minute_close")):
        e = ex[key]
        out.append(f"   {label:<36} " + ("not measured" if not e["trades"] else
                                         f"{e['trades']} trade(s), average {_rs(e['average_rs'])}, total {_rs(e['total_rs'])}"))
    for r in ex["by_reason"]:
        out.append(f"     exit {r['reason']:<12} {r['trades']} trade(s), average {_rs(r['average_rs'])}")

    b = band(db, trades)
    out += ["", "4. Against the backtest"]
    if not b:
        out.append("   No backtest figures are on record (enter them on Analytics, 'Live against the backtest').")
    else:
        out += [f"   {b['label']}, counted from {b['since']}: {b['trades']} trade(s)",
                f"   Live {_rs(b['actual'])}; the backtest expects {_rs(b['expected'])}; luck allows {_rs(b['low'])} to {_rs(b['high'])}",
                f"   Verdict: {b['verdict']}"]
    out += ["", "Nothing was changed. A change to the program still needs a backtest and the owner's go-ahead."]
    return "\n".join(out) + "\n"


def last_quarter(today: date) -> tuple[str, str]:
    """The last complete calendar quarter before ``today``."""
    first_this = date(today.year, 3 * ((today.month - 1) // 3) + 1, 1)
    end = first_this - timedelta(days=1)
    start = date(end.year, 3 * ((end.month - 1) // 3) + 1, 1)
    return start.isoformat(), end.isoformat()
