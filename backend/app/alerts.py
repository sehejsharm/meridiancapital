"""Alerting.

An algorithm that stops trading at 10:04 and is noticed at 15:30 has cost you
the day. The dashboard answers "what is happening" for whoever is looking at
it; this is for the hours nobody is.

Two deliberate constraints:

* **Delivery never blocks the caller.** Every send runs on a worker thread with
  a short timeout, and a dead webhook is logged and dropped. An alerting system
  that can stall the trading loop is worse than no alerting system.
* **A rule that has fired does not fire again until it clears.** The condition
  that matters — "drawdown past 8%" — is true on every tick once it is true, and
  a hundred identical emails is how people learn to filter the sender.
"""
from __future__ import annotations

import json
import logging
import smtplib
import threading
import urllib.error
import urllib.request
from datetime import datetime
from email.message import EmailMessage
from typing import Any, Callable, Optional

from . import db

log = logging.getLogger("meridian.alerts")

KV_CONFIG = "alert_config"
SEND_TIMEOUT_SECONDS = 8

# Each rule: what it watches, whether a threshold applies, and the plain
# sentence that goes out when it fires.
RULES: dict[str, dict] = {
    "kill_switch": {
        "label": "Kill switch triggered",
        "detail": "The daily loss limit was reached and trading stopped.",
        "threshold": None,
        "severity": "critical",
    },
    "error_rate": {
        "label": "Error rate above a threshold",
        "detail": "Distinct errors in a session exceeded the limit set here.",
        "threshold": {"label": "Distinct errors", "default": 5, "min": 1, "max": 500},
        "severity": "error",
    },
    "drawdown": {
        "label": "Drawdown beyond a percentage",
        "detail": "Equity fell this far below its peak.",
        "threshold": {"label": "Drawdown %", "default": 8, "min": 1, "max": 90},
        "severity": "error",
    },
    "bot_stopped": {
        "label": "Algorithm stopped unexpectedly",
        "detail": "A session ended without anyone asking it to.",
        "threshold": None,
        "severity": "critical",
    },
    "trade": {
        "label": "Every trade",
        "detail": "One message per position closed.",
        "threshold": None,
        "severity": "info",
    },
    "daily_summary": {
        "label": "Daily summary at the close",
        "detail": "P&L, trades and the day's context when the session ends.",
        "threshold": None,
        "severity": "info",
    },
}

CHANNELS = ("email", "webhook", "slack")

DEFAULT_CONFIG: dict[str, Any] = {
    "channels": {
        "email": {"enabled": False, "to": "", "from": "", "host": "", "port": 587,
                  "username": "", "password": ""},
        "webhook": {"enabled": False, "url": ""},
        "slack": {"enabled": False, "url": ""},
    },
    "rules": {
        key: {"enabled": key in ("kill_switch", "bot_stopped"),
              "channels": ["email"],
              "threshold": (spec["threshold"] or {}).get("default")}
        for key, spec in RULES.items()
    },
}


# ---------------------------------------------------------------- config


def _merge(base: dict, over: Any) -> dict:
    """Stored config is merged over the defaults rather than replacing them.

    A config written before a rule existed would otherwise leave that rule
    missing entirely, and every read of it would need a guard.
    """
    out = json.loads(json.dumps(base))
    if not isinstance(over, dict):
        return out
    for key, value in over.items():
        if key in out and isinstance(out[key], dict) and isinstance(value, dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def config() -> dict:
    return _merge(DEFAULT_CONFIG, db.kv_get(KV_CONFIG))


def public_config() -> dict:
    """Same thing with the SMTP password blanked — it is never sent back."""
    cfg = config()
    email = cfg["channels"]["email"]
    cfg["channels"]["email"] = {**email, "password": "",
                                "password_set": bool(email.get("password"))}
    return cfg


def save(update: dict) -> dict:
    """Merge an update in. An empty SMTP password means "leave it alone"."""
    current = config()
    merged = _merge(current, update)
    incoming = ((update.get("channels") or {}).get("email") or {})
    if not incoming.get("password"):
        merged["channels"]["email"]["password"] = current["channels"]["email"]["password"]
    # Unknown rules and channels are dropped rather than stored forever.
    merged["rules"] = {k: v for k, v in merged["rules"].items() if k in RULES}
    for rule in merged["rules"].values():
        rule["channels"] = [c for c in (rule.get("channels") or []) if c in CHANNELS]
    db.kv_set(KV_CONFIG, merged)
    return public_config()


# ---------------------------------------------------------------- delivery


def _post_json(url: str, payload: dict) -> None:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json",
                 "User-Agent": "MeridianCapital/1.0"},
        method="POST")
    with urllib.request.urlopen(req, timeout=SEND_TIMEOUT_SECONDS) as resp:
        resp.read(2048)


def _send_webhook(cfg: dict, subject: str, body: str, meta: dict) -> None:
    _post_json(cfg["url"], {
        "event": meta.get("rule"),
        "severity": meta.get("severity"),
        "subject": subject,
        "message": body,
        "at": datetime.now().isoformat(timespec="seconds"),
        "data": meta.get("data") or {},
    })


def _send_slack(cfg: dict, subject: str, body: str, meta: dict) -> None:
    # Slack's incoming-webhook shape, which is not the generic one above.
    _post_json(cfg["url"], {"text": f"*{subject}*\n{body}"})


def _send_email(cfg: dict, subject: str, body: str, meta: dict) -> None:
    msg = EmailMessage()
    msg["Subject"] = f"[Meridian] {subject}"
    msg["From"] = cfg.get("from") or cfg.get("username") or "meridian@localhost"
    msg["To"] = cfg["to"]
    msg.set_content(body)
    port = int(cfg.get("port") or 587)
    if port == 465:
        server: Any = smtplib.SMTP_SSL(cfg["host"], port, timeout=SEND_TIMEOUT_SECONDS)
    else:
        server = smtplib.SMTP(cfg["host"], port, timeout=SEND_TIMEOUT_SECONDS)
        server.starttls()
    try:
        if cfg.get("username"):
            server.login(cfg["username"], cfg.get("password") or "")
        server.send_message(msg)
    finally:
        try:
            server.quit()
        except Exception:
            pass


SENDERS: dict[str, Callable[[dict, str, str, dict], None]] = {
    "email": _send_email,
    "webhook": _send_webhook,
    "slack": _send_slack,
}


def deliver(channel: str, subject: str, body: str, meta: Optional[dict] = None) -> dict:
    """Send on one channel, synchronously. Used by the Test button."""
    cfg = config()["channels"].get(channel)
    if not cfg:
        return {"ok": False, "detail": f"No such channel: {channel}"}
    missing = _missing_settings(channel, cfg)
    if missing:
        return {"ok": False, "detail": f"{channel} is not configured: {missing}"}
    try:
        SENDERS[channel](cfg, subject, body, meta or {})
        return {"ok": True, "detail": f"Sent on {channel}."}
    except (urllib.error.URLError, OSError, smtplib.SMTPException) as exc:
        # The reason is what makes this button useful; a bare "failed" is not.
        return {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}


def _missing_settings(channel: str, cfg: dict) -> str:
    if channel == "email":
        gaps = [k for k in ("host", "to") if not cfg.get(k)]
        return ", ".join(gaps)
    return "" if cfg.get("url") else "url"


# ---------------------------------------------------------------- firing

# Rules that are currently in their fired state, so a condition that stays true
# does not re-send on every evaluation.
_active: set[str] = set()
_lock = threading.Lock()


def reset_state() -> None:
    """Called at the start of a session — a new day starts unfired."""
    with _lock:
        _active.clear()


def fire(rule: str, subject: str, body: str, data: Optional[dict] = None,
         repeatable: bool = False) -> None:
    """Raise an alert. Never raises, never blocks."""
    spec = RULES.get(rule)
    if not spec:
        return
    cfg = config()
    rule_cfg = cfg["rules"].get(rule) or {}
    if not rule_cfg.get("enabled"):
        return

    if not repeatable:
        with _lock:
            if rule in _active:
                return
            _active.add(rule)

    channels = [c for c in rule_cfg.get("channels", [])
                if (cfg["channels"].get(c) or {}).get("enabled")]
    if not channels:
        return

    meta = {"rule": rule, "severity": spec["severity"], "data": data or {}}

    def _run() -> None:
        for channel in channels:
            result = deliver(channel, subject, body, meta)
            if not result["ok"]:
                log.warning("alert %s on %s failed: %s", rule, channel, result["detail"])
        try:
            from . import approvals
            approvals.record("system", "alert_sent", f"{spec['label']}: {subject}",
                             rule=rule, channels=channels)
        except Exception:
            pass

    threading.Thread(target=_run, name=f"alert-{rule}", daemon=True).start()


def clear(rule: str) -> None:
    """The condition stopped being true, so the rule may fire again."""
    with _lock:
        _active.discard(rule)
