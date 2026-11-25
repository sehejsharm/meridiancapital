"""The uploaded program's exit rules, written out once more for the desk to read.

**Display and research only. Nothing here places, moves or cancels an order, and
the program never imports it.** The program's own ``effective_stop`` and
``exit_reason`` decide every real exit; this module restates them so the desk can

* show what the exit will do next on the open trade (the stop now, the next
  step up, how long until the late take-profit switches on), and
* replay each recorded live trade's price path under the v8, v9 and v10 rule
  sets, to judge them on live trades rather than on the backtest alone.

The replay follows the research harness's fill conventions (studies 2 to 7), so
its figures are comparable with the backtest:

* one price a minute, the minute's close;
* a stop sells at the close of the minute that breaches it;
* the late take-profit, once it has switched on, is a resting sale at its level
  and fills at the level; if the trade is already above the level in the minute
  it switches on, it sells at that minute's close instead;
* the position is closed at the last minute's close (15:09).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

LOT = 65


@dataclass(frozen=True)
class RuleSet:
    name: str
    stop: float = 0.45                 # the starting stop, below the entry price
    arm: float = 0.20                  # gain at which the stop goes to the entry price
    trail_from: float = 0.20           # gain at which the trail starts
    trail: float = 0.20                # the trail's distance behind the best gain (of entry); with
                                       # trail_start set, the distance it narrows to
    trail_start: float | None = None   # v18: the trail's distance at entry (TRAIL_START, 27.5%) ...
    narrow_min: int | None = None      # ... narrowing in a straight line to `trail` over these minutes held
    late_after_min: int | None = None  # late take-profit: minutes held before it switches on
    late_frac: float | None = None     # ... and the gain it sells at
    runner_at: float | None = None     # runner lock: from this gain ...
    runner_gap: float | None = None    # ... the stop rides this close behind the peak
    target_pts: float | None = None    # all-day index-point target (v6 and earlier; display only)
    force_close: str = "15:10"

    def width(self, held_min: float | None = None) -> float:
        """The trail's distance behind the best price after ``held_min`` minutes held, as the program's
        ``trail_width``: ``trail_start`` at entry narrowing to ``trail`` at ``narrow_min``. Without a
        time (or for a program before v18) it is ``trail``."""
        if self.trail_start is None or not self.narrow_min or held_min is None:
            return self.trail
        start = max(self.trail_start, self.trail)
        f = min(1.0, max(0.0, float(held_min) / self.narrow_min))
        return start + (self.trail - start) * f

    def level(self, peak: float, held_min: float | None = None) -> float:
        """The stop as a gain on the entry price (negative: below it) once the best gain is ``peak``,
        ``held_min`` minutes after entry (it only matters for v18's narrowing trail)."""
        w = self.width(held_min)
        lv = -self.stop
        if peak >= self.arm:
            lv = max(lv, 0.0)
        if peak >= self.trail_from:
            lv = max(lv, peak - w)
        if self.runner_at is not None and self.runner_gap is not None and peak >= self.runner_at:
            lv = max(lv, peak - min(self.runner_gap, w))
        return lv

    def stage(self, peak: float, held_min: float | None = None) -> str:
        if self.runner_at is not None and peak >= self.runner_at:
            return "runner lock"
        if peak >= self.trail_from and peak - self.width(held_min) > 0:
            return "trailing"
        if peak >= self.arm:
            return "break-even"
        return "initial stop"


V8 = RuleSet("v8")
V9 = RuleSet("v9", late_after_min=180, late_frac=0.40)
V10 = RuleSet("v10", late_after_min=180, late_frac=0.40, runner_at=0.90, runner_gap=0.10)
# Study 7's arm at +25%: better on the two-year backtest (+Rs 10,347, not proven) and
# clearly worse over five years (May 2021 to June 2026). Watched on live trades; not in any program.
ARM25 = RuleSet("v10, trail armed at +25% (watch only)", arm=0.25, trail_from=0.25,
                late_after_min=180, late_frac=0.40, runner_at=0.90, runner_gap=0.10)
STANDARD = (V8, V9, V10, ARM25)


def from_config(values: dict, name: str = "running program") -> RuleSet | None:
    """The rule set a program's own constants describe, or None if they are not all known."""
    try:
        stop, be, trail = float(values["STOP_FRAC"]), float(values["BE_TRIGGER"]), float(values["TRAIL_FRAC"])
    except (KeyError, TypeError, ValueError):
        return None
    late_min, late_frac = values.get("LATE_TP_AFTER_MIN"), values.get("LATE_TP_FRAC")
    runner_at, runner_gap = values.get("RUNNER_AT"), values.get("RUNNER_FRAC")
    t_start, t_min = values.get("TRAIL_START"), values.get("TRAIL_NARROW_MIN")
    narrowing = t_start is not None and t_min is not None
    return RuleSet(
        name, stop=stop, arm=be, trail_from=trail, trail=trail,
        trail_start=float(t_start) if narrowing else None, narrow_min=int(t_min) if narrowing else None,
        late_after_min=int(late_min) if late_min is not None and late_frac is not None else None,
        late_frac=float(late_frac) if late_min is not None and late_frac is not None else None,
        runner_at=float(runner_at) if runner_at is not None and runner_gap is not None else None,
        runner_gap=float(runner_gap) if runner_at is not None and runner_gap is not None else None,
        target_pts=float(values["TARGET_PTS"]) if values.get("TARGET_PTS") is not None else None,
        force_close=str(values.get("FORCE_CLOSE") or "15:10"),
    )


def _minutes(hhmm: str) -> int:
    return int(hhmm[:2]) * 60 + int(hhmm[3:5])


def charges(entry: float, exit_: float, qty: int = LOT) -> float:
    """The research harness's charge model (Angel: Rs 20 an order, exchange, SEBI, stamp, STT, GST)."""
    buy, sell = entry * qty, exit_ * qty
    brokerage = 40.0
    exch = (buy + sell) * 0.0003503
    sebi = (buy + sell) * 1e-6
    stamp = buy * 0.00003
    stt = sell * 0.001
    gst = 0.18 * (brokerage + exch + sebi)
    return brokerage + exch + sebi + stamp + stt + gst


def _stop_name(level: float) -> str:
    if level < -1e-9:
        return "STOP"
    return "BE" if level <= 1e-9 else "TRAIL"


def replay(rs: RuleSet, entry: float, entry_hhmm: str, bars: list[tuple[str, float]]) -> dict | None:
    """Where ``rs`` would have sold a trade bought at ``entry`` in the ``entry_hhmm`` minute.

    ``bars`` are (HH:MM, close) for the minutes after the entry minute, in order, up
    to the last minute before the force close. None when there is no path.
    """
    if not bars or entry <= 0:
        return None
    start = _minutes(entry_hhmm)
    peak, stop = -9.0, -9.0
    prev_level, prev_gain = None, 0.0
    for i, (hhmm, price) in enumerate(bars):
        g = price / entry - 1.0
        peak = max(peak, g)
        held = _minutes(hhmm) - start
        level = rs.late_frac if (rs.late_after_min is not None and held >= rs.late_after_min) else None
        switched_on = level is not None and prev_level is None
        at_market = switched_on and i > 0 and prev_gain >= level - 1e-12
        prev_level, prev_gain = level, g
        if level is not None and not at_market and g >= level:
            return _result(rs, entry, hhmm, entry * (1.0 + level), "LATE_TP", peak, held)
        stop = max(stop, rs.level(peak, held))
        if g <= stop:
            return _result(rs, entry, hhmm, price, _stop_name(stop), peak, held)
        if at_market:
            return _result(rs, entry, hhmm, price, "LATE_TP", peak, held)
    hhmm, price = bars[-1]
    return _result(rs, entry, hhmm, price, "EOD", peak, _minutes(hhmm) - start)


def _result(rs: RuleSet, entry: float, hhmm: str, price: float, reason: str, peak: float, held: int,
            qty: int = LOT) -> dict:
    gross = (price - entry) * qty
    ch = charges(entry, price, qty)
    return {"rule": rs.name, "exit_time": hhmm, "exit": round(price, 2), "reason": reason,
            "peak_pct": round(100 * peak, 1), "hold_min": held,
            "gross": round(gross, 2), "charges": round(ch, 2), "net": round(gross - ch, 2)}


def plan(rs: RuleSet, entry: float, peak: float, held_min: float) -> dict:
    """What the exit will do next on an open trade, for the deck's card.

    ``peak`` is the best gain so far as a fraction (the program's own figure).
    """
    level = rs.level(peak, held_min)
    w = rs.width(held_min)
    if rs.trail_start is not None and rs.narrow_min:
        follows = (f"follows the best price, {w * 100:.1f}% of the entry price behind it now "
                   f"({max(rs.trail_start, rs.trail) * 100:.1f}% at entry, narrowing to {rs.trail * 100:.0f}% "
                   f"by {rs.narrow_min} min held)")
    else:
        follows = f"follows {rs.trail * 100:.0f}% of the entry price behind the best price"
    if abs(rs.trail_from - rs.arm) < 1e-9:
        lock = max(0.0, rs.arm - w)
        to = "the entry price" if lock <= 1e-9 else f"{lock * 100:.0f}% above the entry price"
        steps = [{"key": "break_even", "label": "Break-even", "at_gain": rs.arm,
                  "effect": f"the stop moves up to {to}, then {follows}"}]
    else:
        steps = sorted([
            {"key": "break_even", "label": "Break-even", "at_gain": rs.arm,
             "effect": "the stop moves up to the entry price"},
            {"key": "trail", "label": "Trail starts", "at_gain": rs.trail_from, "effect": f"the stop {follows}"},
        ], key=lambda s: s["at_gain"])
    if rs.runner_at is not None and rs.runner_gap is not None:
        steps.append({"key": "runner", "label": "Runner lock", "at_gain": rs.runner_at,
                      "effect": f"the stop tightens to {min(rs.runner_gap, w) * 100:.0f}% behind the best price"})
    for s in steps:
        s["at_price"] = round(entry * (1.0 + s["at_gain"]), 2)
        s["reached"] = peak >= s["at_gain"] - 1e-12
    nxt = next((s for s in steps if not s["reached"]), None)
    late = None
    if rs.late_after_min is not None and rs.late_frac is not None:
        left = max(0.0, rs.late_after_min - held_min)
        late = {"after_min": rs.late_after_min, "at_gain": rs.late_frac,
                "at_price": round(entry * (1.0 + rs.late_frac), 2),
                "minutes_left": round(left, 0), "on": left <= 0}
    return {
        "rule": {k: v for k, v in asdict(rs).items() if v is not None},
        "stage": rs.stage(peak, held_min),
        "trail_now": round(w, 4),
        "stop_gain": round(level, 4),
        "stop_price": round(entry * (1.0 + level), 2),
        "locked_in": round(level, 4) if level > 0 else 0.0,
        "peak_gain": round(peak, 4),
        "steps": steps,
        "next": nxt,
        "late_tp": late,
        "index_target_pts": rs.target_pts,
        "force_close": rs.force_close,
    }
