"""The editable surface of the strategy.

Every value the dashboard is allowed to change lives here, with the bounds it
must stay inside. This module is the single source of truth: the API validates
against it, the dashboard renders its form from it, and the engine applies it at
startup. Adding a knob means adding one `Param` below — nothing else.

Two deliberate limits:

*   Only these parameters are editable. The instrument, the index token, the
    rate limits and the data-integrity guards are not knobs — they describe
    Angel's API and the NIFTY contract, not the strategy, and changing them
    from a web form breaks the engine rather than tuning it.
*   Bounds are hard. They are not the backtested values; they are the range
    outside which this build is known to misbehave (an account that froze, a
    stop wider than the premium, a lot count Angel will reject). The backtested
    value is `default`, and any deviation is reported as such.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from engine import config as C

Kind = Literal["int", "float", "pct", "money", "time"]


@dataclass(frozen=True)
class Param:
    key: str
    group: str
    label: str
    kind: Kind
    default: Any
    lo: float
    hi: float
    help: str
    # True when raising the value takes on more risk. The API requires an
    # explicit confirmation phrase before any of these move in that direction.
    riskier_up: bool = True
    step: float = 1.0


def _t(hm: tuple[int, int]) -> str:
    return f"{hm[0]:02d}:{hm[1]:02d}"


def _hm(s: str) -> tuple[int, int]:
    h, m = s.split(":")
    return int(h), int(m)


PARAMS: tuple[Param, ...] = (
    # ── signal ───────────────────────────────────────────────────────────────
    Param("DONCHIAN_LB", "Signal", "Donchian lookback", "int", C.DONCHIAN_LB, 20, 300,
          "Bars in the breakout channel. The backtest used 90; shorter fires more "
          "often and trades more noise, longer trades less but later.",
          riskier_up=False),
    Param("STRIKE_OFFSET", "Signal", "Strike offset", "int", C.STRIKE_OFFSET, -3, 2,
          "Strikes in the money. -1 is the Rs50k-validated ITM50 strike. Moving "
          "toward 0 and above buys cheaper, faster-decaying out-of-the-money premium.",
          riskier_up=True, step=1),
    Param("MIN_DTE", "Signal", "Min days to expiry", "int", C.MIN_DTE, 0, 10,
          "Refuse contracts expiring sooner than this. Below 2 the gamma and "
          "decay profile stops resembling the backtest.", riskier_up=False),
    Param("MAX_DTE", "Signal", "Max days to expiry", "int", C.MAX_DTE, 1, 30,
          "Refuse contracts expiring later than this.", riskier_up=True),

    # ── profit ladder ────────────────────────────────────────────────────────
    Param("TARGET_PTS", "Profit ladder", "Take-profit", "int", C.TARGET_PTS, 20, 400,
          "Hard take-profit, in index points moved in favour.", riskier_up=True, step=5),
    Param("BE_TRIGGER", "Profit ladder", "Break-even trigger", "pct", C.BE_TRIGGER, 0.05, 1.0,
          "Premium gain at which the stop jumps to break-even.",
          riskier_up=True, step=0.05),
    Param("TRAIL_FRAC", "Profit ladder", "Trail start", "pct", C.TRAIL_FRAC, 0.05, 2.0,
          "Premium gain past which the stop trails this far behind the peak.",
          riskier_up=True, step=0.05),

    # ── stop ladder ──────────────────────────────────────────────────────────
    Param("STOP_FRAC", "Stop ladder", "Hard stop", "pct", C.STOP_FRAC, 0.10, 0.90,
          "Initial stop, as a fraction of premium paid. The single most "
          "important number here: it sets the worst case on every trade.",
          riskier_up=True, step=0.05),

    # ── sizing ───────────────────────────────────────────────────────────────
    Param("DEPLOY_FRACTION", "Sizing", "Deploy fraction", "pct", C.DEPLOY_FRACTION, 0.05, 1.0,
          "Fraction of equity committed per trade. 0.40 is what the Rs50k "
          "compounding result assumed.", riskier_up=True, step=0.05),
    Param("PER_TRADE_EQUITY_CAP", "Sizing", "Per-trade equity cap", "pct",
          C.PER_TRADE_EQUITY_CAP, 0.05, 1.0,
          "A single position may never exceed this share of equity.",
          riskier_up=True, step=0.05),
    Param("PER_TRADE_RISK_RS", "Sizing", "Per-trade risk cap", "money",
          C.PER_TRADE_RISK_RS, 500, 200_000,
          "Skip any trade whose worst case (premium x lots x hard stop) exceeds this.",
          riskier_up=True, step=500),
    Param("MAX_LOTS", "Sizing", "Max lots", "int", C.MAX_LOTS, 1, 100,
          "Hard concentration cap, whatever the other maths says.", riskier_up=True),
    Param("MAX_PREMIUM", "Sizing", "Max premium", "money", C.MAX_PREMIUM, 50, 5_000,
          "Skip a contract priced above this per unit.", riskier_up=True, step=25),

    # ── kill switches ────────────────────────────────────────────────────────
    Param("MAX_TRADES_DAY", "Kill switches", "Trades per day", "int", C.MAX_TRADES_DAY, 1, 10,
          "Fixed number of entries allowed per session.", riskier_up=True),
    Param("DAILY_LOSS_LIMIT_RS", "Kill switches", "Daily loss limit", "money",
          C.DAILY_LOSS_LIMIT_RS, 1_000, 500_000,
          "Stop trading for the day at this realised loss. Keep it above the "
          "worst single trade or it fires on ordinary red days.",
          riskier_up=True, step=1_000),
    Param("WEEKLY_LOSS_LIMIT_RS", "Kill switches", "Weekly loss limit", "money",
          C.WEEKLY_LOSS_LIMIT_RS, 1_000, 1_000_000,
          "Stop trading for the week at this realised loss.", riskier_up=True, step=1_000),
    Param("CONSEC_LOSS_HALT", "Kill switches", "Consecutive losses", "int",
          C.CONSEC_LOSS_HALT, 1, 20,
          "Pause new entries for the day after this many losses in a row.",
          riskier_up=True),
    Param("DAILY_PROFIT_LOCK_RS", "Kill switches", "Daily profit lock", "money",
          C.DAILY_PROFIT_LOCK_RS, 1_000, 1_000_000,
          "Once up this much on the day, stop and bank it.", riskier_up=True, step=1_000),

    # ── portfolio guard ──────────────────────────────────────────────────────
    Param("MIN_CAPITAL", "Portfolio guard", "Minimum capital", "money",
          C.MIN_CAPITAL, 0, 10_000_000,
          "Refuse to start below this equity. 0 is no floor: sizing still skips any "
          "trade that breaches the equity or risk caps, so a small account waits "
          "rather than over-betting.", riskier_up=False, step=5_000),
    Param("MAX_DRAWDOWN_STOP", "Portfolio guard", "Max drawdown", "pct",
          C.MAX_DRAWDOWN_STOP, 0.05, 0.90,
          "Halt all new entries at this drawdown from peak equity.",
          riskier_up=True, step=0.05),

    # ── session windows ──────────────────────────────────────────────────────
    Param("ENTRY_START", "Session windows", "Entry opens", "time", _t(C.ENTRY_START), 0, 0,
          "No entry before this IST time. The backtest waited for 10:15 so the "
          "opening range had formed.", riskier_up=False),
    Param("ENTRY_CUTOFF", "Session windows", "Entry closes", "time", _t(C.ENTRY_CUTOFF), 0, 0,
          "No new entry after this IST time.", riskier_up=True),
    Param("FORCE_CLOSE", "Session windows", "Force close", "time", _t(C.FORCE_CLOSE), 0, 0,
          "Any open position is squared off at this IST time, unconditionally.",
          riskier_up=True),
)

BY_KEY: dict[str, Param] = {p.key: p for p in PARAMS}
GROUPS: tuple[str, ...] = tuple(dict.fromkeys(p.group for p in PARAMS))

# Windows must stay ordered: entry opens < entry closes < force close.
_TIME_ORDER = ("ENTRY_START", "ENTRY_CUTOFF", "FORCE_CLOSE")


class TuningError(ValueError):
    """A proposed override is outside its bounds or breaks a cross-check."""


def defaults() -> dict[str, Any]:
    return {p.key: p.default for p in PARAMS}


def coerce(key: str, raw: Any) -> Any:
    """Parse one submitted value into its stored type, or raise TuningError."""
    p = BY_KEY.get(key)
    if p is None:
        raise TuningError(f"{key} is not a tunable parameter")
    if p.kind == "time":
        s = str(raw).strip()
        try:
            h, m = _hm(s)
        except (ValueError, AttributeError):
            raise TuningError(f"{p.label}: expected HH:MM, got {raw!r}")
        if not (0 <= h < 24 and 0 <= m < 60):
            raise TuningError(f"{p.label}: {s} is not a valid time")
        return f"{h:02d}:{m:02d}"
    try:
        val = int(raw) if p.kind == "int" else float(raw)
    except (TypeError, ValueError):
        raise TuningError(f"{p.label}: expected a number, got {raw!r}")
    if not (p.lo <= val <= p.hi):
        raise TuningError(f"{p.label}: must be between {p.lo:g} and {p.hi:g} (got {val:g})")
    return val


def validate(values: dict[str, Any]) -> dict[str, Any]:
    """Coerce and cross-check a full or partial override set.

    Cross-checks catch the combinations that are individually in range but
    jointly incoherent — the ones that would otherwise only show up as a
    confusing engine refusal, or worse, as a trade.
    """
    out = {k: coerce(k, v) for k, v in values.items()}
    eff = {**defaults(), **out}

    if eff["MIN_DTE"] > eff["MAX_DTE"]:
        raise TuningError("Min days to expiry cannot exceed max days to expiry")
    if eff["BE_TRIGGER"] > eff["TRAIL_FRAC"]:
        raise TuningError(
            "Break-even trigger must not exceed trail start — the stop ladder only tightens"
        )
    if eff["DEPLOY_FRACTION"] > eff["PER_TRADE_EQUITY_CAP"]:
        raise TuningError("Deploy fraction cannot exceed the per-trade equity cap")
    if eff["DAILY_LOSS_LIMIT_RS"] > eff["WEEKLY_LOSS_LIMIT_RS"]:
        raise TuningError("Daily loss limit cannot exceed the weekly loss limit")

    times = [_hm(eff[k]) for k in _TIME_ORDER]
    if not (times[0] < times[1] < times[2]):
        raise TuningError("Session windows must run entry opens < entry closes < force close")
    if times[2] >= C.MARKET_CLOSE:
        raise TuningError(
            f"Force close must be before the {_t(C.MARKET_CLOSE)} market close"
        )
    if times[0] < C.MARKET_OPEN:
        raise TuningError(f"Entry cannot open before the {_t(C.MARKET_OPEN)} market open")
    return out


def riskier_keys(values: dict[str, Any]) -> list[str]:
    """Which of these overrides move risk up relative to the backtested default."""
    hot = []
    for k, v in values.items():
        p = BY_KEY.get(k)
        if p is None or p.kind == "time":
            continue
        if v == p.default:
            continue
        if (v > p.default) == p.riskier_up:
            hot.append(k)
    # A later entry cutoff or force close leaves positions on longer.
    for k in ("ENTRY_CUTOFF", "FORCE_CLOSE"):
        if k in values and _hm(str(values[k])) > _hm(str(BY_KEY[k].default)):
            hot.append(k)
    return sorted(hot)


def apply(values: dict[str, Any]) -> list[str]:
    """Write overrides onto engine.config. Returns the keys actually changed.

    Call this before the engine reads any strategy value. Time parameters are
    stored as "HH:MM" and converted back to the (h, m) tuples the engine uses.
    """
    changed = []
    for k, v in values.items():
        p = BY_KEY.get(k)
        if p is None:
            continue
        val = _hm(str(v)) if p.kind == "time" else v
        if getattr(C, k, None) != val:
            setattr(C, k, val)
            changed.append(k)
    return changed


def describe() -> list[dict[str, Any]]:
    """The form definition handed to the dashboard."""
    return [
        {
            "key": p.key,
            "group": p.group,
            "label": p.label,
            "kind": p.kind,
            "default": p.default,
            "min": p.lo,
            "max": p.hi,
            "step": p.step,
            "help": p.help,
            "riskier_up": p.riskier_up,
        }
        for p in PARAMS
    ]
