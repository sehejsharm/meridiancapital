"""A program's closed session reaches the deck and the journal (6 Nov 2026).

That day a trade closed at 13:16 (net +₹3,847.64, matched to Angel's balance)
and at 15:25 the program printed its end-of-day block: End equity Rs 45,875,
Net P&L Rs 3,847.640. The deck nonetheless read "Equity ₹0.00 · Day's trading
P&L ₹0.00 · opening ₹0.00 · ₹0.00 trading", and the journal's "Last end-of-day
report" said "No session has been closed out yet" — a few lines below the block.

Two causes, one per surface, both from treating only the built-in engine's
structured output as the record of a session:

* The program's last status on its way out (``state: STOPPED``) carries no
  figures. The follower published it like any other, and the missing equity
  became Rs 0.00 over the last real figures.
* Only the built-in writes an end-of-day record. A program prints one, and
  nothing turned the printed block into the record the journal reads.
"""

from __future__ import annotations

from app import session_close
from app.program_output import ProgramOutput
from app.program_status import has_figures, program_snapshot
from shared.db import snapshot_key

DAY = "2026-11-06"

# The block exactly as the follower files it (colour codes and padding removed).
EOD_LINES = [
    "END OF DAY  ·  गणेश कवच  ·  GANESH KAVACH 50K  ·  Config #5 v4  ·  2026-11-06",
    "Trades                1   1 win / 0 loss",
    "Win rate              100.0%",
    "P&L (Angel)           Rs 3,962.250",
    "Charges (Angel)       Rs 114.610",
    "Net P&L               Rs 3,847.640",
    "Best / worst trade    Rs 3,848  /  Rs 3,848",
    "Avg hold              62 min",
    "Start equity          Rs 42,027",
    "End equity            Rs 45,875",
    "Drawdown from peak    -34.3%",
    "────────────────────────────────────────────────────────────",
    "#  RIGHT  STRIKE   LOTS  ENTRY    EXIT     NET           WHY",
    "1  CE     24500    1     150.00   210.96   Rs 3,847.640  TRAIL",
]

# The last periodic status of the day: flat since 13:16, Angel's figures.
LAST_FLAT = {
    "ts": f"{DAY}T15:24:58", "pid": 4242, "state": "RUNNING", "phase": "MARKET CLOSED",
    "equity": 45874.92, "day_pnl": 3847.64, "peak_equity": 69810.0, "realised_today": 3962.25,
    "realised_week": 3847.64, "position": None,
    "money": {"balance": 45874.92, "opening_balance": 42027.28, "added": 0.0,
              "day_gross": 3962.25, "realised_gross": 3962.25, "charges": 114.61,
              "charges_estimated": False, "equity": 45874.92, "day_net": 3847.64},
}
# What it writes on the way out: write_status(state="STOPPED", reason=...).
STOPPED = {"ts": f"{DAY}T15:25:10", "pid": 4242, "version": "config5-v4-50k",
           "state": "STOPPED", "reason": "keyboard interrupt", "open_position": None}

TRADE = {
    "entry_ts": f"{DAY} 12:14", "exit_ts": f"{DAY} 13:16", "session_date": DAY, "mode": "live",
    "side": "CE", "strike": 24500, "lots": 1, "qty": 65, "entry_prem": 150.0, "exit_prem": 210.96,
    "gross": 3962.25, "charges": 114.61, "net": 3847.64, "reason": "TRAIL", "hold_min": 62,
    "equity": 45874.92, "algo_id": "og",
}


def file_block(db, lines=EOD_LINES):
    for line in lines:
        db.add_event("info", line, source="program", algo_id="og")


class OneSup:
    """The bits of a supervisor the follower's status path reads."""

    def __init__(self, folder, pid=4242):
        from types import SimpleNamespace

        self.program_path = folder / "program.py"
        self.state = SimpleNamespace(running=True, pid=pid, mode="live", started_ts=None, adopted=False)


# ── the printed block ────────────────────────────────────────────────────────

def test_the_printed_block_reads_back_into_figures():
    got = session_close.parse_eod_block(EOD_LINES[1:])
    assert got["complete"] is True
    assert (got["trades"], got["wins"], got["losses"]) == (1, 1, 0)
    assert got["win_rate"] == 1.0
    assert (got["gross"], got["charges"], got["net"]) == (3962.25, 114.61, 3847.64)
    assert (got["best"], got["worst"]) == (3848.0, 3848.0)
    assert (got["start_equity"], got["end_equity"]) == (42027.0, 45875.0)
    assert got["drawdown_pct"] == -0.343
    assert got["avg_hold_min"] == 62.0


def test_both_ways_the_program_prints_a_loss_read_as_negative():
    got = session_close.parse_eod_block([
        "Net P&L               Rs -5,931.250",
        "Best / worst trade    -Rs 5,931  /  -Rs 5,931",
        "Drawdown from peak    +0.0%",
    ])
    assert got["net"] == -5931.25
    assert (got["best"], got["worst"]) == (-5931.0, -5931.0)
    assert got["drawdown_pct"] == 0.0


def test_a_day_with_no_trade_is_still_a_closed_session():
    got = session_close.parse_eod_block([
        "Trades today          0 — no signal qualified in the entry window",
        "Start equity          Rs 42,027",
        "End equity            Rs 42,027",
        "Day P&L               Rs 0",
    ])
    assert got["trades"] == 0 and got["complete"] is True
    assert got["day_pl"] == 0.0 and got["end_equity"] == 42027.0


# ── the journal's card ───────────────────────────────────────────────────────

def test_the_block_becomes_the_journals_end_of_day_record(tmp_db):
    tmp_db.add_trade(TRADE)
    tmp_db.kv_set(snapshot_key("og"), program_snapshot(LAST_FLAT, name="og", mode="live", pid=4242))
    file_block(tmp_db)

    report = session_close.refresh(tmp_db, "og")
    assert report is not None
    stored = tmp_db.kv_get("report:eod:latest:og", None)
    assert stored == tmp_db.kv_get("report:eod:latest", None) == tmp_db.kv_get(f"report:eod:{DAY}:og", None)
    assert stored["session_date"] == DAY and stored["mode"] == "live"
    assert (stored["trades"], stored["wins"], stored["losses"]) == (1, 1, 0)
    # To the paisa from the trades table, not the block's rupee-rounded figures.
    assert (stored["gross"], stored["charges"], stored["net"]) == (3962.25, 114.61, 3847.64)
    assert stored["end_equity"] == 45874.92
    assert stored["start_equity"] == 42027.28
    assert stored["day_pl"] == 3847.64
    assert stored["drawdown_pct"] == -0.343


def test_without_the_trades_table_the_block_alone_is_enough(tmp_db):
    file_block(tmp_db)
    report = session_close.refresh(tmp_db, "og")
    assert (report["net"], report["end_equity"]) == (3847.64, 45875.0)
    assert report["start_equity"] == 42027.36, "the close less the net, which agrees with the printed Rs 42,027"
    assert report["trades"] == 1


def test_filing_is_once_per_block(tmp_db):
    file_block(tmp_db)
    assert session_close.refresh(tmp_db, "og") is not None
    assert session_close.refresh(tmp_db, "og") is None, "nothing new: no rewrite"


def test_an_older_block_never_replaces_a_newer_record(tmp_db):
    tmp_db.kv_set("report:eod:latest:og", {"session_date": "2026-11-09", "header_event_id": -1})
    file_block(tmp_db)
    assert session_close.refresh(tmp_db, "og") is None
    assert tmp_db.kv_get("report:eod:latest:og", None)["session_date"] == "2026-11-09"


def test_the_follower_files_the_block_once_it_is_all_read(tmp_db, tmp_path):
    """The follower spots the header as it files the lines and files the report
    on the next quiet poll, when the whole block is in the journal."""
    follower = ProgramOutput(tmp_db, fleet=None)
    tmp_db.add_trade(TRADE)
    file_block(tmp_db)
    follower._eod_pending.add("og")
    assert follower.close_session("og") is True
    assert tmp_db.kv_get("report:eod:latest:og", None)["net"] == 3847.64
    assert "og" not in follower._eod_pending


# ── the deck's figures ───────────────────────────────────────────────────────

def test_a_stopped_status_carries_no_figures():
    assert has_figures(LAST_FLAT) is True
    assert has_figures(STOPPED) is False
    assert has_figures({"money": {"balance": 100.0}}) is True


def test_the_last_words_never_overwrite_the_sessions_figures(tmp_db, tmp_path):
    import json

    (tmp_path / "program.py").write_text("# stub\n")
    sup = OneSup(tmp_path)
    follower = ProgramOutput(tmp_db, fleet=None)

    (tmp_path / "gk50k_status.json").write_text(json.dumps(LAST_FLAT))
    assert follower.publish_status("og", sup) is True
    assert tmp_db.kv_get(snapshot_key("og"), None)["account"]["equity"] == 45874.92

    (tmp_path / "gk50k_status.json").write_text(json.dumps(STOPPED))
    assert follower.publish_status("og", sup) is False, "the stub is not published"
    acct = tmp_db.kv_get(snapshot_key("og"), None)["account"]
    assert acct["equity"] == 45874.92 and acct["day_pl"] == 3847.64
    assert acct["money"]["balance"] == 45874.92 and acct["money"]["opening_balance"] == 42027.28


def test_a_snapshot_already_zeroed_is_mended_from_the_closed_session(tmp_db):
    """What 6 Nov left in the database: the stub's Rs 0.00 snapshot."""
    tmp_db.kv_set(snapshot_key("og"), program_snapshot(STOPPED, name="og", mode="live", pid=4242))
    zeroed = tmp_db.kv_get(snapshot_key("og"), None)["account"]
    assert zeroed["equity"] == 0.0 and zeroed["day_pl"] == 0.0, "the bug, reproduced"

    tmp_db.add_trade(TRADE)
    file_block(tmp_db)
    session_close.refresh(tmp_db, "og")
    acct = tmp_db.kv_get(snapshot_key("og"), None)["account"]
    assert acct["equity"] == 45874.92
    # The printed opening is to the rupee; the one the paisa figures imply is used.
    assert acct["day_pl"] == 3847.64
    assert acct["start_equity"] == 42027.28


def test_a_good_snapshot_is_left_alone(tmp_db):
    tmp_db.kv_set(snapshot_key("og"), program_snapshot(LAST_FLAT, name="og", mode="live", pid=4242))
    tmp_db.add_trade(TRADE)
    file_block(tmp_db)
    session_close.refresh(tmp_db, "og")
    acct = tmp_db.kv_get(snapshot_key("og"), None)["account"]
    assert acct["money"]["balance"] == 45874.92, "Angel's figures stay as they were"
