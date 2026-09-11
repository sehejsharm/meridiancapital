"""Crash-recovery state for the trading session.

The JSON file on disk stays authoritative (it is what lets a restarted engine
re-adopt an open position mid-session); the database copy exists only so the
dashboard can render state while the engine is down.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field

from engine.config import STATE_FILE


@dataclass
class State:
    session_date: str = ""
    week_id: str = ""
    start_equity: float = 0.0
    week_start_equity: float = 0.0
    peak_equity: float = 0.0
    trades_today: int = 0
    realised_today: float = 0.0
    realised_week: float = 0.0
    consec_losses: int = 0
    halted: bool = False
    week_halted: bool = False
    locked_profit: bool = False
    position: dict = field(default_factory=dict)

    def save(self) -> None:
        try:
            tmp = STATE_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(asdict(self), default=str), encoding="utf-8")
            tmp.replace(STATE_FILE)
        except OSError:
            pass

    def as_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def load() -> "State":
        if not STATE_FILE.exists():
            return State()
        try:
            raw = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            known = {f for f in State.__dataclass_fields__}
            return State(**{k: v for k, v in raw.items() if k in known})
        except Exception:
            return State()
