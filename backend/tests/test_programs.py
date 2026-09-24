"""Standalone trading programs run exactly as written.

These start real processes through the real fleet and supervisor: the
program receives the operator's mode as it would on the command line, its
output lands in the event log, Ctrl-C stops it the way it expects, and an API
restart recognises it rather than starting a second copy.
"""

from __future__ import annotations

import time

import pytest

PROGRAM = '''#!/usr/bin/env python3
"""A trader shaped like the operator's own: argparse flags, prints, Ctrl-C."""
import argparse, os, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))

def trade(dry_run=True):
    print("\\x1b[32mconnected to Angel One\\x1b[0m", flush=True)
    print(f"ORDER FILLED BUY 65 x NIFTY-TEST dry_run={dry_run}", flush=True)
    print("WARN candle feed throttled - backing off", flush=True)
    try:
        while True:
            time.sleep(0.05)
    except KeyboardInterrupt:
        open(os.path.join(HERE, "stopped.txt"), "w").write("clean")
        print("stopped by user.", flush=True)

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--paper", action="store_true")
    ap.add_argument("--live", action="store_true")
    a = ap.parse_args()
    open(os.path.join(HERE, "argv.txt"), "w").write(" ".join(sys.argv[1:]))
    if a.live:
        trade(dry_run=False)
    elif a.paper:
        trade(dry_run=True)
'''


@pytest.fixture
def fleet(tmp_db, tmp_path, monkeypatch):
    from app import programs
    from app.fleet import Fleet
    from app.supervisor import Supervisor

    monkeypatch.setattr(programs, "PROGRAM_DIR", tmp_path / "programs")
    f = Fleet(tmp_db, supervisor_factory=Supervisor)
    yield f
    for sup in f.all().values():
        if sup.state.running:
            sup.stop(force=True)


def register(db, algo_id, source, mode="paper"):
    db.upsert_algo(algo_id, algo_id, kind="uploaded")
    vid = db.add_version(algo_id, source, "sha", "tester")
    db.set_algo_fields(algo_id, active_version=vid, mode=mode)


def wait_for(cond, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


# ── classification, from the text alone ──────────────────────────────────────
def test_the_operators_kind_of_file_is_a_program():
    from app.programs import mode_arguments, runtime_of

    assert runtime_of(PROGRAM) == "program"
    assert mode_arguments(PROGRAM) == {"paper": ["--paper"], "live": ["--live"]}


def test_a_strategy_module_is_still_a_strategy():
    from pathlib import Path

    from app.programs import runtime_of

    assert runtime_of(Path("engine/builtin_gk50k.py").read_text()) == "strategy"


def test_a_file_that_neither_trades_nor_runs_is_invalid():
    from app.programs import runtime_of

    assert runtime_of("NAME = 'x'\n") == "invalid"


# ── running one ──────────────────────────────────────────────────────────────
def test_a_program_runs_as_written_on_paper(fleet, tmp_db):
    from app import programs

    register(tmp_db, "og", PROGRAM, mode="paper")
    res = fleet.start("og")
    assert res["ok"] is True, res
    here = programs.program_path("og").parent
    assert wait_for(lambda: (here / "argv.txt").exists())
    assert (here / "argv.txt").read_text() == "--paper"


def test_real_money_reaches_the_program_as_live(fleet, tmp_db):
    from app import programs

    register(tmp_db, "og", PROGRAM, mode="live")
    assert fleet.start("og")["ok"] is True
    here = programs.program_path("og").parent
    assert wait_for(lambda: (here / "argv.txt").exists())
    assert (here / "argv.txt").read_text() == "--live"


def test_its_output_reaches_the_journal(fleet, tmp_db):
    from app.program_output import ProgramOutput

    register(tmp_db, "og", PROGRAM)
    fleet.start("og")
    follower = ProgramOutput(tmp_db, fleet)

    def seen():
        follower.pump()
        return any("ORDER FILLED" in e["message"] for e in tmp_db.events(50, algo_id="og"))

    assert wait_for(seen), "the program's output never reached the event log"
    events = {e["message"]: e["level"] for e in tmp_db.events(50, algo_id="og")}
    assert "connected to Angel One" in events, "colour codes must be stripped"
    assert events["WARN candle feed throttled - backing off"] == "warn"


def test_stopping_sends_ctrl_c_so_the_program_can_clean_up(fleet, tmp_db):
    from app import programs

    register(tmp_db, "og", PROGRAM)
    fleet.start("og")
    here = programs.program_path("og").parent
    assert wait_for(lambda: (here / "argv.txt").exists())
    time.sleep(0.3)
    res = fleet.stop("og", reason="test")
    assert res["ok"] is True
    assert (here / "stopped.txt").read_text() == "clean", "the program was killed, not interrupted"


def test_an_api_restart_recognises_a_running_program(fleet, tmp_db):
    """Otherwise the scheduler would start a second copy trading the account."""
    from app.supervisor import _is_engine

    register(tmp_db, "og", PROGRAM)
    fleet.start("og")
    pid = fleet.get("og").state.pid
    assert wait_for(lambda: _is_engine(pid))


def test_a_new_version_runs_from_the_same_folder(fleet, tmp_db):
    """A program keeps its state beside itself; a re-upload must not strand it."""
    from app import programs

    first = programs.materialise_program("og", PROGRAM)
    second = programs.materialise_program("og", PROGRAM + "\n# v2\n")
    assert first == second


# ── refusing what cannot be run safely ───────────────────────────────────────
def test_a_program_with_no_paper_switch_is_not_started(fleet, tmp_db):
    """Started 'on paper', a program that cannot be told so might trade live."""
    blind = PROGRAM.replace('"--paper"', '"--dry"').replace('"--live"', '"--real"')
    register(tmp_db, "blind", blind)
    res = fleet.start("blind")
    assert res["ok"] is False
    assert "paper from live" in res["detail"]


def test_a_program_reading_the_mode_from_the_environment_is_accepted():
    from app.programs import mode_arguments

    src = 'import os\nLIVE = os.environ["MERIDIAN_TRADING_MODE"] == "live"\nif __name__ == "__main__":\n    pass\n'
    assert mode_arguments(src) == {"paper": [], "live": []}


def test_a_missing_package_is_named_with_the_install_command(fleet, tmp_db):
    register(tmp_db, "needy", "import surely_not_installed_pkg\n" + PROGRAM)
    res = fleet.start("needy")
    assert res["ok"] is False
    assert "surely_not_installed_pkg" in res["detail"] and "pip install" in res["detail"]


def test_the_operators_imports_are_all_in_the_vm_requirements():
    """SmartApi, pandas, pyotp and colorama — colorama was missing, so the
    operator's script would have died on its first import."""
    import re
    from pathlib import Path

    from app.programs import PIP_NAMES

    declared = {
        re.split(r"[=<>\[ ]", line.strip())[0].lower()
        for line in Path("requirements.txt").read_text().splitlines()
        if line.strip() and not line.startswith("#")
    }
    for module in ("pandas", "colorama", "pyotp", "SmartApi"):
        assert PIP_NAMES.get(module, module).lower() in declared, f"{module} is not installed on the VM"


def test_an_uploaded_program_is_labelled_not_gated(tmp_path, monkeypatch):
    import os

    from fastapi.testclient import TestClient

    from app import deps
    from app.config import settings
    from tests.test_api import PASSWORD, FakeSupervisor

    monkeypatch.setattr(settings, "db_path", tmp_path / "up.db")
    monkeypatch.setattr(settings, "password_hash", os.environ["MERIDIAN_PASSWORD_HASH"])
    monkeypatch.setattr(deps, "Supervisor", FakeSupervisor)
    from app.main import app

    with TestClient(app) as c:
        tok = c.post("/api/auth/login", json={"password": PASSWORD}).json()["token"]
        h = {"Authorization": f"Bearer {tok}"}
        body = c.post("/api/algos", json={"name": "OG Real", "source": PROGRAM}, headers=h).json()
        assert body["kind"] == "program"
        algo = c.get(f"/api/algos/{body['algo_id']}", headers=h).json()
        assert algo["runtime_kind"] == "program"
        assert algo["gate"]["status"] == "program"
