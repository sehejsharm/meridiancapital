import type { FleetAlgo } from "@/lib/types";

/**
 * What each in-session control does to each engine.
 *
 * The built-in engine reads a command queue: Halt, Resume, Clear weekly kill
 * and Flatten act on it within seconds and it keeps running. An uploaded
 * program reads no commands; what reaches it is Ctrl-C, on which it sells any
 * open position through its own exit path and exits. So for a program
 * (the operator's choice, 7 Nov 2026):
 *   Flatten - stops it: it sells what it holds and stays off for today.
 *   Halt    - stops it, only while it is confirmed flat.
 *   Resume  - starts it again (asks paper or real money).
 *   Clear weekly kill - not available.
 * Both stops keep it armed for the next session.
 */
export interface ControlTarget {
  algoId: string;
  name: string;
  kind: "builtin" | "uploaded";
  running: boolean;
  mode: "paper" | "live";
  pid: number | null;
  halt: string;
  flatten: string;
  resume: string;
  clearWeek: string | null; // null: not available
}

export function controlTargets(algos: FleetAlgo[] | undefined): ControlTarget[] {
  return (algos ?? []).map((a) =>
    a.kind === "builtin"
      ? {
          algoId: a.algo_id, name: a.name, kind: a.kind, running: a.running, mode: a.mode, pid: a.pid,
          halt: "No new entries today; it keeps managing any open trade.",
          flatten: "Sends a market exit for the open position and halts further entries.",
          resume: "Re-arms entries.",
          clearWeek: "Clears the weekly kill so it can trade again this week.",
        }
      : {
          algoId: a.algo_id, name: a.name, kind: a.kind, running: a.running, mode: a.mode, pid: a.pid,
          halt: "Stops it for the rest of today — only while it is flat (holding, use Flatten). Stays armed for the next session.",
          flatten: "Stops it with Ctrl-C: it sells any open position on its way out and stays off for today. Stays armed for the next session.",
          resume: "Starts it again; you choose paper or real money.",
          clearWeek: null,
        },
  );
}

/** The engine the controls should open on: one trading real money, else one running, else the built-in. */
export function defaultTarget(targets: ControlTarget[]): string | null {
  const pick =
    targets.find((t) => t.running && t.mode === "live") ??
    targets.find((t) => t.running) ??
    targets.find((t) => t.kind === "builtin") ??
    targets[0];
  return pick?.algoId ?? null;
}

/** "Applies to" line for the in-session controls. */
export function appliesTo(t: ControlTarget | undefined): string {
  if (!t) return "Applies to: —";
  const state = !t.running ? "not running" : t.mode === "live" ? "running on real money" : "running on paper";
  return `Applies to: ${t.name} (${t.kind === "builtin" ? "built-in engine" : "uploaded program"}) — ${state}`;
}
