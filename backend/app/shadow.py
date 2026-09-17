"""Shadow mode: what the live account actually kept, against what paper says.

A shadow is a second registration of the same strategy version, forced to paper,
running beside the live one. Both see the same market and take the same
decisions; only one sends orders. The gap between them is execution drag —
slippage on entry and exit, brokerage, STT, exchange and SEBI fees, stamp duty
and GST — which is the number a backtest cannot tell you and the one that
decides whether an edge survives contact with a real broker.

Pairing on session date rather than on order id is deliberate: the two engines
size independently and may not take the identical number of lots, so the honest
comparison is per-session return, not trade-for-trade.
"""

from __future__ import annotations


def _closed(trades: list[dict]) -> list[dict]:
    return [t for t in trades if t.get("exit_ts")]


def _by_session(trades: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for t in _closed(trades):
        out.setdefault(t.get("session_date") or "", []).append(t)
    return out


def _summarise(trades: list[dict]) -> dict:
    nets = [float(t.get("net") or 0.0) for t in trades]
    gross = [float(t.get("gross") or 0.0) for t in trades]
    charges = [float(t.get("charges") or 0.0) for t in trades]
    return {
        "trades": len(trades),
        "gross": round(sum(gross), 2),
        "charges": round(sum(charges), 2),
        "net": round(sum(nets), 2),
        "wins": sum(1 for n in nets if n > 0),
    }


def compare(db, live_algo_id: str, shadow_algo_id: str, limit: int = 2000) -> dict:
    """Session-by-session drag between a live algorithm and its paper shadow."""
    live_trades = db.trades(limit=limit, algo_id=live_algo_id)
    shadow_trades = db.trades(limit=limit, algo_id=shadow_algo_id)

    live_by_day = _by_session(live_trades)
    shadow_by_day = _by_session(shadow_trades)

    rows = []
    for session in sorted(set(live_by_day) | set(shadow_by_day), reverse=True):
        live = _summarise(live_by_day.get(session, []))
        paper = _summarise(shadow_by_day.get(session, []))
        drag = round(paper["net"] - live["net"], 2)
        rows.append(
            {
                "session_date": session,
                "live": live,
                "paper": paper,
                # Positive drag = the live account kept less than paper said it
                # would. That is the normal direction; negative means live did
                # better, usually favourable slippage.
                "drag": drag,
                "drag_pct_of_paper": (
                    round(100.0 * drag / abs(paper["net"]), 2) if paper["net"] else None
                ),
                "both_traded": bool(live["trades"] and paper["trades"]),
            }
        )

    paired = [r for r in rows if r["both_traded"]]
    total_live = round(sum(r["live"]["net"] for r in paired), 2)
    total_paper = round(sum(r["paper"]["net"] for r in paired), 2)
    total_drag = round(total_paper - total_live, 2)
    charges = round(sum(r["live"]["charges"] for r in paired), 2)

    return {
        "live_algo_id": live_algo_id,
        "shadow_algo_id": shadow_algo_id,
        "sessions": rows,
        "paired_sessions": len(paired),
        "totals": {
            "live_net": total_live,
            "paper_net": total_paper,
            "drag": total_drag,
            "drag_pct_of_paper": (
                round(100.0 * total_drag / abs(total_paper), 2) if total_paper else None
            ),
            "charges_paid": charges,
            # Whatever the drag is not explained by real charges is slippage:
            # the difference between the price the strategy wanted and the price
            # the exchange gave it.
            "slippage_est": round(total_drag - charges, 2),
            "avg_drag_per_session": (
                round(total_drag / len(paired), 2) if paired else None
            ),
        },
    }
