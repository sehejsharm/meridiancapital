"""Test bootstrap. Environment must be set before engine.config is first imported."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

_TMP = Path(tempfile.mkdtemp(prefix="meridian-test-"))
os.environ["MERIDIAN_DATA_DIR"] = str(_TMP)
os.environ["MERIDIAN_DB_PATH"] = str(_TMP / "test.db")
os.environ.setdefault("MERIDIAN_JWT_SECRET", "x" * 48)
os.environ.setdefault("MERIDIAN_TRADING_MODE", "paper")
# Never let a test tick spawn a real engine against a real broker.
os.environ["MERIDIAN_AUTOSTART"] = "0"

import pytest  # noqa: E402


@pytest.fixture
def tmp_db(tmp_path):
    from shared.db import Database

    return Database(tmp_path / "bus.db")


@pytest.fixture(autouse=True)
def _reset_login_throttle():
    """Keep one test's lockout from leaking into the next.

    The limiter is process-global by design — it has to be, to be worth
    anything — so a test that deliberately triggers a lockout would otherwise
    lock out every test that logs in afterwards.
    """
    from app.security import login_throttle

    login_throttle._hits.clear()
    login_throttle._locked.clear()
    login_throttle._strikes.clear()
    yield
    login_throttle._hits.clear()
    login_throttle._locked.clear()
    login_throttle._strikes.clear()
