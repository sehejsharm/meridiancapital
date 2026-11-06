"use client";

import { money, signedMoney } from "@/lib/format";
import { useCountUp } from "@/lib/useCountUp";

/**
 * A money figure that glides to its new value instead of swapping. The hook
 * snaps outright on a large move, so the motion never understates how fast the
 * money actually moved, and the figure always lands on the real one.
 */
export function CountUpMoney({ value, signed = false }: { value: number | null; signed?: boolean }) {
  const shown = useCountUp(value);
  return <>{signed ? signedMoney(shown) : money(shown)}</>;
}
