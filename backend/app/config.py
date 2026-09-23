"""API settings. Every secret comes from the environment — nothing is defaulted
to a usable value, so a misconfigured deploy fails loudly instead of running open.
"""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path

from engine.config import DB_PATH


def _csv_env(name: str, default: str = "") -> list[str]:
    raw = os.environ.get(name, default)
    return [x.strip() for x in raw.split(",") if x.strip()]


@dataclass
class Settings:
    # auth
    jwt_secret: str = field(default_factory=lambda: os.environ.get("MERIDIAN_JWT_SECRET", ""))
    password_hash: str = field(default_factory=lambda: os.environ.get("MERIDIAN_PASSWORD_HASH", ""))
    operator: str = field(default_factory=lambda: os.environ.get("MERIDIAN_OPERATOR", "sehej"))
    # WebAuthn binds a credential to the origin the browser is on. That is the
    # dashboard's domain (Vercel), not this API's — they are different hosts, so
    # it cannot be inferred here and has to be configured.
    rp_id: str = field(default_factory=lambda: os.environ.get("MERIDIAN_RP_ID", ""))
    rp_origin: str = field(default_factory=lambda: os.environ.get("MERIDIAN_RP_ORIGIN", ""))
    token_ttl_min: int = int(os.environ.get("MERIDIAN_TOKEN_TTL_MIN", "720"))
    ws_ticket_ttl_sec: int = int(os.environ.get("MERIDIAN_WS_TICKET_TTL_SEC", "60"))

    # Shared with the dashboard's relay so it can say who is really signing in.
    # Empty disables the feature; see security.client_ip.
    relay_secret: str = field(default_factory=lambda: os.environ.get("MERIDIAN_RELAY_SECRET", ""))

    # network
    cors_origins: list[str] = field(
        default_factory=lambda: _csv_env("MERIDIAN_CORS_ORIGINS", "http://localhost:3000")
    )
    bind_host: str = field(default_factory=lambda: os.environ.get("MERIDIAN_BIND_HOST", "127.0.0.1"))
    bind_port: int = int(os.environ.get("MERIDIAN_BIND_PORT", "8080"))

    # engine supervision
    python_bin: str = field(default_factory=lambda: os.environ.get("MERIDIAN_PYTHON", ""))
    backend_dir: Path = field(default_factory=lambda: Path(__file__).resolve().parent.parent)
    default_mode: str = field(default_factory=lambda: os.environ.get("MERIDIAN_TRADING_MODE", "paper"))
    autostart_enabled: bool = field(
        default_factory=lambda: os.environ.get("MERIDIAN_AUTOSTART", "1") not in ("0", "false", "no")
    )
    restart_backoff_sec: int = int(os.environ.get("MERIDIAN_RESTART_BACKOFF_SEC", "30"))
    max_restarts_per_session: int = int(os.environ.get("MERIDIAN_MAX_RESTARTS", "5"))

    db_path: Path = field(default_factory=lambda: DB_PATH)

    def validate(self) -> list[str]:
        problems = []
        if not self.jwt_secret or len(self.jwt_secret) < 32:
            problems.append("MERIDIAN_JWT_SECRET must be set to at least 32 random characters")
        if not self.password_hash:
            problems.append(
                "MERIDIAN_PASSWORD_HASH must be set — run scripts/bootstrap_secrets.py --write"
            )
        if self.default_mode not in ("paper", "live"):
            problems.append("MERIDIAN_TRADING_MODE must be 'paper' or 'live'")
        return problems


settings = Settings()


def generate_secret() -> str:
    return secrets.token_urlsafe(48)
