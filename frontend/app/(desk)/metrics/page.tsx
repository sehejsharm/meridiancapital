"use client";

import { DeskMetrics } from "@/components/DeskMetrics";

/** Desk metrics on their own tab: the open position's Greeks and trail, the strategy against NIFTY, performance, the market. */
export default function MetricsPage() {
  return (
    <div className="space-y-5">
      <DeskMetrics />
    </div>
  );
}
