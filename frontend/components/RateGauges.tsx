"use client";

import { Card, Empty } from "@/components/ui";
import { recordedAt as when } from "@/lib/recorded";
import type { ApiStats, PriceFeed } from "@/lib/types";

function tone(u: number): { bar: string; text: string } {
  if (u >= 0.85) return { bar: "bg-critical", text: "text-critical" };
  if (u >= 0.6) return { bar: "bg-warning", text: "text-warning" };
  return { bar: "bg-series", text: "text-ink" };
}

/**
 * Where the program's one-minute closes come from. From v6 they come from
 * Angel's live stream, which costs no requests; candles are read at start-up,
 * as a five-minute cross-check, and whenever the stream missed a minute.
 */
function FeedLine({ feed, live }: { feed: PriceFeed; live: boolean }) {
  const streaming = feed.source === "stream";
  const state = !live
    ? { dot: "bg-ink-muted", text: "text-ink-secondary", label: streaming ? "Live stream" : "Candles" }
    : streaming
      ? { dot: "bg-good", text: "text-good", label: "Live stream" }
      : feed.stream_off_reason
        ? { dot: "bg-warning", text: "text-warning", label: "Candles only" }
        : { dot: "bg-warning", text: "text-warning", label: feed.stream_connected ? "Stream checking" : "Candles, stream reconnecting" };
  const detail = streaming
    ? [
        feed.tick_age_sec !== null ? `last price ${feed.tick_age_sec < 1 ? "<1" : Math.round(feed.tick_age_sec)}s ago` : null,
        `${feed.stream_minutes.toLocaleString()} minutes from the stream`,
        feed.reconnects ? `${feed.reconnects} reconnect${feed.reconnects === 1 ? "" : "s"}` : null,
      ]
    : [feed.stream_off_reason ? `stream off: ${feed.stream_off_reason}` : feed.stream_error];
  const checks = feed.checks
    ? `${feed.checks} candle cross-check${feed.checks === 1 ? "" : "s"}` +
      (feed.check_max_diff !== null ? `, largest gap ${feed.check_max_diff.toFixed(2)} pts` : "") +
      (feed.check_refused ? `, ${feed.check_refused} refused` : "")
    : null;
  return (
    <div className="mb-4 rounded-md border border-hairline px-3 py-2">
      <div className="flex items-center justify-between gap-3">
        <span className="font-mono text-2xs uppercase tracking-[0.1em] text-ink-secondary">Price feed</span>
        <span className={`flex items-center gap-1.5 text-2xs font-semibold ${state.text}`}>
          <span className={`h-1.5 w-1.5 rounded-full ${state.dot}`} aria-hidden />
          {state.label}
        </span>
      </div>
      <p className="mt-1 text-2xs text-ink-muted">{[...detail, checks].filter(Boolean).join(" · ") || "—"}</p>
      {feed.refused > 0 && feed.last_refusal && (
        <p className="mt-1 text-2xs text-ink-muted">
          Angel refused {feed.refused} candle read{feed.refused === 1 ? "" : "s"}: “{feed.last_refusal}”
        </p>
      )}
    </div>
  );
}

/**
 * How hard the desk is leaning on Angel One's published caps.
 *
 * The figure that matters is the account's, not this engine's: the caps are per
 * API key, so several engines and a shadow all draw on one budget. Where the
 * account number is available it is the one shown, and the engine's own rate
 * sits beside it.
 */
export function RateGauges({
  api,
  recordedAt = null,
}: {
  api: ApiStats | null | undefined;
  /** Set when these figures are the last session's, not live. */
  recordedAt?: string | null;
}) {
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
        recordedAt
          ? `Last session, recorded ${when(recordedAt)} IST — nothing is calling Angel now`
          : api.shared_budget
            ? "One budget shared by every running engine"
            : "This engine only — the shared budget is unavailable"
      }
      action={
        <span className={`text-xs font-semibold tabular-nums ${tone(peak).text}`}>
          {Math.round(peak * 100)}% peak
        </span>
      }
    >
      {api.feed && <FeedLine feed={api.feed} live={!recordedAt} />}
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
