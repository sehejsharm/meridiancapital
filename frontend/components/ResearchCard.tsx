"use client";

import { useEffect, useState } from "react";

import { Badge, Card, Empty, Skeleton } from "@/components/ui";
import { apiGet } from "@/lib/client-api";
import { pnlClass, signedMoney } from "@/lib/format";
import type { ResearchExecution, ResearchRules, ResearchSummary } from "@/lib/types";

/** The research recorder: what each exit rule set would have made on the recorded live trades, and how good the fills were. */
export function ResearchCard() {
  const [rules, setRules] = useState<ResearchRules | null>(null);
  const [exec, setExec] = useState<ResearchExecution | null>(null);
  const [sum, setSum] = useState<ResearchSummary | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    Promise.all([
      apiGet<ResearchRules>("/research/rules"),
      apiGet<ResearchExecution>("/research/execution"),
      apiGet<ResearchSummary>("/research"),
    ])
      .then(([r, e, s]) => { setRules(r); setExec(e); setSum(s); })
      .catch(() => setFailed(true));
  }, []);

  if (failed) {
    return (
      <Card title="Exit rules on live trades">
        <Empty>The research recorder is not on this server yet. Update the server to switch it on.</Empty>
      </Card>
    );
  }
  if (!rules || !exec || !sum) return <Card title="Exit rules on live trades"><Skeleton rows={4} /></Card>;

  const today = new Date().toISOString().slice(0, 10);
  return (
    <Card
      title="Exit rules on live trades"
      subtitle={`${sum.paths} price path${sum.paths === 1 ? "" : "s"} recorded (${sum.real_money_paths} real money), ${sum.candle_days} day${sum.candle_days === 1 ? "" : "s"} of contract prices kept`}
      action={<Badge tone={rules.enough ? "good" : "neutral"}>{rules.comparable_trades} of 100 trades</Badge>}
    >
      {rules.comparable_trades === 0 ? (
        <Empty>
          No real-money trade has a whole price path on record yet. Paths are recorded from each trade taken after the server update; a
          verdict needs 100.
        </Empty>
      ) : (
        <dl className="grid grid-cols-2 gap-3 text-xs sm:grid-cols-3">
          <div>
            <dt className="text-ink-muted">As traded</dt>
            <dd className={`text-base font-semibold tabular-nums ${pnlClass(rules.actual_net)}`}>{signedMoney(rules.actual_net)}</dd>
          </div>
          {rules.rules.map((name) => (
            <div key={name} className="min-w-0">
              <dt className="break-words text-ink-muted">{name}</dt>
              <dd className={`text-base font-semibold tabular-nums ${pnlClass(rules.totals[name]?.net)}`}>{signedMoney(rules.totals[name]?.net)}</dd>
              <dd className="text-2xs text-ink-muted">{rules.totals[name]?.wins ?? 0} of {rules.comparable_trades} won</dd>
            </div>
          ))}
        </dl>
      )}
      <p className="mt-3 text-2xs leading-relaxed text-ink-muted">{rules.note}</p>

      <h3 className="mt-5 text-2xs font-semibold uppercase tracking-[0.14em] text-ink-secondary">Fill quality</h3>
      {exec.exit_vs_last_seen.trades === 0 ? (
        <p className="mt-2 text-xs text-ink-muted">No exit has been measured yet.</p>
      ) : (
        <dl className="mt-2 grid grid-cols-2 gap-3 text-xs sm:grid-cols-3">
          <div>
            <dt className="text-ink-muted">Exit against the last price seen</dt>
            <dd className={`font-semibold tabular-nums ${pnlClass(exec.exit_vs_last_seen.average_rs)}`}>{signedMoney(exec.exit_vs_last_seen.average_rs)} a trade</dd>
            <dd className="text-2xs text-ink-muted">{exec.exit_vs_last_seen.trades} exits, {signedMoney(exec.exit_vs_last_seen.total_rs)} in all</dd>
          </div>
          {exec.exit_vs_minute_close.trades > 0 && (
            <div>
              <dt className="text-ink-muted">Exit against that minute&apos;s close</dt>
              <dd className={`font-semibold tabular-nums ${pnlClass(exec.exit_vs_minute_close.average_rs)}`}>{signedMoney(exec.exit_vs_minute_close.average_rs)} a trade</dd>
            </div>
          )}
          {exec.entry_vs_minute_close.trades > 0 && (
            <div>
              <dt className="text-ink-muted">Entry against that minute&apos;s close</dt>
              <dd className={`font-semibold tabular-nums ${pnlClass(exec.entry_vs_minute_close.average_rs)}`}>{signedMoney(exec.entry_vs_minute_close.average_rs)} a trade</dd>
            </div>
          )}
        </dl>
      )}
      <p className="mt-3 text-2xs text-ink-muted">Negative is worse than the reference price.</p>
      {sum.first_path && (
        <a
          className="mt-3 inline-block text-xs text-brand underline underline-offset-2"
          href={`/api/proxy/research/ticks.csv?from=${sum.first_path}&to=${today}`}
        >
          Download every recorded price path (CSV)
        </a>
      )}
    </Card>
  );
}
