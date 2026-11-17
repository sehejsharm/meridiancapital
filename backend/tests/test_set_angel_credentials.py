"""The helper that puts the Angel values in meridian.env: it never prints them, keeps every other line, checks the seed."""

from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

import pyotp

spec = importlib.util.spec_from_file_location("sac", Path(__file__).resolve().parent.parent / "scripts" / "set_angel_credentials.py")
sac = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sac)

SEED = pyotp.random_base32()          # made up for the test; no real value appears here
FAKE = {"ANGEL_API_KEY": "abcDEF12", "ANGEL_CLIENT_ID": "XYZ12345", "ANGEL_PASSWORD": "4321", "ANGEL_TOTP_SECRET": SEED}
PROGRAM = "\n".join(f'if not os.environ.get("{k}", "").strip(): os.environ["{k}"] = "{v}"' for k, v in FAKE.items())
BLANKED = "\n".join(f'if not os.environ.get("{k}", "").strip(): os.environ["{k}"] = ""' for k in FAKE)


def test_values_are_checked_without_echoing_them():
    for k, v in FAKE.items():
        assert sac.problem(k, v) is None, k
    assert sac.problem("ANGEL_PASSWORD", "12a4") == "the Angel PIN is digits only"
    assert "seed" in sac.problem("ANGEL_TOTP_SECRET", "not-a-seed!")
    assert sac.problem("ANGEL_API_KEY", "") == "it is empty"
    assert sac.problem("ANGEL_CLIENT_ID", "a b") == "it contains a space or a quote"
    assert "4321" not in (sac.problem("ANGEL_PASSWORD", "4321x") or "")


def test_only_a_program_with_all_four_filled_in_counts():
    assert sac.from_source(PROGRAM) == FAKE
    assert sac.from_source(BLANKED) == {}                         # v14: nothing in the file
    assert sac.from_source(PROGRAM.rsplit("\n", 1)[0]) == {}      # three of four is not enough


def test_from_the_database_takes_the_newest_version_that_has_them(tmp_path):
    db = tmp_path / "m.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE algo_versions (id INTEGER PRIMARY KEY, algo_id TEXT, version INT, source TEXT)")
    con.execute("INSERT INTO algo_versions(algo_id, version, source) VALUES ('gk', 13, ?)", (PROGRAM,))
    con.execute("INSERT INTO algo_versions(algo_id, version, source) VALUES ('gk', 14, ?)", (BLANKED,))
    con.commit()
    con.close()
    assert sac.from_database(db) == (FAKE, "gk version 13")


def test_the_file_keeps_every_other_line_its_mode_and_nothing_is_printed(tmp_path, capsys):
    env = tmp_path / "meridian.env"
    env.write_text("# settings\nMERIDIAN_PASSWORD_HASH=abc\nANGEL_API_KEY=\nANGEL_PUBLIC_IP=1.2.3.4\n# ANGEL_PASSWORD=old comment\n")
    env.chmod(0o640)
    db = tmp_path / "m.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE algo_versions (id INTEGER PRIMARY KEY, algo_id TEXT, version INT, source TEXT)")
    con.execute("INSERT INTO algo_versions(algo_id, version, source) VALUES ('gk', 13, ?)", (PROGRAM,))
    con.commit()
    con.close()
    assert sac.main(["--from-program", "--env", str(env), "--db", str(db), "--no-restart"]) == 0
    lines = env.read_text().splitlines()
    assert lines[:2] == ["# settings", "MERIDIAN_PASSWORD_HASH=abc"]
    assert "ANGEL_API_KEY=abcDEF12" in lines and "ANGEL_PUBLIC_IP=1.2.3.4" in lines
    assert "# ANGEL_PASSWORD=old comment" in lines and "ANGEL_PASSWORD=4321" in lines   # comments are left alone
    assert sum(1 for x in lines if x.startswith("ANGEL_API_KEY=")) == 1
    assert env.stat().st_mode & 0o777 == 0o640
    out = capsys.readouterr()
    for v in FAKE.values():
        assert v not in out.out and v not in out.err
    assert "set, 8 characters" in out.out


def test_nothing_changes_when_no_program_has_them(tmp_path):
    env = tmp_path / "meridian.env"
    env.write_text("ANGEL_API_KEY=\n")
    db = tmp_path / "m.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE algo_versions (id INTEGER PRIMARY KEY, algo_id TEXT, version INT, source TEXT)")
    con.execute("INSERT INTO algo_versions(algo_id, version, source) VALUES ('gk', 14, ?)", (BLANKED,))
    con.commit()
    con.close()
    assert sac.main(["--from-program", "--env", str(env), "--db", str(db), "--no-restart"]) == 1
    assert env.read_text() == "ANGEL_API_KEY=\n"
