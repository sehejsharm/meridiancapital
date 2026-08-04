"""Environment-driven configuration.

Every secret lives here and nowhere else. Nothing in this repo hardcodes a
credential — the bot subprocess inherits them from the parent's environment.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import time as dtime
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:  # dotenv is optional; env may be injected by the platform
    pass


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name) or default)
    except ValueError:
        return default


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name) or default)
    except ValueError:
        return default


def _hhmm(name: str, default: str) -> dtime:
    raw = (os.getenv(name) or default).strip()
    hh, _, mm = raw.partition(":")
    return dtime(int(hh), int(mm or 0))


@dataclass
class Settings:
    # --- broker ---
    angel_api_key: str = field(default_factory=lambda: os.getenv("ANGEL_API_KEY", ""))
    angel_secret_key: str = field(default_factory=lambda: os.getenv("ANGEL_SECRET_KEY", ""))
    angel_client_id: str = field(default_factory=lambda: os.getenv("ANGEL_CLIENT_ID", ""))
    angel_password: str = field(default_factory=lambda: os.getenv("ANGEL_PASSWORD", ""))
    angel_totp_secret: str = field(default_factory=lambda: os.getenv("ANGEL_TOTP_SECRET", ""))

    # --- trading ---
    paper_mode: bool = field(default_factory=lambda: _bool("PAPER_MODE", True))
    seed_capital: float = field(default_factory=lambda: _float("SEED_CAPITAL", 20000.0))
    paper_slippage_pct: float = field(default_factory=lambda: _float("PAPER_SLIPPAGE_PCT", 0.005))

    # --- schedule ---
    session_start: dtime = field(default_factory=lambda: _hhmm("SESSION_START", "09:15"))
    session_stop: dtime = field(default_factory=lambda: _hhmm("SESSION_STOP", "15:45"))
    tz: str = field(default_factory=lambda: os.getenv("TZ", "Asia/Kolkata"))
    auto_schedule: bool = field(default_factory=lambda: _bool("AUTO_SCHEDULE", True))
    skip_holidays: bool = field(default_factory=lambda: _bool("SKIP_HOLIDAYS", True))

    # --- api ---
    api_token: str = field(default_factory=lambda: os.getenv("API_TOKEN", ""))
    host: str = field(default_factory=lambda: os.getenv("HOST", "0.0.0.0"))
    port: int = field(default_factory=lambda: _int("PORT", 8000))
    data_dir: Path = field(default_factory=lambda: Path(os.getenv("DATA_DIR", "./data")).resolve())

    # --- push ---
    expo_push_token: str = field(default_factory=lambda: os.getenv("EXPO_PUSH_TOKEN", ""))
    push_on_entry: bool = field(default_factory=lambda: _bool("PUSH_ON_ENTRY", True))
    push_on_exit: bool = field(default_factory=lambda: _bool("PUSH_ON_EXIT", True))
    push_on_eod: bool = field(default_factory=lambda: _bool("PUSH_ON_EOD", True))
    push_on_kill: bool = field(default_factory=lambda: _bool("PUSH_ON_KILL", True))

    def __post_init__(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "meridian.db"

    @property
    def credentials_present(self) -> bool:
        return all([
            self.angel_api_key,
            self.angel_client_id,
            self.angel_password,
            self.angel_totp_secret,
        ])

    def missing_credentials(self) -> list[str]:
        pairs = {
            "ANGEL_API_KEY": self.angel_api_key,
            "ANGEL_CLIENT_ID": self.angel_client_id,
            "ANGEL_PASSWORD": self.angel_password,
            "ANGEL_TOTP_SECRET": self.angel_totp_secret,
        }
        return [k for k, v in pairs.items() if not v]

    def public_dict(self) -> dict:
        """Config safe to send to the phone — no secrets."""
        return {
            "paper_mode": self.paper_mode,
            "seed_capital": self.seed_capital,
            "paper_slippage_pct": self.paper_slippage_pct,
            "session_start": self.session_start.strftime("%H:%M"),
            "session_stop": self.session_stop.strftime("%H:%M"),
            "tz": self.tz,
            "auto_schedule": self.auto_schedule,
            "skip_holidays": self.skip_holidays,
            "client_id": (
                self.angel_client_id[:3] + "***" + self.angel_client_id[-2:]
                if len(self.angel_client_id) > 5 else "***"
            ),
            "credentials_present": self.credentials_present,
            "push_enabled": bool(self.expo_push_token),
        }


settings = Settings()
