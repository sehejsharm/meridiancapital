import { apiGet } from "@/lib/client-api";
import type { Algo, AlgoList, GateSummary } from "@/lib/types";

/**
 * Algorithm data, normalised at the point it arrives.
 *
 * The dashboard redeploys itself on every push; the control plane on the VM
 * only moves when someone updates it. For a while after any API change the two
 * disagree, and a page that trusts the newer shape crashes against the older
 * one — which is how the whole desk went blank. Every page reads algorithms
 * through here, so a VM a version behind degrades to a missing badge instead.
 */

type RawAlgo = Omit<Algo, "gate"> & {
  gate?: GateSummary;
  /** How control planes before the advisory gate reported the verdict. */
  promotion?: { status?: string };
};

export function normalizeAlgo(raw: RawAlgo): Algo {
  if (raw.gate && typeof raw.gate.status === "string") return raw as Algo;
  // "cleared" was passed-and-seasoned under the old promotion ladder.
  const legacy = raw.promotion?.status;
  const status = legacy === "cleared" ? "passed" : legacy || "none";
  return {
    ...raw,
    versions: raw.versions ?? [],
    gate: { status, gate_passed: status === "passed", runnable: true },
  } as Algo;
}

export async function fetchAlgos(): Promise<AlgoList> {
  const body = await apiGet<{ algos?: RawAlgo[] }>("/algos");
  return { algos: (body.algos ?? []).map(normalizeAlgo) };
}

export async function fetchAlgo(id: string): Promise<Algo> {
  return normalizeAlgo(await apiGet<RawAlgo>(`/algos/${encodeURIComponent(id)}`));
}
