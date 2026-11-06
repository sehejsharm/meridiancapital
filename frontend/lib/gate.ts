/**
 * What the acceptance gate says about an algorithm, in one phrase everywhere.
 * The Algorithms list said "GATE: PROGRAM" and the detail page "Gate: not
 * screened" for the same uploaded program: both meant the 15-check gate does
 * not apply to a full program.
 */
export function gateText(status: string | null | undefined, kind?: string | null): string {
  if (kind === "builtin") return "built-in engine";
  if (status === "program" || kind === "program") return "full program · gate n/a";
  if (status === "passed") return "gate passed";
  if (status === "failed") return "gate flagged issues";
  return "gate not run";
}

export function gateTone(status: string | null | undefined): "good" | "warning" | "neutral" {
  return status === "passed" ? "good" : status === "failed" ? "warning" : "neutral";
}
