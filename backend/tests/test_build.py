"""The server says which commit it runs, so the deck can tell an outdated one."""

from __future__ import annotations

from app import build
from tests.test_api import auth, client  # noqa: F401  (fixtures)


def test_the_installer_stamp_wins(tmp_path, monkeypatch):
    (tmp_path / "BUILD").write_text("e032604 2026-10-02T17:45:00+05:30\n")
    monkeypatch.setattr(build, "ROOT", tmp_path)
    build.build_info.cache_clear()
    try:
        assert build.build_info() == {"commit": "e032604", "committed": "2026-10-02T17:45:00+05:30"}
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
    assert "commit" in body["build"]
