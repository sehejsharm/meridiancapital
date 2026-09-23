"use client";

import dynamic from "next/dynamic";
import type { ComponentProps } from "react";

import type { MarkedChart as Chart } from "@/components/MarkedChart";

/**
 * The charting library is ~60 KB gzipped — a third of a page's JavaScript on
 * its own — and on a phone the chart sits well below the fold. Loading it
 * after the page is interactive gets the numbers on screen first.
 */
const Deferred = dynamic(() => import("@/components/MarkedChart").then((m) => m.MarkedChart), {
  ssr: false,
  loading: () => <div className="h-full animate-pulse rounded-md bg-surface-raised" />,
});

export function LazyMarkedChart(props: ComponentProps<typeof Chart>) {
  // Reserve the chart's height up front so the page does not jump when the
  // library arrives and draws.
  return (
    <div style={{ minHeight: props.height ?? 300 }}>
      <Deferred {...props} />
    </div>
  );
}
