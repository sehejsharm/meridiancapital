"""Tunable strategy parameters.

Every number the algorithm trades on is declared here once: its type, its safe
range, and its v11 baseline. The app edits them, they persist in SQLite, and
the supervisor injects them into the bot process as environment variables on
the next start. The algorithm reads them with the v11 values as defaults, so
an untouched install behaves exactly as the original script did.

Changes never apply to a bot that is already running — a live position must
finish under the rules it was opened with.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, Optional

from . import db

ParamType = Literal["pct", "rupees", "int", "bool", "time", "text"]


@dataclass(frozen=True)
class Param:
    key: str                      # environment variable name
    label: str
    group: str
    type: ParamType
    default: Any
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    step: Optional[float] = None
    help: str = ""
    # Params that change what the bot trades rather than how it sizes risk.
    critical: bool = False

    def coerce(self, raw: Any) -> Any:
        if self.type == "bool":
            if isinstance(raw, bool):
                return raw
            return str(raw).strip().lower() in ("1", "true", "yes", "on")
        if self.type == "int":
            return int(raw)
        if self.type in ("pct", "rupees"):
            return float(raw)
        if self.type == "time":
            text = str(raw).strip()
            hh, _, mm = text.partition(":")
            h, m = int(hh), int(mm or 0)
            if not (0 <= h <= 23 and 0 <= m <= 59):
                raise ValueError(f"{self.label}: {text} is not a valid time")
            return f"{h:02d}:{m:02d}"
        return str(raw).strip()

    def validate(self, value: Any) -> None:
        if self.type in ("pct", "rupees", "int"):
            if self.minimum is not None and value < self.minimum:
                raise ValueError(f"{self.label}: {value} is below the minimum {self.minimum}")
            if self.maximum is not None and value > self.maximum:
                raise ValueError(f"{self.label}: {value} is above the maximum {self.maximum}")

    def to_env(self, value: Any) -> str:
        if self.type == "bool":
            return "true" if value else "false"
        return str(value)


# ---------------------------------------------------------------- registry

PARAMS: list[Param] = [
    # ---- risk ladder ----
    Param("SL_PCT", "Stop loss", "Risk ladder", "pct", 0.10, 0.02, 0.50, 0.01,
          "Initial stop, as a fraction of the entry premium."),
    Param("BE_TRIGGER_PCT", "Breakeven trigger", "Risk ladder", "pct", 0.15, 0.02, 1.00, 0.01,
          "Gain at which the stop moves above the entry."),
    Param("BE_FLOOR_PCT", "Breakeven floor", "Risk ladder", "pct", 0.04, 0.00, 0.50, 0.01,
          "Where the stop sits once breakeven is locked."),
    Param("LOCK1_TRIGGER", "Lock 1 trigger", "Risk ladder", "pct", 0.25, 0.05, 2.00, 0.01),
    Param("LOCK1_FLOOR", "Lock 1 floor", "Risk ladder", "pct", 0.10, 0.00, 1.00, 0.01),
    Param("LOCK2_TRIGGER", "Lock 2 trigger", "Risk ladder", "pct", 0.40, 0.05, 3.00, 0.01),
    Param("LOCK2_FLOOR", "Lock 2 floor", "Risk ladder", "pct", 0.25, 0.00, 2.00, 0.01),
    Param("FREE_GIVEBACK", "Free-run giveback", "Risk ladder", "pct", 0.10, 0.02, 0.50, 0.01,
          "How far below the running peak the trailing stop sits."),

    # ---- limits ----
    Param("DAILY_KILL_RUPEES", "Daily kill switch", "Limits", "rupees", 3000.0, 100, 1_000_000, 100,
          "Stop trading for the day once the loss reaches this."),
    Param("MAX_TRADES_PER_DAY", "Max trades per day", "Limits", "int", 3, 1, 20, 1),
    Param("SL_SAME_DIR_COOLDOWN_MIN", "Cooldown after a stop", "Limits", "int", 15, 0, 240, 1,
          "Minutes the same direction is blocked after a stop loss."),
    Param("EXIT_SAME_DIR_COOLDOWN_MIN", "Cooldown after any exit", "Limits", "int", 3, 0, 120, 1),

    # ---- volatility gates ----
    Param("GARCH_VOL_MIN", "GARCH gate", "Volatility gates", "pct", 0.09, 0.00, 1.00, 0.01,
          "Below this forecast volatility the bot will not pay for premium."),
    Param("GARCH_TRADE3_MIN", "GARCH gate, trade 3+", "Volatility gates", "pct", 0.11, 0.00, 1.00, 0.01),
    Param("POST_SL_GARCH_MIN", "GARCH gate after a stop", "Volatility gates", "pct", 0.09, 0.00, 1.00, 0.01),
    Param("EXPIRY_DAY_GARCH_MIN", "GARCH gate on expiry day", "Volatility gates", "pct", 0.13, 0.00, 1.00, 0.01),

    # ---- filters ----
    Param("ENABLE_CHOP_FILTER", "Chop filter", "Filters", "bool", True, None, None, None,
          "Skip the whole session when the previous day was directionless."),
    Param("CHOP_BLOCK_PCT", "Chop block percentile", "Filters", "int", 15, 0, 60, 1,
          "Block days scoring in the bottom N% of the lookback."),
    Param("CHOP_LOOKBACK_DAYS", "Chop lookback", "Filters", "int", 60, 20, 250, 5),
    Param("BLOCK_EXPIRY_DAY_TRADING", "Block expiry day", "Filters", "bool", True),
    Param("USE_RSI_FILTER", "RSI confirmation", "Filters", "bool", False, None, None, None,
          "Require RSI to agree with the trend stack before entering."),
    Param("RSI_PERIOD", "RSI period", "Filters", "int", 14, 2, 100, 1),

    # ---- indicators ----
    Param("EMA_FAST", "Fast EMA", "Indicators", "int", 9, 2, 100, 1),
    Param("EMA_SLOW", "Slow EMA", "Indicators", "int", 21, 3, 200, 1),
    Param("SLOPE_BARS", "Slope lookback", "Indicators", "int", 3, 1, 30, 1,
          "Bars used to measure whether the fast EMA is rising."),
    Param("SMA_PERIOD", "Warm-up bars", "Indicators", "int", 50, 10, 300, 5,
          "Bars required before the bot will trade at all."),
    Param("ADX_PERIOD", "ADX period", "Indicators", "int", 14, 3, 100, 1),
    Param("RF_RATE", "Risk-free rate", "Indicators", "pct", 0.07, 0.0, 0.30, 0.005,
          "Used by the Black-Scholes reference price only."),

    # ---- session ----
    Param("MARKET_OPEN", "Entries open", "Session", "time", "09:40", None, None, None,
          "No entries before this."),
    Param("ENTRY_CUTOFF", "Entries close", "Session", "time", "13:30"),
    Param("FORCE_CLOSE", "Force close", "Session", "time", "15:00",
          help="Any open position is flattened at this time."),

    # ---- instrument ----
    Param("INDEX_NAME", "Index", "Instrument", "text", "NIFTY", None, None, None,
          "Underlying symbol in the Angel One scrip master.", critical=True),
    Param("INDEX_TOKEN", "Index token", "Instrument", "text", "99926000", None, None, None,
          "Angel One token for the spot index.", critical=True),
    Param("INDEX_TRADINGSYMBOL", "Index quote symbol", "Instrument", "text", "Nifty 50",
          help="The name Angel One uses for the spot quote.", critical=True),
    Param("STRIKE_STEP", "Strike step", "Instrument", "int", 50, 5, 1000, 5,
          "Distance between adjacent strikes.", critical=True),
    Param("DEFAULT_LOT_SIZE", "Fallback lot size", "Instrument", "int", 75, 1, 10_000, 1,
          "Used only if the scrip master cannot be read.", critical=True),

    # ---- execution ----
    Param("PAPER_SLIPPAGE_PCT", "Paper slippage", "Execution", "pct", 0.005, 0.0, 0.05, 0.001,
          "Simulated cost per side in paper mode."),
]

BY_KEY: dict[str, Param] = {p.key: p for p in PARAMS}
GROUPS: list[str] = list(dict.fromkeys(p.group for p in PARAMS))

V11_BASELINE: dict[str, Any] = {p.key: p.default for p in PARAMS}

KV_KEY = "strategy_overrides"
KV_PROFILES = "strategy_profiles"


# ---------------------------------------------------------------- invariants


def check_invariants(values: dict[str, Any]) -> list[str]:
    """Relationships that must hold whatever the numbers are.

    The original script asserted fixed constants, which cannot survive an
    editable strategy. These are the properties that actually matter: a ladder
    that steps the wrong way, or a stop above the entry, is a broken strategy
    at any set of values.
    """
    v = {**V11_BASELINE, **values}
    problems: list[str] = []

    if v["BE_FLOOR_PCT"] >= v["BE_TRIGGER_PCT"]:
        problems.append(
            "Breakeven floor must sit below its trigger, otherwise the stop "
            "would be placed above the current price and fire immediately.")
    if v["LOCK1_TRIGGER"] <= v["BE_TRIGGER_PCT"]:
        problems.append("Lock 1 must trigger above the breakeven trigger.")
    if v["LOCK2_TRIGGER"] <= v["LOCK1_TRIGGER"]:
        problems.append("Lock 2 must trigger above Lock 1.")
    if v["LOCK1_FLOOR"] >= v["LOCK1_TRIGGER"]:
        problems.append("Lock 1 floor must sit below its trigger.")
    if v["LOCK2_FLOOR"] >= v["LOCK2_TRIGGER"]:
        problems.append("Lock 2 floor must sit below its trigger.")
    if v["LOCK2_FLOOR"] <= v["LOCK1_FLOOR"]:
        problems.append("Lock 2 floor must be above Lock 1 floor, or the ladder loosens.")
    if v["BE_FLOOR_PCT"] > v["LOCK1_FLOOR"]:
        problems.append("Breakeven floor must not exceed Lock 1 floor.")
    if v["EMA_FAST"] >= v["EMA_SLOW"]:
        problems.append("The fast EMA period must be shorter than the slow EMA period.")

    open_t, cutoff_t, close_t = v["MARKET_OPEN"], v["ENTRY_CUTOFF"], v["FORCE_CLOSE"]
    if not (open_t < cutoff_t < close_t):
        problems.append(
            f"Session times must run in order: entries open ({open_t}) before "
            f"entries close ({cutoff_t}) before force close ({close_t}).")
    if close_t >= "15:30":
        problems.append("Force close must be before 15:30, when the exchange shuts.")

    return problems


def drift_from_v11(values: dict[str, Any]) -> dict[str, dict]:
    """Which parameters differ from the shipped v11 configuration."""
    out: dict[str, dict] = {}
    for key, baseline in V11_BASELINE.items():
        current = values.get(key, baseline)
        if current != baseline:
            out[key] = {
                "label": BY_KEY[key].label,
                "group": BY_KEY[key].group,
                "v11": baseline,
                "current": current,
                "critical": BY_KEY[key].critical,
            }
    return out


# ---------------------------------------------------------------- storage


def load_overrides() -> dict[str, Any]:
    raw = db.kv_get(KV_KEY, {}) or {}
    clean: dict[str, Any] = {}
    for key, value in raw.items():
        param = BY_KEY.get(key)
        if not param:
            continue        # a parameter that no longer exists is dropped
        try:
            clean[key] = param.coerce(value)
        except (ValueError, TypeError):
            continue
    return clean


def effective() -> dict[str, Any]:
    return {**V11_BASELINE, **load_overrides()}


def apply(updates: dict[str, Any]) -> dict[str, Any]:
    """Validate and persist. Raises ValueError with everything that is wrong."""
    current = effective()
    coerced: dict[str, Any] = {}
    errors: list[str] = []

    for key, raw in updates.items():
        param = BY_KEY.get(key)
        if not param:
            errors.append(f"Unknown parameter: {key}")
            continue
        try:
            value = param.coerce(raw)
            param.validate(value)
            coerced[key] = value
        except (ValueError, TypeError) as exc:
            errors.append(str(exc))

    if errors:
        raise ValueError("; ".join(errors))

    merged = {**current, **coerced}
    problems = check_invariants(merged)
    if problems:
        raise ValueError(" ".join(problems))

    # Store only what actually differs from the baseline, so a parameter reset
    # to its v11 value disappears rather than lingering as an override.
    overrides = {k: v for k, v in merged.items() if v != V11_BASELINE[k]}
    db.kv_set(KV_KEY, overrides)
    return merged


def reset() -> dict[str, Any]:
    db.kv_set(KV_KEY, {})
    return effective()


def as_env() -> dict[str, str]:
    """The environment the bot process is launched with."""
    return {key: BY_KEY[key].to_env(value) for key, value in effective().items()}


def describe() -> dict:
    """Everything the app needs to render the strategy editor."""
    values = effective()
    return {
        "groups": [
            {
                "name": group,
                "params": [
                    {
                        "key": p.key, "label": p.label, "type": p.type,
                        "value": values[p.key], "default": p.default,
                        "min": p.minimum, "max": p.maximum, "step": p.step,
                        "help": p.help, "critical": p.critical,
                        "modified": values[p.key] != p.default,
                    }
                    for p in PARAMS if p.group == group
                ],
            }
            for group in GROUPS
        ],
        "drift": drift_from_v11(values),
        "problems": check_invariants(values),
        "profiles": list_profiles(),
    }


# ---------------------------------------------------------------- profiles


def list_profiles() -> list[dict]:
    saved = db.kv_get(KV_PROFILES, {}) or {}
    return sorted(
        (
            {"name": name, "saved_at": entry.get("saved_at"),
             "changes": len(entry.get("overrides", {}))}
            for name, entry in saved.items()
        ),
        key=lambda x: x["saved_at"] or "",
        reverse=True,
    )


def save_profile(name: str) -> list[dict]:
    name = name.strip()
    if not name:
        raise ValueError("A profile needs a name")
    if len(name) > 40:
        raise ValueError("Profile names are limited to 40 characters")
    saved = db.kv_get(KV_PROFILES, {}) or {}
    if len(saved) >= 20 and name not in saved:
        raise ValueError("Profile limit reached (20) — delete one first")
    saved[name] = {
        "overrides": load_overrides(),
        "saved_at": datetime.now().isoformat(timespec="seconds"),
    }
    db.kv_set(KV_PROFILES, saved)
    return list_profiles()


def load_profile(name: str) -> dict[str, Any]:
    saved = db.kv_get(KV_PROFILES, {}) or {}
    entry = saved.get(name)
    if entry is None:
        raise ValueError(f"No profile named {name!r}")
    merged = {**V11_BASELINE, **(entry.get("overrides") or {})}
    problems = check_invariants(merged)
    if problems:
        raise ValueError("Saved profile is no longer valid: " + " ".join(problems))
    db.kv_set(KV_KEY, {k: v for k, v in merged.items() if v != V11_BASELINE[k]})
    return merged


def delete_profile(name: str) -> list[dict]:
    saved = db.kv_get(KV_PROFILES, {}) or {}
    saved.pop(name, None)
    db.kv_set(KV_PROFILES, saved)
    return list_profiles()
