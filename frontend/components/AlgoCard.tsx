"use client";

import Link from "next/link";

import { Badge, Button } from "@/components/ui";
import { money, signedMoney, pnlClass } from "@/lib/format";
import type { Algo, Snapshot } from "@/lib/types";

/**
 * One algorithm on the deck.
 *
 * Mode is the loudest thing on the card. Paper and live are one click apart in
 * the control plane, and the only protection against acting on the wrong one is
 * being able to tell them apart without reading carefully.
 */
export function AlgoCard({
  algo,
  snapshot,
  busy,
  onStart,
  onStop,
  compact = false,
}: {
  algo: Algo;
  snapshot?: Snapshot | null;
  busy?: boolean;
  onStart?: () => void;
  onStop?: () => void;
  compact?: boolean;
}) {
  const live = algo.mode === "live";
  const running = algo.runtime?.running ?? false;
  const account = snapshot?.account;
  const position = snapshot?.position ?? null;

  return (
    <article
      className={`flex flex-col rounded-lg border bg-surface transition-colors ${
        live && running
          ? "border-critical/50 shadow-[0_0_0_1px_rgba(248,113,113,0.15)]"
          : "border-hairline"
      }`}
    >
      <header className="flex items-start justify-between gap-3 border-b border-hairline px-4 py-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span
              className={`h-1.5 w-1.5 shrink-0 rounded-full ${
                running ? "live-dot bg-good" : "bg-ink-muted"
              }`}
              aria-hidden="true"
            />
            <Link
              href={`/algos/${algo.id}`}
              className="-my-3 block truncate py-3 text-sm font-semibold text-ink hover:text-brand"
            >
              {algo.name}
            </Link>
          </div>
          <p className="mt-0.5 truncate text-2xs text-ink-muted">
            {algo.kind === "builtin" ? "Built-in build" : `v${algo.active?.version ?? "—"}`}
            {" · "}
            {running ? `pid ${algo.runtime.pid}` : "stopped"}
          </p>
        </div>
        <Badge tone={live ? "critical" : "neutral"}>{live ? "Real money" : "Paper"}</Badge>
      </header>

      {!compact && (
        <div className="grid grid-cols-2 gap-x-4 gap-y-3 px-4 py-3">
          <Figure label="Equity" value={account ? money(account.equity) : "—"} />
          <Figure
            label="Day P&L"
            value={account ? signedMoney(account.day_pl) : "—"}
            tone={account ? pnlClass(account.day_pl) : undefined}
          />
          <Figure
            label="Position"
            value={
              position
                ? `${position.side ?? ""} ${position.strike ?? ""} × ${position.lots ?? 0}`
                : "flat"
            }
          />
          <Figure
            label="Contract price"
            value={
              position
                ? `Rs ${(position.live_premium ?? position.entry_premium).toFixed(2)}`
                : "—"
            }
            tone={
              position?.gain_pct != null ? pnlClass(position.gain_pct) : undefined
            }
          />
        </div>
      )}

      <footer className="mt-auto flex items-center justify-between gap-3 border-t border-hairline px-4 py-2.5">
        <span className="truncate text-2xs text-ink-muted">
          {algo.gate.status === "passed"
            ? "gate passed"
            : algo.gate.status === "failed"
              ? "gate flagged issues"
              : "not screened"}
        </span>
        {running ? (
          <Button variant="danger" onClick={onStop} disabled={busy}>
            Stop
          </Button>
        ) : (
          <Button variant="default" onClick={onStart} disabled={busy}>
            Start
          </Button>
        )}
      </footer>
    </article>
  );
}

function Figure({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return (
    <div className="min-w-0">
      <div className="text-2xs uppercase tracking-[0.12em] text-ink-muted">{label}</div>
      <div className={`truncate text-sm font-semibold tabular-nums ${tone ?? "text-ink"}`}>
        {value}
      </div>
    </div>
  );
}
