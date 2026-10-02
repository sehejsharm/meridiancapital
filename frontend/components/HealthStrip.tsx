"use client";

import { Card } from "@/components/ui";
import { useHealth } from "@/lib/useDeskFeeds";
import { duration } from "@/lib/format";
import type { HealthCheck } from "@/lib/types";

const STATE_DOT: Record<string, string> = {
  ok: "bg-good",
  warning: "bg-warning",
  critical: "bg-critical",
  unknown: "bg-ink-muted",
};

const STATE_TEXT: Record<string, string> = {
  ok: "text-good",
  warning: "text-warning",
  critical: "text-critical",
  unknown: "text-ink-muted",
};

function Row({ check }: { check: HealthCheck }) {
  return (
    <div className="flex items-start justify-between gap-3 py-2">
      <div className="flex min-w-0 items-center gap-2">
        <span
          className={`h-1.5 w-1.5 shrink-0 rounded-full ${STATE_DOT[check.state]}`}
          aria-hidden="true"
        />
        <div className="min-w-0">
          <div className="text-xs text-ink">{check.label}</div>
          <div className="truncate text-2xs text-ink-muted">{check.detail}</div>
        </div>
      </div>
      <div className={`shrink-0 text-xs font-medium tabular-nums ${STATE_TEXT[check.state]}`}>
        {check.value === null ? "—" : check.value.toLocaleString("en-IN")}{" "}
        <span className="text-ink-muted">{check.unit}</span>
      </div>
    </div>
  );
}

const DASHBOARD_COMMIT = process.env.NEXT_PUBLIC_BUILD_COMMIT ?? "";

/**
 * Which code the server runs, beside the dashboard's own.
 *
 * Every change deploys the dashboard by itself, but the server only changes
 * when it is updated by hand — so this row is how you tell whether it was.
 */
function CodeRow({ server }: { server: string | null | undefined }) {
  const dash = DASHBOARD_COMMIT.slice(0, 7);
  const known = Boolean(server);
  const current = known && (!dash || server!.slice(0, 7) === dash);
  const state = current ? "ok" : "warning";
  const detail = !known
    ? "the server predates version stamps — update it (git pull, then install.sh)"
    : current
      ? dash
        ? "up to date with this dashboard"
        : "server version"
      : `older than this dashboard (${dash}) — update it (git pull, then install.sh)`;
  return (
    <div className="flex items-start justify-between gap-3 py-2">
      <div className="flex min-w-0 items-center gap-2">
        <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${STATE_DOT[state]}`} aria-hidden="true" />
        <div className="min-w-0">
          <div className="text-xs text-ink">Server code</div>
          <div className={`text-2xs ${current ? "text-ink-muted" : "text-warning"}`}>{detail}</div>
        </div>
      </div>
      <div className={`shrink-0 font-mono text-xs font-medium ${STATE_TEXT[state]}`}>
        {known ? server!.slice(0, 7) : "unknown"}
      </div>
    </div>
  );
}

/** Backend health on the deck: the machine the algorithms are standing on. */
export function HealthStrip() {
  const { data, error } = useHealth();

  return (
    <Card
      title="Backend health"
      subtitle={
        data ? `up ${duration(data.uptime_seconds)}` : error ? "unreachable" : "checking…"
      }
      action={
        <span
          className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-2xs font-medium uppercase tracking-[0.1em] ${
            data?.state === "ok"
              ? "border-good/40 text-good"
              : data?.state === "warning"
                ? "border-warning/50 text-warning"
                : data?.state === "critical"
                  ? "border-critical/50 text-critical"
                  : "border-hairline text-ink-muted"
          }`}
        >
          <span className={`h-1.5 w-1.5 rounded-full ${STATE_DOT[data?.state ?? "unknown"]}`} />
          {data?.state ?? "unknown"}
        </span>
      }
    >
      {error && !data ? (
        <p className="text-xs text-critical">{error}</p>
      ) : (
        <div className="divide-y divide-hairline">
          {data && <CodeRow server={data.build?.commit} />}
          {(data?.checks ?? []).map((c) => (
            <Row key={c.key} check={c} />
          ))}
          {data?.load_average && (
            <div className="flex items-center justify-between gap-3 py-2">
              <span className="text-xs text-ink-secondary">Load average</span>
              <span className="text-xs font-medium tabular-nums text-ink">
                {data.load_average.join("  ")}
              </span>
            </div>
          )}
        </div>
      )}
    </Card>
  );
}
