import type { FleetAlgo } from "@/lib/types";

/**
 * Moving real money from one algorithm to another (POST /api/algos/{id}/switch).
 * Only one algorithm may trade real money on the account: switching stops the
 * one that does (a program sells what it holds on its way out) and starts this
 * one on real money.
 */
export interface SwitchPlan {
  /** The algorithms trading real money now, other than this one. */
  from: FleetAlgo[];
  /** This one already trades real money: nothing to switch. */
  alreadyLive: boolean;
  button: string;
  confirm: string;
}

export function switchPlan(algos: FleetAlgo[] | undefined, algoId: string, name: string): SwitchPlan | null {
  const all = algos ?? [];
  const me = all.find((a) => a.algo_id === algoId);
  const from = all.filter((a) => a.algo_id !== algoId && a.running && a.mode === "live");
  const alreadyLive = Boolean(me?.running && me.mode === "live");
  if (alreadyLive || from.length === 0) return null;
  const names = from.map((a) => a.name).join(", ");
  return {
    from,
    alreadyLive,
    button: `Switch real money from ${names}`,
    confirm:
      `This stops ${names} — a program sells any open position on its way out — and starts ${name} on real money. ` +
      `${names} is disarmed; ${name} is armed and starts itself at the next open.`,
  };
}
