"use client";

import { useCallback, useEffect, useState } from "react";

import { Badge, Button, Card, Skeleton } from "@/components/ui";
import { apiGet, apiPost } from "@/lib/client-api";
import { pollWhileVisible } from "@/lib/pollWhileVisible";
import { serverLines, type Tone } from "@/lib/server";
import type { CloseCheck, HostReport } from "@/lib/types";

const DOT: Record<Tone, string> = { good: "bg-good", warning: "bg-warning", critical: "bg-critical", neutral: "bg-ink-muted" };
const TEXT: Record<Tone, string> = { good: "text-ink", warning: "text-warning", critical: "text-critical", neutral: "text-ink-secondary" };

/** The machine and the desk's housekeeping: outside monitor, backups, restore test, after-close check. */
export function ServerCard() {
  const [host, setHost] = useState<HostReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setHost(await apiGet<HostReport>("/reliability/host"));
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "could not read the server's status");
    }
  }, []);
  useEffect(() => pollWhileVisible(() => void load(), 60_000), [load]);

  const runCheck = async () => {
    setBusy(true);
    setNote(null);
    try {
      const r = await apiPost<{ check: CloseCheck }>("/reliability/close-check/run");
      setNote(r.check.status === "ok" ? "Checked: nothing wrong." : `Checked: ${r.check.findings[0]?.text ?? "needs a look"}`);
      await load();
    } catch (e) {
      setNote(e instanceof Error ? e.message : "The check did not run.");
    } finally {
      setBusy(false);
    }
  };

  const lines = host ? serverLines(host) : [];
  const worst: Tone = lines.some((l) => l.tone === "critical") ? "critical" : lines.some((l) => l.tone === "warning") ? "warning" : "good";
  const check = host?.upkeep?.close_check;
  return (
    <Card
      title="Server and housekeeping"
      subtitle="The machine, the monitor outside it, backups and the check after each close"
      action={host ? <Badge tone={worst}>{worst === "good" ? "All well" : worst === "warning" ? "Needs a look" : "Action needed"}</Badge> : undefined}
    >
      {error && <p role="alert" className="mb-3 text-xs text-critical">{error}</p>}
      {!host ? (
        <Skeleton rows={6} />
      ) : (
        <>
          <ul className="divide-y divide-hairline text-xs">
            {lines.map((l) => (
              <li key={l.key} className="flex items-start gap-3 py-2">
                <span className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${DOT[l.tone]}`} aria-hidden="true" />
                <span className="w-32 shrink-0 text-ink-muted">{l.label}</span>
                <span className={`min-w-0 break-words ${TEXT[l.tone]}`}>{l.text}</span>
              </li>
            ))}
          </ul>
          {check && check.findings.length > 0 && (
            <ol className="mt-3 space-y-1 rounded-md border border-hairline p-3 text-xs">
              {check.findings.map((f, i) => (
                <li key={i} className={`break-words ${f.level === "critical" ? "text-critical" : "text-warning"}`}>{f.text}</li>
              ))}
            </ol>
          )}
          {worst !== "good" && (
            <p className="mt-3 break-words rounded-md border border-warning/40 bg-warning/10 px-3 py-2 text-2xs leading-relaxed text-warning">
              The outside monitor, phone alerts, backups off the VM and Angel's books for this check are switched on with
              one guided command on the VM, outside market hours (it asks for each value, hides the secret ones and tests
              them): <code className="break-all font-mono">sudo bash ~/meridiancapital/backend/deploy/configure.sh</code>
            </p>
          )}
          <div className="mt-3 flex flex-wrap items-center gap-3">
            <Button onClick={() => void runCheck()} disabled={busy}>{busy ? "Checking…" : "Run the after-close check now"}</Button>
            {note && <span className="min-w-0 break-words text-2xs text-ink-secondary">{note}</span>}
          </div>
          <p className="mt-3 text-2xs leading-relaxed text-ink-muted">
            {host.upkeep?.angel.configured
              ? "Angel's books are read after the close, once nothing is running."
              : "Angel's books are not read: the Angel settings are not in the server's meridian.env, so only the desk's own records are checked."}{" "}
            Journal lines older than {host.upkeep?.journal_keep_days ?? 365} days are archived to a file, then removed. Database{" "}
            {host.machine.storage.database_mb ?? "—"} MB, backups {host.machine.storage.backups_mb ?? "—"} MB.
          </p>
        </>
      )}
    </Card>
  );
}
