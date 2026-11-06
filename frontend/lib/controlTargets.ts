import type { FleetAlgo } from "@/lib/types";

/**
 * Which running engine each in-session control actually reaches.
 *
 * Halt, Resume, Clear weekly kill and Flatten travel through the command queue,
 * which only the built-in engine reads. An uploaded program reads no commands:
 * those buttons never reached the program trading real money, and the page
 * said only "for the built-in engine" — easy to miss mid-session. What does
 * reach a program today is a stop (SIGINT, on which it sells any open position
 * through its own exit path and ends its day) and the emergency stop.
 */
export interface ControlTarget {
  algoId: string;
  name: string;
  kind: "builtin" | "uploaded";
  running: boolean;
  mode: "paper" | "live";
  /** Halt / Resume / Clear weekly kill / Flatten reach it. */
  commands: boolean;
  /** What to use instead when they do not. */
  instead: string | null;
}

export function controlTargets(algos: FleetAlgo[] | undefined): ControlTarget[] {
  return (algos ?? []).map((a) => ({
    algoId: a.algo_id,
    name: a.name,
    kind: a.kind,
    running: a.running,
    mode: a.mode,
    commands: a.kind === "builtin",
    instead:
      a.kind === "builtin"
        ? null
        : "Stop on its card (it sells any open position, then stops for the day) or Stop everything",
  }));
}

/** The running engines these controls do not reach — the ones to warn about. */
export function unreachedRunning(targets: ControlTarget[]): ControlTarget[] {
  return targets.filter((t) => t.running && !t.commands);
}

/** "Applies to" line for the in-session controls. */
export function appliesTo(targets: ControlTarget[]): string {
  const builtin = targets.find((t) => t.kind === "builtin");
  const name = builtin?.name ?? "the built-in engine";
  if (!builtin?.running) return `Applies to: ${name} (built-in) — not running, so these are off`;
  return `Applies to: ${name} (built-in), ${builtin.mode === "live" ? "on real money" : "on paper"}`;
}
