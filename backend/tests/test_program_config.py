"""The Strategy page and the live program agree, or the desk says where not.

6 Nov 2026: the Strategy page ("every value here was fixed by the backtest")
showed break-even 25%, trail start 35% and a 90-point take-profit. The live
program logged break-even at +20%, a trail riding 20% behind the peak and no
take-profit. Every other value matched.

The page renders the built-in engine's parameters (Config #5 v3); the program
trading real money is an uploaded file with its own constants (Config #5 v4)
that never reads them. The engine was right and the page described a different
engine without saying so. The desk now reads what the program logged at startup
and what its code says, and names every disagreement.
"""

from __future__ import annotations

from app import program_config
from app.algo_store import sha256

# The startup block exactly as the v8 program logs it (colour codes removed).
V8_BLOCK = [
    "[09:06:41] PROFIT LADDER (per trade):",
    "[09:06:41]   take-profit: none — winners run until the trailing stop or 15:10",
    "[09:06:41]   break-even : at +20% premium gain, stop jumps to entry",
    "[09:06:41]   trail      : past +20% gain, stop rides 20% behind peak (locks profit)",
    "[09:06:41] STOP LADDER (per trade):",
    "[09:06:41]   hard stop  : -45% of premium initially, tightens only, never loosens",
    "[09:06:41]   rupee cap  : skip any trade risking more than Rs 9,000 (premium x lots x stop)",
    "[09:06:41] DAY / WEEK KILL SWITCHES (money terms):",
    "[09:06:41]   daily kill : stop the day at Rs 12,000 realised loss",
    "[09:06:41]   weekly kill: stop the week at Rs 25,000 realised loss",
    "[09:06:41]   streak halt: pause the day after 4 losses in a row",
    "[09:06:41]   profit lock: bank the day at +Rs 20,000 and stop",
    "[09:06:41]   trades/day : fixed 1",
    "[09:06:41] PORTFOLIO GUARD (last line):",
    "[09:06:41]   drawdown halt -45% from peak | deploy 40%/trade | position cap 60% | max 10 lots",
    "[09:06:41] entries 10:15–14:00, flat by 15:10. Ctrl-C to stop.",
]

# The v8 program's constants as they sit in its source.
V8_SOURCE = '''
import os
if not os.environ.get("ANGEL_API_KEY", "").strip(): os.environ["ANGEL_API_KEY"] = "not-a-real-key"
STRIKE_OFFSET = -1
DONCHIAN_LB = 90
ENTRY_START, ENTRY_CUTOFF, FORCE_CLOSE = (10, 15), (14, 0), (15, 10)
MIN_DTE, MAX_DTE = 2, 8
TARGET_PTS   = None    # None = no fixed target
BE_TRIGGER   = 0.20
TRAIL_FRAC   = 0.20
STOP_FRAC    = 0.45
DEPLOY_FRACTION      = 0.40
PER_TRADE_EQUITY_CAP = 0.60
PER_TRADE_RISK_RS    = 9_000.0
MAX_LOTS             = 10
MAX_PREMIUM          = 600.0
MAX_TRADES_DAY       = 1
DAILY_LOSS_LIMIT_RS  = 12_000.0
WEEKLY_LOSS_LIMIT_RS = 25_000.0
CONSEC_LOSS_HALT     = 4
DAILY_PROFIT_LOCK_RS = 20_000.0
MIN_CAPITAL          = 0.0
MAX_DRAWDOWN_STOP    = 0.45
raise SystemExit("parsed, never run")
'''


def register(db, source=V8_SOURCE, block=V8_BLOCK):
    db.upsert_algo("og", "ganesh_kavach_og_real (3)")
    vid = db.add_version("og", source, sha256(source), "test")
    db.set_algo_fields("og", active_version=vid, mode="live")
    for line in block:
        db.add_event("info", line, source="program", algo_id="og")
    return db.algo("og")


def test_the_startup_block_reads_back_into_parameters():
    got = program_config.parse_startup_block(V8_BLOCK[1:])
    assert got["TARGET_PTS"] is None
    assert (got["BE_TRIGGER"], got["TRAIL_FRAC"], got["STOP_FRAC"]) == (0.20, 0.20, 0.45)
    assert (got["PER_TRADE_RISK_RS"], got["DAILY_LOSS_LIMIT_RS"], got["WEEKLY_LOSS_LIMIT_RS"]) == (9000, 12000, 25000)
    assert (got["CONSEC_LOSS_HALT"], got["DAILY_PROFIT_LOCK_RS"], got["MAX_TRADES_DAY"]) == (4, 20000, 1)
    assert (got["MAX_DRAWDOWN_STOP"], got["DEPLOY_FRACTION"], got["PER_TRADE_EQUITY_CAP"]) == (0.45, 0.40, 0.60)
    assert got["MAX_LOTS"] == 10
    assert (got["ENTRY_START"], got["ENTRY_CUTOFF"], got["FORCE_CLOSE"]) == ("10:15", "14:00", "15:10")


def test_v9s_late_take_profit_and_a_fixed_target_are_read_too():
    got = program_config.parse_startup_block([
        "  take-profit: FIXED +90 index pts (single hard target, no TP ladder)",
        "  late TP    : from 180 min after entry, sell at +40% or better (bot-enforced)",
        "  expiry     : weekly contract 2-6 days out; no trade on a day without one",
    ])
    assert got["TARGET_PTS"] == 90
    assert (got["LATE_TP_AFTER_MIN"], got["LATE_TP_FRAC"]) == (180, 0.40)
    assert (got["MIN_DTE"], got["MAX_DTE"]) == (2, 6)


def test_the_code_is_parsed_never_run():
    got = program_config.parse_source_constants(V8_SOURCE)
    assert got["TARGET_PTS"] is None and got["BE_TRIGGER"] == 0.20
    assert (got["ENTRY_START"], got["FORCE_CLOSE"]) == ("10:15", "15:10")
    assert (got["MIN_DTE"], got["MAX_DTE"]) == (2, 8)
    assert not any("ANGEL" in k for k in got), "only the strategy's constants are read"


def test_exactly_the_three_values_the_page_showed_differ_from_the_built_in(tmp_db):
    report = program_config.config_report(tmp_db, register(tmp_db))
    assert sorted(report["differs_from_builtin"]) == ["BE_TRIGGER", "TARGET_PTS", "TRAIL_FRAC"]
    assert report["drift"] == [], "the live program runs what its code says"
    rows = {r["key"]: r for r in report["rows"]}
    assert (rows["BE_TRIGGER"]["running"], rows["BE_TRIGGER"]["builtin"]) == (0.20, 0.25)
    assert (rows["TRAIL_FRAC"]["running"], rows["TRAIL_FRAC"]["builtin"]) == (0.20, 0.35)
    assert (rows["TARGET_PTS"]["running"], rows["TARGET_PTS"]["builtin"]) == (None, 90)
    assert rows["STOP_FRAC"]["differs_from_builtin"] is False


def test_a_process_running_older_code_than_the_upload_is_flagged(tmp_db):
    """The code was changed (or a new version uploaded) and the process was not
    restarted: what is in memory is not what the desk holds."""
    newer = V8_SOURCE.replace("BE_TRIGGER   = 0.20", "BE_TRIGGER   = 0.25")
    report = program_config.config_report(tmp_db, register(tmp_db, source=newer))
    assert report["drift"] == ["BE_TRIGGER"]
    row = next(r for r in report["rows"] if r["key"] == "BE_TRIGGER")
    assert (row["running"], row["code"]) == (0.20, 0.25)


def test_the_latest_startup_is_the_one_read(tmp_db):
    register(tmp_db)
    for line in V8_BLOCK:
        tmp_db.add_event("info", line.replace("+20% premium gain", "+30% premium gain"),
                         source="program", algo_id="og")
    report = program_config.config_report(tmp_db, tmp_db.algo("og"))
    assert next(r for r in report["rows"] if r["key"] == "BE_TRIGGER")["running"] == 0.30


def test_a_program_that_never_started_has_only_its_code(tmp_db):
    report = program_config.config_report(tmp_db, register(tmp_db, block=[]))
    assert report["running"] is None
    assert report["drift"] == []
    assert sorted(report["differs_from_builtin"]) == ["BE_TRIGGER", "TARGET_PTS", "TRAIL_FRAC"]
