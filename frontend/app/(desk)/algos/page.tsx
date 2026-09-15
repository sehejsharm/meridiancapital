"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import { GateReport } from "@/components/GateReport";
import { Badge, Button, Card, Empty } from "@/components/ui";
import { apiGet, apiPost } from "@/lib/client-api";
import { istDateTime } from "@/lib/format";
import type { AlgoList, GateCheck, GateReport as Report } from "@/lib/types";

interface UploadResult {
  ok: boolean;
  algo_id: string;
  version: number;
  passed: boolean;
  report: Report;
  next: string;
}

export default function AlgosPage() {
  const [data, setData] = useState<AlgoList | null>(null);
  const [catalogue, setCatalogue] = useState<GateCheck[] | null>(null);
  const [name, setName] = useState("");
  const [source, setSource] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<UploadResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement | null>(null);

  const load = useCallback(async () => {
    try {
      setData(await apiGet<AlgoList>("/algos"));
    } catch (e) {
      setError(e instanceof Error ? e.message : "could not load algorithms");
    }
  }, []);

  useEffect(() => {
    void load();
    void apiGet<{ checks: GateCheck[] }>("/algos/gate-catalogue")
      .then((c) => setCatalogue(c.checks))
      .catch(() => setCatalogue(null));
  }, [load]);

  const submit = useCallback(async () => {
    if (!name.trim() || !source.trim()) return;
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const out = await apiPost<UploadResult>("/algos", { name, source });
      setResult(out);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "upload failed");
    } finally {
      setBusy(false);
    }
  }, [name, source, load]);

  const onFile = useCallback(async (file: File) => {
    setSource(await file.text());
    if (!name.trim()) setName(file.name.replace(/\.py$/i, ""));
  }, [name]);

  return (
    <div className="space-y-5">
      <div className="rounded-lg border border-warning/40 bg-warning/10 px-4 py-3 text-xs text-warning">
        <strong className="font-semibold">Uploaded code runs against a real brokerage account.</strong>{" "}
        Every submission is screened and put through the acceptance gate below before it can
        trade anything, and must then serve {data?.paper_sessions_required ?? 5} clean paper
        sessions before real money is possible. The gate proves internal coherence — it cannot
        prove a strategy is profitable.
      </div>

      {error && (
        <div role="alert" className="rounded-lg border border-critical/40 bg-critical/10 px-4 py-3 text-xs text-critical">
          {error}
        </div>
      )}

      <div className="grid gap-5 xl:grid-cols-[3fr_2fr]">
        <Card title="Upload an algorithm" subtitle="Paste a strategy module, or choose a .py file">
          <div className="space-y-3">
            <div className="flex flex-wrap items-center gap-3">
              <input
                aria-label="Algorithm name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="Strategy name"
                className="min-w-0 flex-1 rounded-md border border-hairline bg-surface-raised px-3 py-2 text-xs text-ink outline-none focus:border-brand"
              />
              <input
                ref={fileInput}
                type="file"
                accept=".py,text/x-python"
                className="hidden"
                onChange={(e) => {
                  const f = e.target.files?.[0];
                  if (f) void onFile(f);
                }}
              />
              <Button onClick={() => fileInput.current?.click()} disabled={busy}>
                Choose file
              </Button>
            </div>

            <textarea
              aria-label="Strategy source"
              value={source}
              onChange={(e) => setSource(e.target.value)}
              spellCheck={false}
              placeholder={"NAME = \"My strategy\"\n\ndef signal(closes): ...\ndef target_strike(spot, right): ...\ndef effective_stop(peak_gain): ...\ndef size_position(equity, premium): ...\ndef guards(): ..."}
              className="h-72 w-full resize-y rounded-md border border-hairline bg-surface-raised p-3 font-mono text-2xs leading-relaxed text-ink outline-none focus:border-brand"
            />

            <div className="flex flex-wrap items-center justify-between gap-3">
              <span className="text-2xs text-ink-muted">
                {new Blob([source]).size.toLocaleString()} bytes
              </span>
              <Button variant="primary" onClick={submit} disabled={busy || !name.trim() || !source.trim()}>
                {busy ? "Running the gate…" : "Upload and run the gate"}
              </Button>
            </div>
          </div>
        </Card>

        <Card
          title="What the gate checks"
          subtitle={catalogue ? `${catalogue.length} checks, every upload` : "loading…"}
        >
          {!catalogue ? (
            <Empty>Catalogue unavailable.</Empty>
          ) : (
            <ul className="-my-1 divide-y divide-hairline">
              {catalogue.map((c) => (
                <li key={c.key} className="py-2">
                  <p className="text-xs text-ink">{c.title}</p>
                  <p className="mt-0.5 text-2xs italic text-ink-muted">{c.spec}</p>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>

      {result && (
        <Card
          title={`Gate result — v${result.version}`}
          subtitle={result.next}
          action={<Badge tone={result.passed ? "good" : "critical"}>{result.passed ? "accepted" : "rejected"}</Badge>}
        >
          <GateReport report={result.report} />
        </Card>
      )}

      <Card title="Registered algorithms" subtitle={`${data?.algos.length ?? 0} in the registry`}>
        {!data?.algos.length ? (
          <Empty>Nothing registered yet.</Empty>
        ) : (
          <div className="-mx-4 overflow-x-auto px-4">
            <table className="w-full min-w-[640px] text-xs">
              <thead>
                <tr className="border-b border-hairline text-left text-2xs uppercase tracking-[0.12em] text-ink-muted">
                  <th className="py-2 pr-3 font-medium">Name</th>
                  <th className="py-2 pr-3 font-medium">Kind</th>
                  <th className="py-2 pr-3 font-medium">Mode</th>
                  <th className="py-2 pr-3 font-medium">Version</th>
                  <th className="py-2 pr-3 font-medium">Promotion</th>
                  <th className="py-2 font-medium">State</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-hairline">
                {data.algos.map((a) => (
                  <tr key={a.id}>
                    <td className="py-2.5 pr-3">
                      <Link href={`/algos/${a.id}`} className="font-medium text-ink hover:text-brand">
                        {a.name}
                      </Link>
                      <div className="text-2xs text-ink-muted">{istDateTime(a.created_ts)}</div>
                    </td>
                    <td className="py-2.5 pr-3 text-ink-secondary">{a.kind}</td>
                    <td className="py-2.5 pr-3">
                      <Badge tone={a.mode === "live" ? "critical" : "neutral"}>
                        {a.mode === "live" ? "real money" : "paper"}
                      </Badge>
                    </td>
                    <td className="py-2.5 pr-3 tabular-nums text-ink-secondary">
                      {a.active ? `v${a.active.version}` : "—"}
                    </td>
                    <td className="py-2.5 pr-3 text-ink-secondary">
                      {a.promotion.can_live
                        ? "cleared"
                        : `${a.promotion.paper_sessions}/${a.promotion.required} sessions`}
                    </td>
                    <td className="py-2.5">
                      <Badge tone={a.runtime.running ? "good" : "neutral"} dot={a.runtime.running}>
                        {a.runtime.running ? "running" : "stopped"}
                      </Badge>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}
