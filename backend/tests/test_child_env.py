"""The processes the API starts are not handed the API's own secrets.

A program that prints its environment, or a traceback that dumps it, must not
leak the dashboard's signing key, PIN hash, relay secret or backup passphrase.
The acceptance gate imports untrusted code, so it gets no secret at all.
"""

from __future__ import annotations

from app import child_env

ENV = {
    "PATH": "/usr/bin", "HOME": "/var/lib/meridian", "MERIDIAN_DATA_DIR": "/var/lib/meridian",
    "MERIDIAN_TRADING_MODE": "paper", "MERIDIAN_JWT_SECRET": "j" * 48, "MERIDIAN_PASSWORD_HASH": "pbkdf2...",
    "MERIDIAN_RELAY_SECRET": "r" * 40, "MERIDIAN_SESSION_SECRET": "s" * 40,
    "MERIDIAN_BACKUP_PASSPHRASE": "p" * 30, "MERIDIAN_BACKUP_URL": "https://objectstorage.example/p/abc/",
    "MERIDIAN_BACKUP_REMOTE": "u@h:/b/", "ANGEL_API_KEY": "k", "ANGEL_CLIENT_ID": "A1", "ANGEL_PASSWORD": "1",
    "ANGEL_TOTP_SECRET": "T" * 32, "MERIDIAN_TELEGRAM_BOT_TOKEN": "1:x", "MERIDIAN_TELEGRAM_CHAT_ID": "9",
    "MERIDIAN_ALERT_WEBHOOK": "https://hook.example/x", "MERIDIAN_HEARTBEAT_URL": "https://hc.example/x",
    "SOME_API_KEY": "z",
}


def test_a_program_keeps_its_broker_credentials_but_not_the_apis_secrets():
    env = child_env.for_program(ENV)
    for name in child_env.API_ONLY:
        assert name not in env
    for name in ("ANGEL_API_KEY", "ANGEL_CLIENT_ID", "ANGEL_PASSWORD", "ANGEL_TOTP_SECRET",
                 "MERIDIAN_DATA_DIR", "MERIDIAN_TRADING_MODE", "PATH", "MERIDIAN_TELEGRAM_BOT_TOKEN"):
        assert env[name] == ENV[name]


def test_the_gate_gets_no_secret_at_all():
    env = child_env.for_gate(ENV)
    assert not [k for k in env if k.startswith("ANGEL_") or k.startswith("MERIDIAN_TELEGRAM_")]
    for name in ("MERIDIAN_ALERT_WEBHOOK", "MERIDIAN_HEARTBEAT_URL", "SOME_API_KEY", *child_env.API_ONLY):
        assert name not in env
    # What it needs to run is still there.
    assert env["PATH"] == "/usr/bin" and env["MERIDIAN_DATA_DIR"] == "/var/lib/meridian"


def test_the_supervisor_starts_programs_with_the_scrubbed_environment(tmp_path, monkeypatch):
    import subprocess

    from app import supervisor as sup_mod
    from shared.db import Database

    for k, v in ENV.items():
        if k not in ("PATH", "HOME", "MERIDIAN_DATA_DIR"):
            monkeypatch.setenv(k, v)
    seen = {}

    class FakeProc:
        pid = 424242

        def poll(self):
            return None

    def fake_popen(cmd, **kw):
        seen.update(kw["env"])
        return FakeProc()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    db = Database(tmp_path / "sup.db")
    s = sup_mod.Supervisor(db)
    monkeypatch.setattr(s, "pidfile", tmp_path / "engine.pid")
    monkeypatch.setattr(s, "outfile", tmp_path / "engine.out")
    s.refresh = lambda: None
    s.start("test")
    assert seen, "the supervisor did not start anything"
    assert "MERIDIAN_JWT_SECRET" not in seen and "MERIDIAN_RELAY_SECRET" not in seen
    assert "MERIDIAN_BACKUP_PASSPHRASE" not in seen and "MERIDIAN_PASSWORD_HASH" not in seen
    assert seen["ANGEL_API_KEY"] == "k"


def test_the_gate_process_is_started_without_secrets(monkeypatch):
    import subprocess

    from app import algo_store

    monkeypatch.setenv("ANGEL_TOTP_SECRET", "T" * 32)
    monkeypatch.setenv("MERIDIAN_JWT_SECRET", "j" * 48)
    seen = {}

    def fake_run(cmd, **kw):
        seen.update(kw.get("env") or {"<inherited>": "everything"})
        return subprocess.CompletedProcess(cmd, 0, stdout='{"passed": false, "checks": []}', stderr="")

    monkeypatch.setattr(algo_store.subprocess, "run", fake_run)
    algo_store.run_gate("x = 1\n")
    assert "<inherited>" not in seen
    assert "ANGEL_TOTP_SECRET" not in seen and "MERIDIAN_JWT_SECRET" not in seen
