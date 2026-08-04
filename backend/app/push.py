"""Expo push notifications.

Entries, exits, the daily kill switch and the EOD report are worth a buzz on
the phone. Everything else stays in the app.
"""
from __future__ import annotations

import logging
import threading
from typing import Any

import httpx

from . import db
from .config import settings

log = logging.getLogger("meridian.push")

EXPO_ENDPOINT = "https://exp.host/--/api/v2/push/send"


def _tokens() -> list[str]:
    tokens = set(db.list_push_tokens())
    if settings.expo_push_token:
        tokens.add(settings.expo_push_token)
    return [t for t in tokens if t.startswith("ExponentPushToken") or t.startswith("ExpoPushToken")]


def send(title: str, body: str, data: dict[str, Any] | None = None) -> None:
    tokens = _tokens()
    if not tokens:
        return
    messages = [
        {"to": t, "title": title, "body": body, "sound": "default",
         "priority": "high", "data": data or {}}
        for t in tokens
    ]
    try:
        with httpx.Client(timeout=10) as client:
            resp = client.post(EXPO_ENDPOINT, json=messages,
                               headers={"Accept": "application/json",
                                        "Content-Type": "application/json"})
        payload = resp.json()
        # Expo reports dead tokens per-message; prune them so we stop retrying.
        for token, item in zip(tokens, payload.get("data", []) or []):
            if isinstance(item, dict) and item.get("status") == "error":
                if (item.get("details") or {}).get("error") == "DeviceNotRegistered":
                    db.remove_push_token(token)
    except Exception as exc:
        log.warning("push failed: %s", exc)


def send_async(title: str, body: str, data: dict[str, Any] | None = None) -> None:
    threading.Thread(target=send, args=(title, body, data), daemon=True).start()


def _rupees(v: Any) -> str:
    try:
        return f"₹{float(v):,.0f}"
    except (TypeError, ValueError):
        return "₹—"


def notify_event(kind: str, payload: dict, event: dict) -> None:
    """Called by the supervisor for every structured event."""
    if kind == "entry" and settings.push_on_entry:
        side = "CE" if payload.get("opt_type") == "C" else "PE"
        send_async(
            f"Entry — {payload.get('strike')}{side}",
            f"Filled {_rupees(payload.get('fill'))} × {payload.get('qty')} · "
            f"SL {_rupees(payload.get('sl_price'))} · risk {_rupees(payload.get('risk_rs'))}",
            {"screen": "dashboard", "kind": kind},
        )
    elif kind == "exit" and settings.push_on_exit:
        net = payload.get("net") or 0
        sign = "＋" if net >= 0 else "－"
        send_async(
            f"Exit {sign}{_rupees(abs(net))} — {payload.get('reason')}",
            f"{payload.get('symbol')} · {payload.get('move_pct')}% · "
            f"equity {_rupees(payload.get('equity'))}",
            {"screen": "trades", "kind": kind},
        )
    elif kind == "daily_kill" and settings.push_on_kill:
        send_async(
            "Daily kill switch hit",
            f"Day P&L {_rupees(payload.get('day_pnl'))} — trading stopped for today",
            {"screen": "dashboard", "kind": kind},
        )
    elif kind == "eod" and settings.push_on_eod:
        send_async(
            f"Session closed — {_rupees(payload.get('day_pnl'))}",
            f"{payload.get('trades', 0)} trades · equity {_rupees(payload.get('equity'))}",
            {"screen": "reports", "kind": kind},
        )
    elif kind == "fatal":
        send_async("Bot stopped with an error", event.get("message", "")[:150],
                   {"screen": "dashboard", "kind": kind})
