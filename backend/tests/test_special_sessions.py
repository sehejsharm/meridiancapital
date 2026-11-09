"""Muhurat trading and other sessions NSE holds outside its calendar.

On 8 Nov 2026 (a Sunday) the scheduler used to stop, within 15 seconds and with
"session ended", anything the operator started: a Sunday is never a session.
On a special-session day nothing starts on its own, and what the operator
starts runs until five minutes before the session's close.
"""

from __future__ import annotations

from datetime import date, datetime

import pytest

from app import scheduler as scheduler_mod
from app import special_sessions as S
from shared import market_calendar as M
from shared.nse_holidays import SPECIAL_SESSIONS
from tests.test_autostart import FakeFleet, FakeSupervisor
from tests.test_desk import auth, client  # noqa: F401  (fixtures)

MUHURAT = {"2026-11-08": {"open": "14:20", "close": "15:30", "label": "Muhurat trading (Diwali)"}}


def at(hm: str, day: str = "2026-11-08") -> datetime:
    return datetime.strptime(f"{day} {hm}", "%Y-%m-%d %H:%M")


def test_muhurat_2026_ships_with_the_code():
    assert SPECIAL_SESSIONS["2026-11-08"]["open"] == "14:20"
    assert SPECIAL_SESSIONS["2026-11-08"]["close"] == "15:30"


def test_the_window_opens_30_minutes_early_and_ends_5_before_the_close():
    assert M.special_window(date(2026, 11, 8), MUHURAT) == (at("13:50"), at("15:25"))
    assert M.in_special_session(at("13:49"), MUHURAT) is None
    assert M.in_special_session(at("13:50"), MUHURAT)["label"] == "Muhurat trading (Diwali)"
    assert M.in_special_session(at("15:24"), MUHURAT) is not None
    assert M.in_special_session(at("15:25"), MUHURAT) is None
    assert M.in_special_session(at("14:30", "2026-11-09"), MUHURAT) is None, "only on its own day"
    assert M.special_window(date(2026, 11, 9), MUHURAT) is None


def test_upcoming_sessions_for_the_banner():
    assert [s["day"] for s in M.upcoming_specials(date(2026, 11, 7), MUHURAT)] == ["2026-11-08"]
    assert M.upcoming_specials(date(2026, 11, 9), MUHURAT) == []


def test_shipped_sessions_load_once_and_keep_the_operators_edits(tmp_db):
    assert S.seed(tmp_db, MUHURAT) == ["2026-11-08"]
    S.put(tmp_db, "2026-11-08", "14:20", "15:40", "Muhurat trading (Diwali)")
    assert S.seed(tmp_db, MUHURAT) == []
    assert S.load(tmp_db)["2026-11-08"]["close"] == "15:40", "a restart must not undo the edit"
    S.remove(tmp_db, "2026-11-08")
    S.seed(tmp_db, MUHURAT)
    assert S.load(tmp_db) == {}, "a removed session stays removed"


@pytest.mark.parametrize("day,o,c,msg", [
    ("08-11-2026", "14:20", "15:30", "ISO date"), ("2026-11-08", "2:20", "15:30", "24-hour"),
    ("2026-11-08", "14:20", "25:00", "24-hour"), ("2026-11-08", "15:30", "14:20", "after the open"),
])
def test_bad_sessions_are_refused(tmp_db, day, o, c, msg):
    with pytest.raises(ValueError, match=msg):
        S.put(tmp_db, day, o, c, "x")


# ── the scheduler on Muhurat Sunday ──────────────────────────────────────────
def _desk(tmp_db, monkeypatch, moment: datetime, running: bool, armed: bool = True):
    tmp_db.upsert_algo("og", "ganesh_kavach_og_real (3)", kind="uploaded")
    tmp_db.set_algo_fields("og", enabled=1 if armed else 0)
    S.seed(tmp_db, MUHURAT)
    sup = FakeSupervisor()
    sup.state.running = running
    builtin = FakeSupervisor()
    sched = scheduler_mod.Scheduler(tmp_db, builtin, fleet=FakeFleet({"og": sup}))
    sched.set_enabled(True)
    monkeypatch.setattr(scheduler_mod, "now_ist", lambda: moment)
    return sched, sup, builtin


def test_what_the_operator_starts_for_muhurat_keeps_running(tmp_db, monkeypatch):
    for hm in ("13:50", "14:20", "15:00", "15:24"):
        sched, sup, _ = _desk(tmp_db, monkeypatch, at(hm), running=True)
        sched.tick()
        assert sup.stops == 0, f"stopped at {hm} during the Muhurat window"
        assert "Muhurat" in sched.last_decision


def test_nothing_starts_on_its_own_for_muhurat(tmp_db, monkeypatch):
    sched, sup, builtin = _desk(tmp_db, monkeypatch, at("14:20"), running=False, armed=True)
    sched.tick()
    assert sup.starts == 0 and builtin.starts == 0, "an armed algorithm started itself on Sunday"


def test_it_is_stopped_five_minutes_before_the_close(tmp_db, monkeypatch):
    sched, sup, _ = _desk(tmp_db, monkeypatch, at("15:25"), running=True)
    sched.tick()
    assert sup.stops == 1
    assert tmp_db.algo("og")["enabled"], "stays armed for Monday's open"


def test_outside_the_window_the_sunday_rule_still_holds(tmp_db, monkeypatch):
    sched, sup, _ = _desk(tmp_db, monkeypatch, at("11:00"), running=True)
    sched.tick()
    assert sup.stops == 1, "a Sunday morning run is still stopped"


def test_status_tells_the_deck_about_the_session(tmp_db, monkeypatch):
    sched, _, _ = _desk(tmp_db, monkeypatch, at("10:00"), running=False)
    st = sched.status()
    assert st["special_today"]["runs_from"] == "13:50" and st["special_today"]["stops_at"] == "15:25"
    assert st["in_special_window"] is False
    monkeypatch.setattr(scheduler_mod, "now_ist", lambda: at("14:00", "2026-11-07"))
    st = sched.status()
    assert st["special_today"] is None and st["special_upcoming"][0]["day"] == "2026-11-08"


# ── the API ──────────────────────────────────────────────────────────────────
def test_the_api_lists_edits_and_removes_sessions(client, auth):  # noqa: F811
    r = client.get("/api/control/special-sessions", headers=auth)
    assert r.status_code == 200 and any(s["day"] == "2026-11-08" for s in r.json()["sessions"])
    r = client.post("/api/control/special-sessions", json={"day": "2026-11-08", "open": "14:20", "close": "15:40",
                                                   "label": "Muhurat trading (Diwali)"}, headers=auth)
    assert r.status_code == 200 and r.json()["sessions"][0]["close"] == "15:40"
    r = client.post("/api/control/special-sessions", json={"day": "2026-11-08", "open": "15:40", "close": "14:20"}, headers=auth)
    assert r.status_code == 400 and "after the open" in r.json()["detail"]
    r = client.delete("/api/control/special-sessions/2026-11-08", headers=auth)
    assert r.status_code == 200 and r.json()["sessions"] == []
    assert client.get("/api/control/special-sessions").status_code == 401
