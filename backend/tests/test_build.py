"""The server says which commit it runs, so the deck can tell an outdated one."""

from __future__ import annotations

from app import build
from tests.test_api import auth, client  # noqa: F401  (fixtures)


def test_the_installer_stamp_wins(tmp_path, monkeypatch):
    (tmp_path / "BUILD").write_text("e032604 2026-10-02T17:45:00+05:30\n")
    monkeypatch.setattr(build, "ROOT", tmp_path)
    build.build_info.cache_clear()
    try:
        info = build.build_info()
        assert info["commit"] == "e032604" and info["committed"] == "2026-10-02T17:45:00+05:30"
    finally:
        build.build_info.cache_clear()


def test_without_a_stamp_or_git_it_says_unknown(tmp_path, monkeypatch):
    monkeypatch.setattr(build, "ROOT", tmp_path)  # not a git checkout, no BUILD
    build.build_info.cache_clear()
    try:
        assert build.build_info()["commit"] is None
    finally:
        build.build_info.cache_clear()


def test_the_deck_health_carries_it(client, auth):  # noqa: F811
    body = client.get("/api/health/detail", headers=auth).json()
    assert {"commit", "fingerprint"} <= set(body["build"])


def _tree(root):
    (root / "app").mkdir(parents=True)
    (root / "app" / "main.py").write_text("print('hi')\n")
    (root / "shared").mkdir()
    (root / "shared" / "db.py").write_text("X = 1\n")
    (root / "requirements.txt").write_text("fastapi\n")
    return root


def test_the_fingerprint_follows_the_code_and_nothing_else(tmp_path):
    root = _tree(tmp_path / "backend")
    first = build.code_fingerprint(root)
    assert first and len(first) == 12
    # Bytecode caches, tests and other files are not the server's code.
    (root / "app" / "__pycache__").mkdir()
    (root / "app" / "__pycache__" / "main.cpython-314.pyc").write_bytes(b"\x00junk")
    (root / "tests").mkdir()
    (root / "tests" / "test_x.py").write_text("assert True\n")
    (root / "app" / "notes.md").write_text("hello\n")
    assert build.code_fingerprint(root) == first
    (root / "shared" / "db.py").write_text("X = 2\n")
    assert build.code_fingerprint(root) != first, "a code change changes it"


def test_python_and_the_dashboard_build_agree():
    """frontend/backend-fingerprint.mjs must compute the very same value."""
    import shutil
    import subprocess

    node = shutil.which("node")
    if node is None:
        import pytest

        pytest.skip("node is not installed here")
    script = (build.ROOT / "frontend" / "backend-fingerprint.mjs").as_uri()
    out = subprocess.run(
        [node, "--input-type=module", "-e",
         f"import {{ backendFingerprint }} from {script!r}; "
         f"console.log(backendFingerprint({str(build.ROOT / 'backend')!r}))"],
        capture_output=True, text=True, timeout=30, check=True,
    ).stdout.strip()
    assert out == build.code_fingerprint()
