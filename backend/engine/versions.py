"""What is known about a version of an algorithm's source.

Uploaded source is screened by the acceptance gate and the verdict is recorded,
but the verdict is *advice*, not a permission: the operator decides what runs
and in which mode. The gate cannot tell a profitable strategy from a losing one
— it only checks internal coherence — so treating its opinion as a lock stopped
working code from running while never once proving the code that passed was
sound.

Paper and live are therefore a straight operator choice, made when an algorithm
is started and changeable whenever it is stopped.
"""

from __future__ import annotations

STATUS_PENDING = "pending"
STATUS_FAILED = "failed"
STATUS_PASSED = "passed"
# A complete program: run as written, so the strategy gate does not apply.
STATUS_PROGRAM = "program"


def gate_verdict(version: dict | None) -> str:
    """The gate's opinion of this version, for display."""
    if not version:
        return "none"
    return version.get("status") or STATUS_PENDING


def summary(version: dict | None) -> dict:
    """What the dashboard shows beside a version."""
    if not version:
        return {"status": "none", "gate_passed": False, "runnable": False}
    status = gate_verdict(version)
    return {
        "status": status,
        "gate_passed": status == STATUS_PASSED,
        # Anything with source attached can run. The gate result colours the
        # badge next to it; it does not decide this.
        "runnable": True,
    }
