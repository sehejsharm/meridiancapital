"""How an algorithm earns the right to trade real money.

Three states, and the rule that you cannot skip one:

    pending   just uploaded, nothing known about it
    passed    cleared the static acceptance gate; may trade PAPER only
    cleared   also survived PAPER_SESSIONS_REQUIRED clean paper sessions;
              may be switched to LIVE by an operator typing the phrase

The paper requirement comes straight from the deployment plan, which asks for
five full trading days in paper mode with zero crashes and zero database
errors before any real capital is committed. The gate is a static proof of
coherence; only paper trading tells you how the thing behaves against a live
feed, and no amount of unit testing substitutes for that.

A session only counts if it was clean. A session with an error-level event
resets nothing, but it does not count towards the total either, so a strategy
that errors every day never accumulates credit.
"""

from __future__ import annotations

PAPER_SESSIONS_REQUIRED = 5

STATUS_PENDING = "pending"
STATUS_FAILED = "failed"
STATUS_PASSED = "passed"
STATUS_CLEARED = "cleared"

GO_LIVE_PHRASE = "TRADE REAL MONEY"


def may_run_paper(version: dict) -> tuple[bool, str]:
    status = version.get("status")
    if status in (STATUS_PASSED, STATUS_CLEARED):
        return True, ""
    if status == STATUS_FAILED:
        return False, "this version failed the acceptance gate and cannot be run"
    return False, "this version has not been through the acceptance gate yet"


def may_run_live(version: dict) -> tuple[bool, str]:
    status = version.get("status")
    if status == STATUS_CLEARED:
        return True, ""
    if status == STATUS_PASSED:
        done = int(version.get("paper_sessions") or 0)
        left = max(0, PAPER_SESSIONS_REQUIRED - done)
        return False, (
            f"{done} of {PAPER_SESSIONS_REQUIRED} clean paper sessions completed — "
            f"{left} more before this version can trade real money"
        )
    if status == STATUS_FAILED:
        return False, "this version failed the acceptance gate"
    return False, "this version has not been through the acceptance gate yet"


def record_paper_session(db, version_id: int) -> dict:
    """Count one clean paper session, promoting to cleared at the threshold."""
    total = db.bump_paper_sessions(version_id)
    promoted = False
    if total >= PAPER_SESSIONS_REQUIRED:
        version = db.version(version_id)
        if version and version.get("status") == STATUS_PASSED:
            db.set_version_status(version_id, STATUS_CLEARED)
            promoted = True
    return {
        "paper_sessions": total,
        "required": PAPER_SESSIONS_REQUIRED,
        "promoted_to_live_eligible": promoted,
    }


def progress(version: dict | None) -> dict:
    """What the dashboard shows beside a version."""
    if not version:
        return {"status": "none", "paper_sessions": 0, "required": PAPER_SESSIONS_REQUIRED,
                "can_paper": False, "can_live": False, "live_blocker": "no active version"}
    can_paper, _ = may_run_paper(version)
    can_live, blocker = may_run_live(version)
    return {
        "status": version.get("status"),
        "paper_sessions": int(version.get("paper_sessions") or 0),
        "required": PAPER_SESSIONS_REQUIRED,
        "can_paper": can_paper,
        "can_live": can_live,
        "live_blocker": blocker,
    }
