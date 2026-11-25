import type { ExitPlan } from "./types";

/** "₹1,234.50" without importing the formatter (this file is imported by tests). */
function rupees(v: number): string {
  return `₹${v.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function pct(v: number): string {
  const n = Math.round(v * 1000) / 10;
  return `${n > 0 ? "+" : ""}${Number.isInteger(n) ? n.toFixed(0) : n.toFixed(1)}%`;
}

export interface ExitPlanView {
  stage: string;
  stop: string;
  stopNote: string;
  next: string | null;
  nextDistance: string | null;
  late: string | null;
  close: string;
}

/**
 * The exit plan in sentences. `live` is the premium right now and `restingStop` the
 * stop order the program actually has at the exchange; when that differs from the
 * rule's level, the resting order is what is shown, because it is what will fill.
 */
export function describePlan(plan: ExitPlan, live: number | null, restingStop: number | null): ExitPlanView {
  const useResting = restingStop != null && restingStop > 0 && Math.abs(restingStop - plan.stop_price) > 0.05;
  const stopPrice = useResting ? (restingStop as number) : plan.stop_price;
  const stopNote = useResting
    ? `resting at Angel; the rules put it at ${rupees(plan.stop_price)}`
    : plan.stop_gain > 0
      ? `locks in ${pct(plan.locked_in)}`
      : plan.stop_gain === 0
        ? "at the entry price"
        : `${pct(plan.stop_gain)} from entry`;
  let next: string | null = null;
  let nextDistance: string | null = null;
  if (plan.next) {
    next = `${plan.next.label} at ${rupees(plan.next.at_price)} (${pct(plan.next.at_gain)}): ${plan.next.effect}`;
    if (live != null && live > 0) {
      const away = plan.next.at_price - live;
      nextDistance = away > 0 ? `${rupees(away)} away (${pct(away / live)})` : "reached";
    }
  }
  let late: string | null = null;
  if (plan.late_tp) {
    const t = plan.late_tp;
    late = t.on
      ? `Late take-profit is on: sells at ${rupees(t.at_price)} (${pct(t.at_gain)}) or better`
      : `Late take-profit switches on in ${Math.round(t.minutes_left)} min: then sells at ${rupees(t.at_price)} (${pct(t.at_gain)}) or better`;
  }
  return { stage: plan.stage, stop: rupees(stopPrice), stopNote, next, nextDistance, late, close: `Closed at ${plan.force_close} whatever happens` };
}

/**
 * The position panel's one line on the trail when there is no fixed target. v18's trail starts wider
 * (27.5% of entry behind the best price) and narrows to its final distance (20%) by 180 minutes held;
 * the program's own status only carries the final distance, so it said "follows 20%" from the start.
 */
export function trailSentence(trailPct: number | null, plan: ExitPlan | null | undefined): string {
  if (trailPct == null) return "No fixed target. The stop follows the best price reached.";
  const start = plan?.rule?.trail_start;
  const mins = plan?.rule?.narrow_min;
  if (start != null && mins) {
    const at = (v: number) => {
      const n = Math.round(v * 1000) / 10;
      return `${Number.isInteger(n) ? n.toFixed(0) : n.toFixed(1)}%`;
    };
    const now = plan?.trail_now != null ? ` (${at(plan.trail_now)} now)` : "";
    return `No fixed target. Once the option is up ${trailPct}%, the stop moves to the entry price, then follows the best price, ${at(Math.max(start, trailPct / 100))} of the entry price behind it at entry, narrowing to ${trailPct}% by ${mins} minutes held${now}.`;
  }
  return `No fixed target. Once the option is up ${trailPct}%, the stop moves to the entry price, then follows ${trailPct}% of the entry price behind the best price reached.`;
}
