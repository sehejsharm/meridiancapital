"""After the close: is the account flat, and do Angel's fills match what the desk recorded?

Until now the only reconciliation was the next morning's opening balance. A
position left open at 15:30, or a fill the program never recorded, was found
the next day at the earliest. This runs once, ten minutes after the last
algorithm has been stopped for the day, and answers two questions.

From the desk's own records, always:

* every real-money trade recorded today has an exit;
* no program's last status still shows a position;
* the journal has no "exit could not be placed" line.

From Angel itself, when the server has the Angel credentials in its settings
and no program is running (Angel keeps one session per key, so a login beside a
running program would log that program out):

* Angel's position book is flat;
* each recorded trade's contract, quantity and prices agree with Angel's trade
  book, and Angel has no fills the desk does not know about.

Read-only: it never places, changes or cancels an order. The Angel part runs in
a process of its own (``app.angel_after_close``), so its memory is given back
and a failure there cannot touch the API.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from app.notify import RULES
from engine.clock import now_ist
from shared.db import snapshot_key

RUN_AFTER = (15, 35)
K_LATEST = "closecheck:latest"
PRICE_TOLERANCE = 0.051      # one tick, with room for rounding
ANGEL_TIMEOUT_SEC = 240
_EXIT_FAILED = next(rx for kind, _lvl, rx in RULES if kind == "exit_failed")


def key(day: str) -> str:
    return f"closecheck:{day}"


def angel_configured() -> bool:
    """The four Angel settings are present in the server's environment (values never read here)."""
    return all(os.environ.get(k, "").strip() for k in
               ("ANGEL_API_KEY", "ANGEL_CLIENT_ID", "ANGEL_PASSWORD", "ANGEL_TOTP_SECRET"))


def angel_enabled() -> bool:
    return os.environ.get("MERIDIAN_AFTER_CLOSE_ANGEL", "1").strip().lower() not in ("0", "false", "no", "off")


def _sym(s) -> str:
    return str(s or "").strip().upper()


def recorded(db, day: str) -> dict:
    """What the desk itself has on record for the day, and anything wrong with it."""
    with db.conn() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT * FROM trades WHERE session_date = ? AND mode = 'live' ORDER BY entry_ts", (day,))]
    findings = []
    for t in rows:
        if not t.get("exit_ts"):
            findings.append({"level": "critical", "kind": "open_trade",
                             "text": f"{t.get('tsym') or 'a trade'} entered at {str(t.get('entry_ts'))[11:16]} "
                                     f"has no exit on record"})
    for algo in db.algos():
        snap = db.kv_get(snapshot_key(algo["id"]), None)
        if not isinstance(snap, dict) or str(snap.get("ts") or "")[:10] != day:
            continue
        pos = snap.get("position")
        mode = (snap.get("engine") or {}).get("mode")
        if isinstance(pos, dict) and mode == "live":
            findings.append({"level": "critical", "kind": "status_position",
                             "text": f"{algo.get('name') or algo['id']}'s last status still shows "
                                     f"{pos.get('tsym') or 'a position'} held ({pos.get('qty')} qty)"})
    for e in db.events(limit=3000, day=day):
        if e.get("source") == "program" and _EXIT_FAILED.search(e.get("message") or ""):
            findings.append({"level": "critical", "kind": "exit_failed",
                             "text": f"{str(e.get('ts'))[11:19]} {e['message'][:160]}"})
            break
    return {"trades": rows, "findings": findings}


def _book(fills: list[dict]) -> dict[str, dict]:
    """Angel's fills per contract: quantity and average price bought and sold."""
    out: dict[str, dict] = {}
    for f in fills:
        b = out.setdefault(_sym(f.get("tsym")), {"buy_qty": 0, "sell_qty": 0, "buy_value": 0.0, "sell_value": 0.0,
                                                 "order_ids": []})
        qty, price = int(f.get("qty") or 0), float(f.get("price") or 0.0)
        side = "buy" if str(f.get("side") or "").upper().startswith("B") else "sell"
        b[f"{side}_qty"] += qty
        b[f"{side}_value"] += qty * price
        if f.get("order_id") and f["order_id"] not in b["order_ids"]:
            b["order_ids"].append(f["order_id"])
    for b in out.values():
        b["buy_avg"] = round(b["buy_value"] / b["buy_qty"], 2) if b["buy_qty"] else None
        b["sell_avg"] = round(b["sell_value"] / b["sell_qty"], 2) if b["sell_qty"] else None
    return out


def compare(trades: list[dict], fills: list[dict], positions: list[dict]) -> list[dict]:
    """Angel's position book and trade book against the day's recorded real-money trades."""
    findings = []
    ours: dict[str, dict] = {}
    for t in trades:
        o = ours.setdefault(_sym(t.get("tsym")), {"qty_in": 0, "qty_out": 0, "value_in": 0.0, "value_out": 0.0})
        qty = int(t.get("qty") or 0)
        o["qty_in"] += qty
        o["value_in"] += qty * float(t.get("entry_prem") or 0.0)
        if t.get("exit_ts"):
            o["qty_out"] += qty
            o["value_out"] += qty * float(t.get("exit_prem") or 0.0)

    for p in positions:
        net = int(p.get("net_qty") or 0)
        if net:
            known = _sym(p.get("tsym")) in ours
            findings.append({
                "level": "critical" if known else "warn", "kind": "position_open",
                "text": f"Angel shows {p.get('tsym')} still open: {net:+d} qty"
                        + ("" if known else " — not a trade the desk recorded (placed by hand?)")})

    book = _book(fills)
    for sym, o in ours.items():
        b = book.get(sym)
        if b is None:
            findings.append({"level": "critical", "kind": "no_fill",
                             "text": f"the desk recorded a trade in {sym} but Angel's trade book has no fill for it"})
            continue
        if b["buy_qty"] != o["qty_in"] or b["sell_qty"] != o["qty_out"]:
            findings.append({"level": "critical", "kind": "quantity",
                             "text": f"{sym}: recorded {o['qty_in']} bought / {o['qty_out']} sold, "
                                     f"Angel has {b['buy_qty']} bought / {b['sell_qty']} sold"})
            continue
        for label, qty, value, angel in (("entry", o["qty_in"], o["value_in"], b["buy_avg"]),
                                         ("exit", o["qty_out"], o["value_out"], b["sell_avg"])):
            if not qty or angel is None:
                continue
            mine = value / qty
            if abs(mine - angel) > PRICE_TOLERANCE:
                findings.append({"level": "warn", "kind": "price",
                                 "text": f"{sym}: recorded {label} {mine:.2f}, Angel's average fill {angel:.2f} "
                                         f"({(angel - mine) * qty:+,.2f} on {qty} qty)"})
    for sym, b in book.items():
        if sym not in ours:
            findings.append({"level": "warn", "kind": "unrecorded_fill",
                             "text": f"Angel has fills in {sym} ({b['buy_qty']} bought, {b['sell_qty']} sold) "
                                     f"that the desk has no trade for (placed by hand?)"})
    return findings


def ask_angel(db, day: str) -> dict:
    """Run the read-only Angel process and return what it wrote. Never raises."""
    data_dir = Path(db.path).parent
    work = data_dir / "research"
    try:
        work.mkdir(parents=True, exist_ok=True)
        fd, out = tempfile.mkstemp(prefix="after-close-", suffix=".json", dir=str(work))
        os.close(fd)
    except OSError as e:
        return {"ok": False, "reason": f"cannot write to {work}: {type(e).__name__}"}
    backend = Path(__file__).resolve().parent.parent
    env = os.environ.copy()
    env["PYTHONPATH"] = str(backend) + os.pathsep + env.get("PYTHONPATH", "")
    try:
        proc = subprocess.run(  # noqa: S603 - our own module, fixed arguments
            [sys.executable, "-m", "app.angel_after_close", "--day", day, "--out", out, "--dir", str(work)],
            cwd=str(work), env=env, capture_output=True, timeout=ANGEL_TIMEOUT_SEC, check=False,
        )
        try:
            result = json.loads(Path(out).read_text(encoding="utf-8") or "{}")
        except (OSError, ValueError):
            result = {}
        if not result:
            result = {"ok": False, "reason": f"the Angel process exited {proc.returncode} without a result"}
        return result
    except subprocess.TimeoutExpired:
        return {"ok": False, "reason": f"Angel did not answer within {ANGEL_TIMEOUT_SEC} seconds"}
    except OSError as e:
        return {"ok": False, "reason": f"could not start the Angel process: {type(e).__name__}"}
    finally:
        try:
            os.unlink(out)
        except OSError:
            pass


def run(db, day: str, *, running: bool = False, angel=ask_angel) -> dict:
    """The after-close check for one day. Stores and returns the result."""
    rec = recorded(db, day)
    findings = list(rec["findings"])
    result: dict = {"day": day, "ran_at": now_ist().isoformat(timespec="seconds"),
                    "trades": len(rec["trades"]), "angel": None, "fills": [], "research": None}
    if not angel_enabled():
        result["angel"] = "off (MERIDIAN_AFTER_CLOSE_ANGEL=0)"
    elif not angel_configured():
        result["angel"] = "not asked: the Angel credentials are not in the server's settings"
    elif running:
        result["angel"] = "not asked: a program is still running, and a second login would log it out"
    else:
        got = angel(db, day)
        if got.get("ok"):
            result["angel"] = "asked"
            result["fills"] = got.get("fills") or []
            result["research"] = got.get("research")
            findings += compare(rec["trades"], result["fills"], got.get("positions") or [])
        else:
            result["angel"] = f"could not be asked: {got.get('reason') or 'no reason given'}"
            findings.append({"level": "warn", "kind": "angel_unread",
                             "text": f"Angel's books were not read ({got.get('reason') or 'unknown'}); "
                                     f"only the desk's own records were checked"})
    result["findings"] = findings
    worst = "ok"
    if any(f["level"] == "critical" for f in findings):
        worst = "problem"
    elif findings:
        worst = "check"
    result["status"] = worst
    result["independent"] = result["angel"] == "asked"
    db.kv_set(key(day), result)
    db.kv_set(K_LATEST, result)
    if result["fills"]:
        # Angel's order ids for the day's fills: the trade page shows them.
        db.kv_set(f"angel:fills:{day}", result["fills"])
    return result


def headline(result: dict) -> str:
    """One line for the journal and the alert."""
    n = result.get("trades") or 0
    trades = f"{n} real-money trade{'s' if n != 1 else ''}"
    if result["status"] == "ok":
        if result.get("independent"):
            return f"after-close check: flat at Angel, {trades} on record agree with Angel's trade book"
        return f"after-close check: the desk's records are flat with {trades}; Angel's books {result['angel']}"
    worst = [f for f in result["findings"] if f["level"] == "critical"] or result["findings"]
    more = len(result["findings"]) - 1
    return f"after-close check: {worst[0]['text']}" + (f" (+{more} more)" if more > 0 else "")
