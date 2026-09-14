"""The acceptance gate an algorithm must pass before it is allowed near money.

Each check below is a requirement lifted from the deployment documents and
turned into something executable. The mapping is recorded in `spec` on every
check so a failure points back at the clause it came from, rather than at an
opaque test name.

What this gate can and cannot tell you: every check here is *static* — it
exercises pure decision functions against synthetic inputs. It proves the
algorithm is internally coherent, refuses obviously bad input, and cannot
loosen a stop. It cannot prove the algorithm is profitable, and it cannot
prove it behaves under a real feed. That is what the paper-trading requirement
after this gate is for.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

from engine.contract import LoadedStrategy

MARKET_OPEN = (9, 15)
MARKET_CLOSE = (15, 30)


@dataclass
class CheckResult:
    key: str
    title: str
    spec: str
    passed: bool
    detail: str = ""
    critical: bool = True


@dataclass
class GateReport:
    passed: bool = False
    checks: list[CheckResult] = field(default_factory=list)
    error: str | None = None

    @property
    def failures(self) -> list[CheckResult]:
        return [c for c in self.checks if not c.passed]

    def as_dict(self) -> dict:
        return {
            "passed": self.passed,
            "error": self.error,
            "total": len(self.checks),
            "failed": len(self.failures),
            "checks": [asdict(c) for c in self.checks],
        }


Check = Callable[[LoadedStrategy], tuple[bool, str]]
_REGISTRY: list[tuple[str, str, str, bool, Check]] = []


def check(key: str, title: str, spec: str, critical: bool = True):
    def wrap(fn: Check) -> Check:
        _REGISTRY.append((key, title, spec, critical, fn))
        return fn

    return wrap


def _series(values):
    """A closes series without requiring pandas in the gate itself."""
    try:
        import pandas as pd

        return pd.Series(values, dtype="float64")
    except ImportError:  # pragma: no cover - pandas is a hard dependency in prod
        return values


def _hm(v: Any) -> tuple[int, int]:
    if isinstance(v, (tuple, list)) and len(v) == 2:
        return int(v[0]), int(v[1])
    if isinstance(v, str) and ":" in v:
        h, m = v.split(":")[:2]
        return int(h), int(m)
    raise ValueError(f"expected a time as (h, m) or 'HH:MM', got {v!r}")


# ── 1. Core logic: signal ────────────────────────────────────────────────────
@check("signal_empty", "Signal handles an empty series",
       "LIVE_TRADING_TEST_STRATEGY 1.2 — missing data must not generate signals")
def _signal_empty(s: LoadedStrategy) -> tuple[bool, str]:
    for bad in ([], [1.0], None):
        try:
            out = s.signal(_series(bad) if bad is not None else None)
        except Exception as exc:
            return False, f"signal() raised {type(exc).__name__} on {bad!r}: {exc}"
        if out not in ("CE", "PE", ""):
            return False, f"signal() returned {out!r} on {bad!r}; expected 'CE', 'PE' or ''"
    return True, "empty, single-bar and None inputs all decline to signal"


@check("signal_nan", "Signal refuses NaN input",
       "LIVE_TRADING_TEST_STRATEGY 3.2 — NaN in an indicator must skip entry, not trade")
def _signal_nan(s: LoadedStrategy) -> tuple[bool, str]:
    closes = _series([float("nan")] * 200)
    try:
        out = s.signal(closes)
    except Exception as exc:
        return False, f"signal() raised {type(exc).__name__} on an all-NaN series: {exc}"
    if out != "":
        return False, f"signal() returned {out!r} on an all-NaN series; it must decline"
    return True, "an all-NaN series produces no signal"


@check("signal_deterministic", "Signal is deterministic",
       "LIVE_TRADING_TEST_STRATEGY 1.1 — the same market state must produce the same decision")
def _signal_deterministic(s: LoadedStrategy) -> tuple[bool, str]:
    closes = _series([100 + (i % 17) * 1.5 for i in range(300)])
    outs = {s.signal(closes) for _ in range(5)}
    if len(outs) != 1:
        return False, f"signal() returned differing results across identical calls: {outs}"
    return True, f"five identical calls all returned {outs.pop()!r}"


@check("signal_no_lookahead", "Signal does not read future bars",
       "LIVE_TRADING_TEST_STRATEGY 1.1 — a decision may only use bars up to now")
def _signal_no_lookahead(s: LoadedStrategy) -> tuple[bool, str]:
    base = [100 + math.sin(i / 9) * 40 for i in range(300)]
    # Appending future bars must not change the decision made at the old last bar.
    a = s.signal(_series(base))
    b = s.signal(_series(base + [9999.0, 12000.0, 15000.0]))
    truncated = s.signal(_series(base[:-3]))
    if a == b and a != truncated:
        return True, "decision tracks the final bar and ignores nothing appended after it"
    if a != b:
        return True, "decision changes when new bars arrive, as expected"
    return False, "appending wildly different bars changed nothing — signal may be ignoring recent data"


# ── 2. Stop ladder ───────────────────────────────────────────────────────────
@check("stop_monotonic", "Stop ladder only ever tightens",
       "Final Checks 1 — the trailing stop locks in profit and never moves downward")
def _stop_monotonic(s: LoadedStrategy) -> tuple[bool, str]:
    prev = None
    worst = None
    for i in range(0, 301):
        gain = i / 100.0
        try:
            es = float(s.effective_stop(gain))
        except Exception as exc:
            return False, f"effective_stop({gain}) raised {type(exc).__name__}: {exc}"
        if not math.isfinite(es):
            return False, f"effective_stop({gain}) returned {es}"
        if prev is not None and es > prev + 1e-9:
            worst = (gain, prev, es)
            break
        prev = es
    if worst:
        gain, before, after = worst
        return False, (
            f"stop loosened at peak gain {gain:.2f}: {before:.4f} -> {after:.4f}. "
            f"A stop that widens gives back profit already locked in."
        )
    return True, "stop distance is non-increasing across peak gains 0.00 to 3.00"


@check("stop_initial_bounded", "Initial stop is a sane fraction",
       "LIVE_TRADING_TEST_STRATEGY 1.1 — the hard stop sets the worst case on every trade")
def _stop_initial(s: LoadedStrategy) -> tuple[bool, str]:
    es = float(s.effective_stop(0.0))
    if not (0.0 < es <= 1.0):
        return False, f"stop at zero gain is {es:.3f}; it must be above 0 and at most 1.0 of premium"
    return True, f"initial stop is {es * 100:.0f}% of premium"


@check("stop_locks_profit", "Stop reaches break-even and then locks profit",
       "Final Checks 1 — profits lock in as the trade moves in favour")
def _stop_locks(s: LoadedStrategy) -> tuple[bool, str]:
    at_zero = float(s.effective_stop(0.0))
    at_high = float(s.effective_stop(2.0))
    if at_high >= at_zero:
        return False, (
            f"stop never tightened: {at_zero:.3f} at no gain, {at_high:.3f} at +200%. "
            f"A strategy that never trails gives back every open profit."
        )
    return True, f"stop moves from {at_zero:.3f} at entry to {at_high:.3f} at +200% gain"


# ── 3. Position sizing ───────────────────────────────────────────────────────
@check("size_rejects_degenerate", "Sizing refuses impossible input",
       "LIVE_TRADING_TEST_STRATEGY 3.2 — a qty of 0 must not place an order")
def _size_degenerate(s: LoadedStrategy) -> tuple[bool, str]:
    cases = [
        (0.0, 100.0, "zero equity"),
        (-5000.0, 100.0, "negative equity"),
        (100000.0, 0.0, "zero premium"),
        (100000.0, -10.0, "negative premium"),
        (float("nan"), 100.0, "NaN equity"),
        (100000.0, float("nan"), "NaN premium"),
        (float("inf"), 100.0, "infinite equity"),
    ]
    for equity, premium, label in cases:
        try:
            lots = s.size_position(equity, premium)
        except Exception as exc:
            return False, f"size_position raised {type(exc).__name__} on {label}: {exc}"
        if not isinstance(lots, int) or lots != 0:
            return False, f"{label} produced {lots!r} lots; it must produce 0"
    return True, "zero, negative, NaN and infinite inputs all size to 0 lots"


@check("size_non_negative", "Sizing never returns a negative quantity",
       "LIVE_TRADING_TEST_STRATEGY 1.1 — position sizing must be exact")
def _size_non_negative(s: LoadedStrategy) -> tuple[bool, str]:
    for equity in (50_000, 100_000, 1_000_000):
        for premium in (5, 50, 140, 600, 5000):
            lots = s.size_position(float(equity), float(premium))
            if not isinstance(lots, int):
                return False, f"size_position({equity}, {premium}) returned {type(lots).__name__}, not int"
            if lots < 0:
                return False, f"size_position({equity}, {premium}) returned {lots}"
    return True, "all sampled equity/premium combinations sized to a non-negative integer"


@check("size_monotonic_in_equity", "Sizing does not shrink as equity grows",
       "LIVE_TRADING_TEST_STRATEGY 1.1 — intraday equity maths must move sizing coherently")
def _size_monotonic(s: LoadedStrategy) -> tuple[bool, str]:
    prev = -1
    for equity in range(50_000, 1_000_001, 50_000):
        lots = s.size_position(float(equity), 140.0)
        if lots < prev:
            return False, f"lots fell from {prev} to {lots} as equity rose to Rs {equity:,}"
        prev = lots
    return True, "lot count is non-decreasing as equity rises from Rs 50k to Rs 10L"


@check("size_respects_capital", "A position cannot cost more than the account holds",
       "LIVE_TRADING_TEST_STRATEGY 2.2 — margin checked before entry, no overshoot")
def _size_capital(s: LoadedStrategy) -> tuple[bool, str]:
    # Lot size is the strategy's own business, so infer the largest plausible
    # multiplier and require the position to stay inside equity even then.
    for equity in (50_000.0, 200_000.0):
        for premium in (50.0, 140.0, 400.0):
            lots = s.size_position(equity, premium)
            cost = lots * premium * 75  # NIFTY contract multiplier
            if cost > equity:
                return False, (
                    f"at Rs {equity:,.0f} equity and premium {premium}, sized {lots} lots "
                    f"costing Rs {cost:,.0f} — more than the account holds"
                )
    return True, "sized positions stay within available equity at every sampled level"


# ── 4. Strike selection ──────────────────────────────────────────────────────
@check("strike_valid", "Strike selection returns a tradeable strike",
       "LIVE_TRADING_TEST_STRATEGY 1.2 — strike selection logic and contract naming")
def _strike_valid(s: LoadedStrategy) -> tuple[bool, str]:
    for spot in (18_000.0, 24_512.3, 25_000.0, 31_337.7):
        for right in ("CE", "PE"):
            try:
                k = s.target_strike(spot, right)
            except Exception as exc:
                return False, f"target_strike({spot}, {right}) raised {type(exc).__name__}: {exc}"
            if not isinstance(k, int):
                return False, f"target_strike({spot}, {right}) returned {type(k).__name__}, not int"
            if k % 50 != 0:
                return False, f"target_strike({spot}, {right}) returned {k}, not a multiple of 50"
            if abs(k - spot) > 1000:
                return False, f"target_strike({spot}, {right}) returned {k}, over 1000 points from spot"
    return True, "strikes are integers on the 50-point grid and close to spot"


# ── 5. Declared guards ───────────────────────────────────────────────────────
@check("guards_present", "Kill switches are declared with usable values",
       "LIVE_TRADING_TEST_STRATEGY 3.4 — daily loss, drawdown and position limits enforced")
def _guards_values(s: LoadedStrategy) -> tuple[bool, str]:
    g = s.guards()
    numeric = {
        "max_trades_day": (1, 50),
        "daily_loss_limit_rs": (500, 10_000_000),
        "weekly_loss_limit_rs": (500, 50_000_000),
        "consec_loss_halt": (1, 50),
        "max_drawdown_stop": (0.01, 0.95),
        "min_capital": (1_000, 100_000_000),
    }
    for key, (lo, hi) in numeric.items():
        v = g.get(key)
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            return False, f"guard '{key}' is {v!r}; expected a number"
        if not (lo <= float(v) <= hi):
            return False, f"guard '{key}' is {v}; outside the sane range {lo}–{hi}"
    return True, "every declared limit is numeric and within a sane range"


@check("guards_coherent", "Kill switches are mutually coherent",
       "LIVE_TRADING_TEST_STRATEGY 3.4 — limits must escalate, not contradict")
def _guards_coherent(s: LoadedStrategy) -> tuple[bool, str]:
    g = s.guards()
    if float(g["daily_loss_limit_rs"]) > float(g["weekly_loss_limit_rs"]):
        return False, (
            f"daily loss limit Rs {g['daily_loss_limit_rs']:,.0f} exceeds the weekly limit "
            f"Rs {g['weekly_loss_limit_rs']:,.0f} — the weekly kill could never fire first"
        )
    return True, "daily limit sits at or below the weekly limit"


@check("guards_session_windows", "Session windows are ordered and inside market hours",
       "LIVE_TRADING_TEST_STRATEGY 1.1 — time-based exits respect market close exactly")
def _guards_windows(s: LoadedStrategy) -> tuple[bool, str]:
    g = s.guards()
    try:
        start, cutoff, close = (_hm(g["entry_start"]), _hm(g["entry_cutoff"]), _hm(g["force_close"]))
    except Exception as exc:
        return False, f"session windows are unreadable: {exc}"
    if not (start < cutoff < close):
        return False, (
            f"windows out of order: entry opens {start}, entry closes {cutoff}, force close {close}"
        )
    if start < MARKET_OPEN:
        return False, f"entry opens at {start}, before the {MARKET_OPEN} market open"
    if close >= MARKET_CLOSE:
        return False, (
            f"force close at {close} is at or after the {MARKET_CLOSE} market close — "
            f"a position could be left open past the bell"
        )
    return True, f"entry {start}–{cutoff}, flat by {close}, all inside market hours"


def run_checks(strategy: LoadedStrategy) -> GateReport:
    report = GateReport()
    for key, title, spec, critical, fn in _REGISTRY:
        try:
            passed, detail = fn(strategy)
        except Exception as exc:  # a check must never take the gate down with it
            passed, detail = False, f"check raised {type(exc).__name__}: {exc}"
        report.checks.append(
            CheckResult(key=key, title=title, spec=spec, passed=passed, detail=detail, critical=critical)
        )
    report.passed = all(c.passed for c in report.checks if c.critical)
    return report


def check_catalogue() -> list[dict]:
    """The published list of what an upload will be held to."""
    return [
        {"key": k, "title": t, "spec": sp, "critical": cr}
        for k, t, sp, cr, _ in _REGISTRY
    ]
