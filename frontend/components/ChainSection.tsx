"use client";

import { useEffect, useState } from "react";

import { MarketLiquidity } from "@/components/MarketLiquidity";
import { OptionChain } from "@/components/OptionChain";
import { OptionFlow } from "@/components/OptionFlow";
import { apiGet } from "@/lib/client-api";
import type { FlowPayload } from "@/lib/flow";
import { pollWhileVisible } from "@/lib/pollWhileVisible";
import type { ChainPayload } from "@/lib/types";

const CHAIN_EVERY_MS = 15_000;
const FLOW_EVERY_MS = 30_000;   // the flow moves a minute at a time

/** Option flow, then liquidity and volume, then the chain: one chain request serves all three. */
export function ChainSection() {
  const [chain, setChain] = useState<ChainPayload | null>(null);
  const [chainError, setChainError] = useState<string | null>(null);
  const [flow, setFlow] = useState<FlowPayload | null>(null);
  const [flowError, setFlowError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    const stopChain = pollWhileVisible(() => {
      apiGet<ChainPayload>("/market/chain")
        .then((c) => { if (alive) { setChain(c); setChainError(null); } })
        .catch((e) => { if (alive) setChainError(e instanceof Error ? e.message : "chain unavailable"); });
    }, CHAIN_EVERY_MS);
    const stopFlow = pollWhileVisible(() => {
      apiGet<FlowPayload>("/market/flow")
        .then((f) => { if (alive) { setFlow(f); setFlowError(null); } })
        .catch((e) => { if (alive) setFlowError(e instanceof Error ? e.message : "not on this server yet"); });
    }, FLOW_EVERY_MS);
    return () => {
      alive = false;
      stopChain();
      stopFlow();
    };
  }, []);

  const noChain = chain && !chain.available ? chain : null;
  return (
    <>
      <OptionFlow
        flow={noChain ? { available: false, reason: noChain.reason } : flowError && !flow ? { available: false, reason: "Option flow is not on this server yet: update the server to switch it on." } : flow}
        spot={chain?.spot ?? null}
        atm={chain?.atm ?? null}
        error={null}
      />
      <MarketLiquidity chain={chain} error={chainError} />
      <OptionChain feed={{ chain, error: chainError }} />
    </>
  );
}
