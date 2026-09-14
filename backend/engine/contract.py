"""The interface an uploaded strategy must satisfy.

An algorithm reaching this system is not a script that runs itself — it is a
set of pure decisions the supervised engine calls. That split is deliberate:
the engine owns the broker, the money, the order retries and the kill
switches, and a strategy can only answer questions about what it would like to
do. A strategy that cannot place an order cannot place a bad one.

A conforming module defines:

    NAME                str
    signal(closes)      -> "CE" | "PE" | ""   (which way to trade, if at all)
    target_strike(spot, right) -> int         (which contract)
    effective_stop(peak_gain)  -> float       (stop distance, as a fraction)
    size_position(equity, premium) -> int     (lots, 0 to decline the trade)
    guards()            -> dict               (the kill-switch thresholds)

`guards()` must return the keys in REQUIRED_GUARDS. The engine enforces them;
the strategy only declares them, so a strategy cannot opt out of having a
daily loss limit — only choose its value, within the bounds the gate checks.
"""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass
from types import ModuleType
from typing import Any

REQUIRED_CALLABLES = ("signal", "target_strike", "effective_stop", "size_position", "guards")
REQUIRED_ATTRS = ("NAME",)

REQUIRED_GUARDS = (
    "max_trades_day",
    "daily_loss_limit_rs",
    "weekly_loss_limit_rs",
    "consec_loss_halt",
    "max_drawdown_stop",
    "min_capital",
    "entry_start",
    "entry_cutoff",
    "force_close",
)


class ContractError(Exception):
    """The module does not implement the strategy interface."""


@dataclass
class LoadedStrategy:
    module: ModuleType
    name: str

    def signal(self, closes) -> str:
        return self.module.signal(closes)

    def target_strike(self, spot: float, right: str) -> int:
        return self.module.target_strike(spot, right)

    def effective_stop(self, peak_gain: float) -> float:
        return self.module.effective_stop(peak_gain)

    def size_position(self, equity: float, premium: float) -> int:
        return self.module.size_position(equity, premium)

    def guards(self) -> dict[str, Any]:
        return dict(self.module.guards())


def load_module(source: str, module_name: str) -> ModuleType:
    """Execute source as a module. Screen it with sandbox.scan first."""
    spec = importlib.util.spec_from_loader(module_name, loader=None)
    if spec is None:  # pragma: no cover - importlib always returns a spec here
        raise ContractError("could not create a module spec")
    module = importlib.util.module_from_spec(spec)
    module.__dict__["__name__"] = module_name
    try:
        exec(compile(source, f"<algo:{module_name}>", "exec"), module.__dict__)
    except Exception as exc:
        raise ContractError(f"module failed to load: {type(exc).__name__}: {exc}") from exc
    sys.modules[module_name] = module
    return module


def verify(module: ModuleType) -> LoadedStrategy:
    """Check the module implements the interface, and return a bound handle."""
    missing = [a for a in REQUIRED_ATTRS if not hasattr(module, a)]
    missing += [f"{c}()" for c in REQUIRED_CALLABLES if not callable(getattr(module, c, None))]
    if missing:
        raise ContractError("missing from the strategy interface: " + ", ".join(missing))

    name = getattr(module, "NAME")
    if not isinstance(name, str) or not name.strip():
        raise ContractError("NAME must be a non-empty string")

    try:
        g = module.guards()
    except Exception as exc:
        raise ContractError(f"guards() raised {type(exc).__name__}: {exc}") from exc
    if not isinstance(g, dict):
        raise ContractError("guards() must return a dict")
    absent = [k for k in REQUIRED_GUARDS if k not in g]
    if absent:
        raise ContractError("guards() is missing required limits: " + ", ".join(absent))

    return LoadedStrategy(module=module, name=name.strip())


def load_and_verify(source: str, module_name: str) -> LoadedStrategy:
    return verify(load_module(source, module_name))
