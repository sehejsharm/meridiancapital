"use client";

import { Card, Empty } from "@/components/ui";
import type { ApiStats } from "@/lib/types";

function tone(u: number): { bar: string; text: string } {
  if (u >= 0.85) return { bar: "bg-critical", text: "text-critical" };
  if (u >= 0.6) return { bar: "bg-warning", text: "text-warning" };
  return { bar: "bg-series", text: "text-ink" };
}

/**
 * How hard the desk is leaning on Angel One's published caps.
 *
 * The figure that matters is the account's, not this engine's: the caps are per
 * API key, so several engines and a shadow all draw on one budget. Where the
 * account number is available it is the one shown, and the engine's own rate
 * sits beside it.
 */
export function RateGauges({ api }: { api: ApiStats | null | undefined }) {
  if (!api?.endpoints?.length) {
    return (
      <Card title="Angel One rate budget" subtitle="Requests against the published caps">
        <Empty>No engine is connected, so nothing is calling Angel One.</Empty>
      </Card>
    );
  }

  const peak = api.peak_utilisation ?? 0;

  return (
    <Card
      title="Angel One rate budget"
      subtitle={
        api.shared_budget
          ? "One budget shared by every running engine"
          : "This engine only — the shared budget is unavailable"
      }
      action={
        <span className={`text-xs font-semibold tabular-nums ${tone(peak).text}`}>
          {Math.round(peak * 100)}% peak
        </span>
      }
    >
      <ul className="flex flex-col gap-3">
        {api.endpoints.map((e) => {
          const t = tone(e.utilisation);
          return (
            <li key={e.endpoint}>
              <div className="flex items-baseline justify-between gap-3">
                <span className="font-mono text-2xs uppercase tracking-[0.1em] text-ink-secondary">
                  {e.endpoint}
                  {e.cooling_off && (
                    <span className="ml-2 text-critical">backing off</span>
                  )}
                </span>
                <span className={`text-2xs tabular-nums ${t.text}`}>
                  {e.rate_per_sec.toFixed(2)}
                  <span className="text-ink-muted"> / {e.cap_per_sec}/s</span>
                </span>
              </div>
              <div
                className="mt-1 h-1.5 overflow-hidden rounded-full bg-surface-raised"
                role="meter"
                aria-label={`${e.endpoint} rate`}
                aria-valuenow={Math.round(e.utilisation * 100)}
                aria-valuemin={0}
                aria-valuemax={100}
              >
                <div
                  className={`h-full rounded-full transition-[width] duration-500 ${t.bar}`}
                  style={{ width: `${Math.min(100, e.utilisation * 100)}%` }}
                />
              </div>
              <div className="mt-1 flex justify-between text-2xs text-ink-muted">
                <span>
                  {e.account_calls_this_second !== null
                    ? `${e.account_calls_this_second} account call${e.account_calls_this_second === 1 ? "" : "s"} this second`
                    : `${e.calls.toLocaleString()} calls this session`}
                </span>
                {e.throttled > 0 && (
                  <span className="text-warning">{e.throttled} backed off</span>
                )}
              </div>
            </li>
          );
        })}
      </ul>

      <dl className="mt-4 flex flex-wrap gap-x-6 gap-y-1 border-t border-hairline pt-3 text-2xs text-ink-muted">
        <div className="flex gap-2">
          <dt>Total calls</dt>
          <dd className="tabular-nums text-ink">{api.total_calls.toLocaleString()}</dd>
        </div>
        <div className="flex gap-2">
          <dt>Waited</dt>
          <dd className="tabular-nums text-ink">{api.waited_sec}s</dd>
        </div>
        {api.shared_budget && (
          <div className="flex gap-2">
            <dt>Yielded to other engines</dt>
            <dd className="tabular-nums text-ink">{api.shared_waited_sec}s</dd>
          </div>
        )}
      </dl>
    </Card>
  );
}
