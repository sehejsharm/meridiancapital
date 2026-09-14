"""Crash-recovery state for the trading session.

The JSON file on disk stays authoritative (it is what lets a restarted engine
re-adopt an open position mid-session); the database copy exists only so the
dashboard can render state while the engine is down.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from engine.config import DATA_DIR, STATE_FILE


def state_file_for(algo_id: str) -> Path:
    """Each algorithm gets its own crash-recovery file.

    Sharing one would be catastrophic with several engines running: whichever
    saved last would overwrite the others' open positions, and a restarted
    engine would re-adopt a position belonging to a different algorithm.
    The built-in keeps the original filename so an in-flight session survives
    this upgrade.
    """
    if algo_id == "gk50k":
        return STATE_FILE
    return DATA_DIR / f"gk50k_state_{algo_id}.json"


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

    def save(self, algo_id: str = "gk50k") -> None:
        path = state_file_for(algo_id)
        try:
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(asdict(self), default=str), encoding="utf-8")
            tmp.replace(path)
        except OSError:
            pass

    def as_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def load(algo_id: str = "gk50k") -> "State":
        path = state_file_for(algo_id)
        if not path.exists():
            return State()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            known = {f for f in State.__dataclass_fields__}
            return State(**{k: v for k, v in raw.items() if k in known})
        except Exception:
            return State()
