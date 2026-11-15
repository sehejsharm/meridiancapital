"""Deploy safety: the install guard, backups and the restore test, staging, paper first.

The shell scripts are run for real against a scratch database.
"""

from __future__ import annotations

import gzip
import json
import os
import shutil
import sqlite3
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app import backups, paper_first, upkeep
from shared.db import Database
from tests.test_desk import auth, client  # noqa: F401  (fixtures)

DEPLOY = Path(__file__).resolve().parent.parent / "deploy"
REPO = DEPLOY.parent.parent


# ── updates only outside market hours ────────────────────────────────────────

def blocked(now: str) -> tuple[bool, str]:
    r = subprocess.run(
        ["bash", "-c", f'source "{DEPLOY}/market-hours.sh"; update_blocked_reason "{REPO}"'],
        env={**os.environ, "MERIDIAN_NOW": now, "MERIDIAN_SKIP_PROCESS_CHECK": "1"},
        capture_output=True, text=True, check=False)
    return r.returncode == 0, r.stdout.strip()


@pytest.mark.parametrize("now,expect", [
    ("2026-11-12 4 0859", False),      # Thursday, a minute before the window
    ("2026-11-12 4 0900", True),
    ("2026-11-12 4 1200", True),
    ("2026-11-12 4 1529", True),
    ("2026-11-12 4 1530", False),      # the close: updates are allowed again
    ("2026-11-14 6 1200", False),      # Saturday
    ("2026-11-15 7 1200", False),      # Sunday
    ("2026-11-12 4 0030", False),
    ("2026-11-10 2 1200", False),      # Diwali Balipratipada: NSE is closed
])
def test_an_update_waits_while_the_market_is_open(now, expect):
    is_blocked, reason = blocked(now)
    assert is_blocked is expect
    assert ("market is open" in reason) is expect


def test_an_nse_holiday_is_not_a_trading_day():
    from shared.nse_holidays import NSE_HOLIDAYS

    day = next(d for d in sorted(NSE_HOLIDAYS) if datetime.fromisoformat(d).isoweekday() <= 5)
    dow = datetime.fromisoformat(day).isoweekday()
    assert blocked(f"{day} {dow} 1200") == (False, "")


def test_the_installer_refuses_before_touching_anything_and_takes_force():
    text = (DEPLOY / "install.sh").read_text()
    guard, first_change = text.index("update_blocked_reason"), text.index("apt-get update")
    assert guard < first_change                           # checked before the first change to the machine
    assert "--force" in text and "exit 3" in text
    assert subprocess.run(["bash", "-n", str(DEPLOY / "install.sh")], check=False).returncode == 0
    # Not root: it stops at once, whatever the hour, having changed nothing.
    r = subprocess.run(["bash", str(DEPLOY / "install.sh"), "--no-such-option"], capture_output=True, text=True,
                       check=False)
    assert r.returncode == 2 and "unknown option" in r.stderr


# ── backup and restore test, run for real ────────────────────────────────────

@pytest.fixture
def box(tmp_path):
    """A scratch 'VM': a data folder with a live database, and an empty env file."""
    data = tmp_path / "data"
    db = Database(data / "meridian.db")
    for i in range(3):
        db.add_trade({"entry_ts": f"2026-11-0{i + 2}T11:00:00", "exit_ts": f"2026-11-0{i + 2}T12:00:00",
                      "session_date": f"2026-11-0{i + 2}", "mode": "live", "tsym": "NIFTY10NOV2625500CE",
                      "qty": 65, "entry_prem": 100.0, "exit_prem": 110.0, "gross": 650.0, "charges": 70.0,
                      "net": 580.0, "algo_id": "og"})
    db.add_event("info", "a journal line", source="api")
    env_file = tmp_path / "meridian.env"
    env_file.write_text("")
    env = {**{k: v for k, v in os.environ.items() if not k.startswith("MERIDIAN_BACKUP")},
           "MERIDIAN_DATA_DIR": str(data), "MERIDIAN_DB_PATH": str(data / "meridian.db"),
           "MERIDIAN_ENV_FILE": str(env_file), "MERIDIAN_PYTHON": shutil.which("python3") or "python3"}
    env.pop("MERIDIAN_BACKUP_DIR", None)
    return {"data": data, "db": db, "env": env, "env_file": env_file}


def run(script: str, box, *args) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", str(DEPLOY / script), *args], env=box["env"], capture_output=True, text=True,
                          check=False)


def test_a_backup_is_verified_and_says_it_never_left_the_machine(box):
    r = run("backup.sh", box)
    assert r.returncode == 0, r.stderr
    last = json.loads((box["data"] / "backups" / "last-backup.json").read_text())
    assert last["verified"] is True and last["off_machine"] == "not set up" and last["bytes"] > 0
    assert (box["data"] / "backups" / last["file"]).exists()
    assert "exists only on this VM" in r.stdout
    st = backups.status(box["db"])
    assert [p["kind"] for p in st["problems"]] == ["off_machine_missing"]
    assert backups.alerts(box["db"]) == []                # a standing condition, not an alert every day


def test_the_restore_test_restores_the_backup_and_counts_what_matters(box):
    assert run("backup.sh", box).returncode == 0
    r = run("restore-test.sh", box)
    assert r.returncode == 0, r.stderr
    out = json.loads((box["data"] / "backups" / "last-restore-test.json").read_text())
    assert out["ok"] is True and out["source"] == "local copy"
    assert out["rows"]["trades"] == 3 and out["rows"]["last_trade_day"] == "2026-11-04"
    assert "restored and verified: 3 trades (latest 2026-11-04)" in out["detail"]
    # The live database was only read.
    assert len(box["db"].trades(limit=10)) == 3


def test_a_backup_that_cannot_be_restored_fails_the_test_loudly(box):
    assert run("backup.sh", box).returncode == 0
    newest = sorted((box["data"] / "backups").glob("meridian-*.db.gz"))[-1]
    with gzip.open(newest, "wb") as fh:
        fh.write(b"this is not a database")
    r = run("restore-test.sh", box)
    assert r.returncode == 1 and "RESTORE TEST FAILED" in r.stderr
    out = json.loads((box["data"] / "backups" / "last-restore-test.json").read_text())
    assert out["ok"] is False
    assert [p["kind"] for p in backups.alerts(box["db"])] == ["restore_failed"]


def test_a_backup_missing_trades_the_live_database_has_fails(box):
    assert run("backup.sh", box).returncode == 0
    newest = sorted((box["data"] / "backups").glob("meridian-*.db.gz"))[-1]
    raw = box["data"] / "scratch.db"
    raw.write_bytes(gzip.decompress(newest.read_bytes()))
    con = sqlite3.connect(raw)
    con.execute("DELETE FROM trades")
    con.commit()
    con.close()
    newest.write_bytes(gzip.compress(raw.read_bytes()))
    r = run("restore-test.sh", box)
    assert r.returncode == 1
    assert "the live database has trades and the backup has none" in r.stderr


def test_no_backup_at_all_is_a_failed_test(box):
    r = run("restore-test.sh", box)
    assert r.returncode == 1
    assert json.loads((box["data"] / "backups" / "last-restore-test.json").read_text())["ok"] is False


def test_an_unencrypted_database_is_never_uploaded(box):
    box["env_file"].write_text("MERIDIAN_BACKUP_URL=https://objectstorage.invalid/p/token/o/\n")
    r = run("backup.sh", box)
    assert r.returncode == 1
    assert "an unencrypted" in r.stderr and "never uploaded" in r.stderr
    last = json.loads((box["data"] / "backups" / "last-backup.json").read_text())
    assert last["off_machine"] == "failed" and last["verified"] is True      # the local copy is still good
    raw = (box["data"] / "backups" / "last-backup.json").read_text()
    assert "objectstorage" not in raw and "token" not in raw                 # the address is never written down
    assert [p["kind"] for p in backups.alerts(box["db"])] == ["off_machine_failed"]


def test_the_copy_that_leaves_is_encrypted_and_only_the_passphrase_opens_it(box, tmp_path):
    """The same openssl commands the scripts use, end to end."""
    assert run("backup.sh", box).returncode == 0
    newest = sorted((box["data"] / "backups").glob("meridian-*.db.gz"))[-1]
    enc, dec = tmp_path / "copy.enc", tmp_path / "copy.gz"
    env = {**os.environ, "MERIDIAN_BACKUP_PASSPHRASE": "correct horse"}
    base = ["openssl", "enc", "-aes-256-cbc", "-pbkdf2", "-iter", "200000"]
    assert subprocess.run([*base, "-salt", "-in", str(newest), "-out", str(enc), "-pass",
                           "env:MERIDIAN_BACKUP_PASSPHRASE"], env=env, check=False).returncode == 0
    assert enc.read_bytes()[:8] == b"Salted__" and newest.read_bytes()[:64] not in enc.read_bytes()
    assert subprocess.run([*base, "-d", "-in", str(enc), "-out", str(dec), "-pass",
                           "env:MERIDIAN_BACKUP_PASSPHRASE"], env=env, check=False).returncode == 0
    assert dec.read_bytes() == newest.read_bytes()
    wrong = subprocess.run([*base, "-d", "-in", str(enc), "-out", str(tmp_path / "x"), "-pass",
                            "env:MERIDIAN_BACKUP_PASSPHRASE"],
                           env={**os.environ, "MERIDIAN_BACKUP_PASSPHRASE": "wrong"}, capture_output=True,
                           check=False)
    assert wrong.returncode != 0 or (tmp_path / "x").read_bytes() != newest.read_bytes()


def test_a_missed_night_and_an_old_restore_test_are_reported(tmp_db):
    folder = backups.folder(tmp_db)
    folder.mkdir(parents=True)
    now = datetime(2026, 11, 11, 4, 0, tzinfo=timezone.utc)
    stamp = lambda dt: dt.strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: E731
    (folder / "last-backup.json").write_text(json.dumps(
        {"ts": stamp(now - timedelta(hours=11)), "file": "meridian-x.db.gz", "bytes": 10, "verified": True,
         "off_machine": "ok"}))
    (folder / "last-restore-test.json").write_text(json.dumps({"ts": stamp(now - timedelta(days=10)), "ok": True}))
    assert backups.status(tmp_db, now)["problems"] == []
    (folder / "last-backup.json").write_text(json.dumps(
        {"ts": stamp(now - timedelta(hours=60)), "file": "meridian-x.db.gz", "off_machine": "ok"}))
    (folder / "last-restore-test.json").write_text(json.dumps({"ts": stamp(now - timedelta(days=50)), "ok": True}))
    kinds = [p["kind"] for p in backups.status(tmp_db, now)["problems"]]
    assert kinds == ["backup_stale", "restore_stale"]
    assert "2.5 days old" in backups.status(tmp_db, now)["problems"][0]["text"]


def test_backup_trouble_is_sent_once_a_day(tmp_db):
    folder = backups.folder(tmp_db)
    folder.mkdir(parents=True)
    (folder / "last-restore-test.json").write_text(json.dumps(
        {"ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "ok": False, "detail": "integrity check: bad"}))
    sent = []
    u = upkeep.Upkeep(tmp_db, sender=lambda *a: sent.append(a) or {})
    assert u.backup_watch(datetime(2026, 11, 11, 8, 0)) == []              # before 09:00
    assert len(u.backup_watch(datetime(2026, 11, 11, 9, 5))) == 1
    assert u.backup_watch(datetime(2026, 11, 11, 13, 0)) == []
    assert sent[0][0] == "Backups need attention" and "integrity check: bad" in sent[0][1]


# ── paper first ──────────────────────────────────────────────────────────────

def _run(db, algo, mode, start, stop, version_id=None):
    with db.conn() as c:
        c.execute("INSERT INTO engine_runs(started_ts, stopped_ts, pid, mode, trigger, exit_code, reason, algo_id, "
                  "version_id) VALUES(?,?,?,?,?,?,?,?,?)", (start, stop, 1, mode, "manual", 0, "", algo, version_id))


def _version(db, algo, created):
    vid = db.add_version(algo, "print(1)", f"sha-{created}", "tester")
    with db.conn() as c:
        c.execute("UPDATE algo_versions SET created_ts = ? WHERE id = ?", (created, vid))
    return vid


def test_full_paper_sessions_are_counted_per_version(tmp_db):
    tmp_db.upsert_algo("og", "ganesh kavach")
    v1 = _version(tmp_db, "og", "2026-10-01T08:00:00")
    v2 = _version(tmp_db, "og", "2026-11-02T08:00:00")
    # v1, before runs carried a version: attributed by upload time.
    _run(tmp_db, "og", "paper", "2026-10-05T09:05:00", "2026-10-05T15:25:00")     # 09:15-15:25 = 370 min: full
    _run(tmp_db, "og", "live", "2026-10-06T09:05:00", "2026-10-06T15:25:00")
    # v2: two full paper days, one cut short, one split by a restart, then real money.
    _run(tmp_db, "og", "paper", "2026-11-03T09:05:00", "2026-11-03T15:25:00", v2)
    _run(tmp_db, "og", "paper", "2026-11-04T09:05:00", "2026-11-04T11:00:00", v2)  # 105 min: not a session
    _run(tmp_db, "og", "paper", "2026-11-05T09:05:00", "2026-11-05T12:00:00", v2)  # 165 min ...
    _run(tmp_db, "og", "paper", "2026-11-05T12:10:00", "2026-11-05T15:25:00", v2)  # ... + 195 = 360: full
    _run(tmp_db, "og", "live", "2026-11-06T09:05:00", "2026-11-06T15:25:00", v2)
    got = paper_first.by_version(tmp_db, "og", now=datetime(2026, 11, 7, 10, 0))
    assert got[v1] == {"paper_sessions": 1, "paper_days": ["2026-10-05"], "partial_paper_days": 0, "live_days": 1,
                       "first_live": "2026-10-06"}
    assert got[v2]["paper_sessions"] == 2 and got[v2]["paper_days"] == ["2026-11-03", "2026-11-05"]
    assert got[v2]["partial_paper_days"] == 1 and got[v2]["live_days"] == 1

    tmp_db.set_algo_fields("og", active_version=v2)
    st = paper_first.status(tmp_db, tmp_db.algo("og"), got)
    assert st["ok"] is False and st["paper_sessions"] == 2 and st["required"] == 3
    assert st["warning"] == ("This version has run 2 full paper sessions; the rule is 3 before real money "
                             "(a full session is 300 of the 375 market minutes on paper). "
                             "It has already traded real money on 1 day.")
    assert paper_first.warning(0).startswith("This version has run no full paper session;")


def test_three_full_sessions_clear_the_warning(tmp_db):
    tmp_db.upsert_algo("og", "ganesh kavach")
    v = _version(tmp_db, "og", "2026-11-01T08:00:00")
    for d in ("03", "04", "05"):
        _run(tmp_db, "og", "paper", f"2026-11-{d}T09:05:00", f"2026-11-{d}T15:25:00", v)
    tmp_db.set_algo_fields("og", active_version=v)
    st = paper_first.status(tmp_db, tmp_db.algo("og"))
    assert st["ok"] is True and st["warning"] is None and st["paper_sessions"] == 3


def test_going_to_real_money_short_of_paper_sessions_is_written_in_the_journal(client, auth):  # noqa: F811
    from app.deps import ctx

    src = "import sys\nif __name__ == '__main__':\n    mode = '--live' in sys.argv or '--paper' in sys.argv\n"
    up = client.post("/api/algos", headers=auth, json={"name": "paper first", "source": src}).json()
    algo_id = up["algo_id"]
    view = next(a for a in client.get("/api/algos", headers=auth).json()["algos"] if a["id"] == algo_id)
    assert view["paper_first"]["paper_sessions"] == 0 and view["paper_first"]["ok"] is False
    assert view["versions"][0]["paper_sessions"] == 0 and view["versions"][0]["live_days"] == 0
    r = client.post(f"/api/algos/{algo_id}/start", headers=auth, json={"mode": "live"})
    assert r.status_code == 200, r.text
    assert r.json()["paper_first"]["ok"] is False
    lines = [e["message"] for e in ctx().db.events(limit=20, algo_id=algo_id)]
    assert any("is going on real money for" in m and "after 0 of 3 full paper sessions" in m for m in lines)
    # The run carries its version from now on.
    assert ctx().fleet.get(algo_id).version_id == up["version_id"]


def test_a_run_records_which_version_it_was(tmp_db):
    run_id = tmp_db.start_run(4242, "paper", "manual", algo_id="og", version_id=7)
    with tmp_db.conn() as c:
        assert c.execute("SELECT version_id FROM engine_runs WHERE id = ?", (run_id,)).fetchone()[0] == 7
    assert tmp_db.start_run(4243, "paper", "manual", algo_id="og") > run_id      # older callers still work


# ── staging ──────────────────────────────────────────────────────────────────

def test_a_staging_copy_refuses_real_money_everywhere(client, auth, monkeypatch):  # noqa: F811
    from app.config import settings
    from app.deps import ctx

    src = "import sys\nif __name__ == '__main__':\n    mode = '--live' in sys.argv or '--paper' in sys.argv\n"
    algo_id = client.post("/api/algos", headers=auth, json={"name": "staging probe", "source": src}).json()["algo_id"]
    monkeypatch.setattr(settings, "staging", True)
    assert client.get("/api/status", headers=auth).json()["staging"] is True
    for path, body in ((f"/api/algos/{algo_id}/start", {"mode": "live"}),
                       (f"/api/algos/{algo_id}/mode", {"mode": "live"}),
                       (f"/api/algos/{algo_id}/switch", {"confirm": True}),
                       ("/api/control/mode", {"mode": "live", "confirm": True})):
        r = client.post(path, headers=auth, json=body)
        assert r.status_code == 409, (path, r.text)
        assert "staging copy" in r.json()["detail"] and "paper only" in r.json()["detail"]
    r = client.post(f"/api/algos/{algo_id}/start", headers=auth, json={"mode": "paper"})
    assert r.status_code == 200 and r.json()["mode"] == "paper"
    assert ctx().db.algo(algo_id)["mode"] == "paper"


def test_on_staging_even_a_record_that_says_live_starts_on_paper(tmp_db, monkeypatch):
    from app.config import settings
    from app.supervisor import StagingOnly, Supervisor

    tmp_db.upsert_algo("probe", "probe")
    tmp_db.set_algo_fields("probe", mode="live")
    sup = Supervisor(tmp_db, algo_id="probe", mode_provider=lambda: "live")
    assert sup.desired_mode() == "live"
    monkeypatch.setattr(settings, "staging", True)
    assert sup.desired_mode() == "paper"
    with pytest.raises(StagingOnly, match="paper only"):
        sup.set_mode("live")
    sup.set_mode("paper")


def test_staging_is_off_unless_the_server_says_so(monkeypatch):
    from app.config import Settings

    monkeypatch.delenv("MERIDIAN_STAGING", raising=False)
    assert Settings().staging is False
    monkeypatch.setenv("MERIDIAN_STAGING", "1")
    assert Settings().staging is True
