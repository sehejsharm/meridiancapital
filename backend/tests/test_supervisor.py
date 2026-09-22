"""An engine that dies before telemetry is up must still say why.

The supervisor used to send the child's stdout and stderr to /dev/null, so a
crash during startup — a bad credential, a failed import — left a restart loop
with no cause recorded anywhere. These tests pin the capture in place.
"""

from __future__ import annotations

import time

import pytest

from app.config import settings
from app.supervisor import OUTPUT_TAIL_BYTES, Supervisor, _tail


def _fake_python(tmp_path, body: str):
    """A stand-in for the interpreter the supervisor spawns."""
    script = tmp_path / "fake-python"
    script.write_text("#!/bin/sh\n" + body)
    script.chmod(0o755)
    return script


def _local(sup, tmp_path):
    """Keep a supervisor's files out of the shared data directory."""
    sup.pidfile = tmp_path / "engine.pid"
    sup.outfile = tmp_path / "engine.out"
    return sup


def _wait_for_exit(sup, timeout: float = 10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        sup.refresh()
        if not sup.state.running:
            return True
        time.sleep(0.05)
    return False


# ── the tail reader ──────────────────────────────────────────────────────────
def test_tail_of_a_missing_file_is_empty(tmp_path):
    assert _tail(tmp_path / "nothing-here.out") == []


def test_tail_keeps_the_last_lines_and_drops_blanks(tmp_path):
    p = tmp_path / "out"
    p.write_text("\n".join(f"line {i}" for i in range(50)) + "\n\n\n")
    assert _tail(p, max_lines=3) == ["line 47", "line 48", "line 49"]


def test_tail_does_not_return_a_half_line(tmp_path):
    """Reading from the end lands mid-line; that fragment is not a line."""
    p = tmp_path / "out"
    p.write_text("x" * (OUTPUT_TAIL_BYTES * 2) + "\nthe real last line\n")
    assert _tail(p) == ["the real last line"]


# ── capture through a real spawn ─────────────────────────────────────────────
def test_a_crash_before_telemetry_is_recorded(tmp_db, tmp_path, monkeypatch):
    monkeypatch.setattr(
        settings,
        "python_bin",
        str(
            _fake_python(
                tmp_path,
                "echo 'connecting to Angel One'\n"
                "echo 'RuntimeError: invalid TOTP secret' >&2\n"
                "exit 2\n",
            )
        ),
    )
    sup = _local(Supervisor(tmp_db, algo_id="crashy"), tmp_path)

    assert sup.start()["ok"] is True
    assert _wait_for_exit(sup), "the fake engine never exited"

    assert sup.state.last_exit_code == 2
    # stdout and stderr are merged in the order they were written.
    assert sup.state.stdout_tail == [
        "connecting to Angel One",
        "RuntimeError: invalid TOTP secret",
    ]
    assert sup.snapshot()["last_output"][-1] == "RuntimeError: invalid TOTP secret"

    # And the cause reaches the event log, which is what the deck renders.
    stopped = [e for e in tmp_db.events(50) if "engine stopped" in e["message"]]
    assert stopped, "no stop event was recorded"
    assert "invalid TOTP secret" in stopped[0]["message"]
    assert stopped[0]["level"] == "error"


def test_a_clean_exit_reports_no_crash_output(tmp_db, tmp_path, monkeypatch):
    monkeypatch.setattr(
        settings, "python_bin", str(_fake_python(tmp_path, "echo 'shutting down'\nexit 0\n"))
    )
    sup = _local(Supervisor(tmp_db, algo_id="tidy"), tmp_path)

    assert sup.start()["ok"] is True
    assert _wait_for_exit(sup)

    assert sup.state.last_exit_code == 0
    assert sup.state.stdout_tail == []
    stopped = [e for e in tmp_db.events(50) if "engine stopped" in e["message"]]
    assert stopped and stopped[0]["level"] == "info"
    assert "last output" not in stopped[0]["message"]


def test_each_start_gets_a_fresh_log(tmp_db, tmp_path, monkeypatch):
    """A tail from the previous run would send the operator after the wrong bug."""
    monkeypatch.setattr(
        settings, "python_bin", str(_fake_python(tmp_path, "echo 'run one failure' >&2\nexit 2\n"))
    )
    sup = _local(Supervisor(tmp_db, algo_id="twice"), tmp_path)
    sup.start()
    assert _wait_for_exit(sup)
    assert sup.state.stdout_tail == ["run one failure"]

    monkeypatch.setattr(
        settings, "python_bin", str(_fake_python(tmp_path, "echo 'run two failure' >&2\nexit 2\n"))
    )
    sup.state.blocked_until = 0.0
    sup.start()
    assert _wait_for_exit(sup)
    assert sup.state.stdout_tail == ["run two failure"]


def test_an_unwritable_log_does_not_stop_the_engine(tmp_db, tmp_path, monkeypatch):
    """Losing the diagnostics is worse than nothing; losing the engine is worse."""
    monkeypatch.setattr(settings, "python_bin", str(_fake_python(tmp_path, "exit 0\n")))
    sup = _local(Supervisor(tmp_db, algo_id="nolog"), tmp_path)
    sup.outfile = tmp_path / "no-such-directory" / "engine.out"

    assert sup.start()["ok"] is True
    assert _wait_for_exit(sup)
    assert sup.state.last_exit_code == 0
    assert any("engine output will be discarded" in e["message"] for e in tmp_db.events(50))


@pytest.mark.parametrize("code", [0, 1, 2])
def test_the_exit_code_is_preserved(tmp_db, tmp_path, monkeypatch, code):
    monkeypatch.setattr(settings, "python_bin", str(_fake_python(tmp_path, f"exit {code}\n")))
    sup = _local(Supervisor(tmp_db, algo_id=f"code{code}"), tmp_path)
    sup.start()
    assert _wait_for_exit(sup)
    assert sup.state.last_exit_code == code
