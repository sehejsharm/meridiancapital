"use client";

import { useState } from "react";

import { apiPost } from "@/lib/client-api";
import { useLiveFeed } from "@/lib/LiveContext";
import { recordedAt } from "@/lib/recorded";

/**
 * Every page's alarm for an engine that keeps dying. 21 Oct 2026: seven exits
 * with code 1 in 44 minutes while the live program held real money, found only
 * by reading the run history. It stays up until the operator acknowledges it.
 */
export function CrashLoopBanner() {
  const { status, refresh } = useLiveFeed();
  const [busy, setBusy] = useState<string | null>(null);
  const alerts = status?.alerts ?? [];
  if (!alerts.length) return null;

  const ack = async (key: string) => {
    setBusy(key);
    try {
      await apiPost("/reliability/ack", { key });
      refresh();
    } finally {
      setBusy(null);
    }
  };

  return (
    <div role="alert" className="relative z-20 space-y-1 border-b border-critical/60 bg-critical/20 px-4 py-2 text-xs text-critical">
      {alerts.map((a) => (
        <div key={a.key} className="mx-auto flex max-w-[2200px] flex-wrap items-center gap-x-3 gap-y-1">
          <span className="live-dot h-2 w-2 shrink-0 rounded-full bg-critical" aria-hidden="true" />
          <strong className="font-semibold uppercase tracking-[0.1em]">Crash loop</strong>
          <span>
            {a.name ?? a.algo_id} exited abnormally {a.exits} times between {recordedAt(a.first_exit)} and{" "}
            {recordedAt(a.last_exit)} IST (exit {[...new Set(a.codes)].join(", ")})
            {a.mode === "live" ? " while on real money" : ""}.
          </span>
          <a href="/reliability" className="underline underline-offset-2">Reliability</a>
          <a href="/journal" className="underline underline-offset-2">Journal</a>
          <button
            type="button"
            onClick={() => void ack(a.key)}
            disabled={busy === a.key}
            className="ml-auto rounded-md border border-critical/60 px-2.5 py-1 text-2xs font-semibold uppercase tracking-[0.1em] hover:bg-critical/20 disabled:opacity-50 touch:min-h-[40px]"
          >
            {busy === a.key ? "…" : "Acknowledge"}
          </button>
        </div>
      ))}
    </div>
  );
}
