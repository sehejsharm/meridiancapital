"use client";

import { use, useCallback, useEffect, useState } from "react";

import { GateReport } from "@/components/GateReport";
import { RunModeDialog } from "@/components/RunModeDialog";
import { ShadowPanel } from "@/components/ShadowPanel";
import { Badge, Button, Card, Empty, Field } from "@/components/ui";
import { apiDelete, apiGet, apiPost } from "@/lib/client-api";
import { fetchAlgo } from "@/lib/algos";
import { istDateTime } from "@/lib/format";
import type { Algo, GateReport as Report } from "@/lib/types";

type Notice = { tone: "good" | "critical"; text: string } | null;

function gateLabel(status: string): string {
  if (status === "passed") return "passed every check";
  if (status === "failed") return "flagged issues — see the report below";
  return "not screened";
}

export default function AlgoDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [algo, setAlgo] = useState<Algo | null>(null);
  const [notice, setNotice] = useState<Notice>(null);
  const [busy, setBusy] = useState(false);
  const [asking, setAsking] = useState(false);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [openReport, setOpenReport] = useState<number | null>(null);
  const [reports, setReports] = useState<Record<number, Report>>({});

  const load = useCallback(async () => {
    try {
      setAlgo(await fetchAlgo(id));
    } catch (e) {
      setNotice({ tone: "critical", text: e instanceof Error ? e.message : "not found" });
    }
  }, [id]);

  useEffect(() => {
    void load();
    const t = setInterval(() => void load(), 8000);
    return () => clearInterval(t);
  }, [load]);

  const act = useCallback(
    async (fn: () => Promise<unknown>, success: string) => {
      setBusy(true);
      setNotice(null);
      try {
        await fn();
        setNotice({ tone: "good", text: success });
        await load();
      } catch (e) {
        setNotice({ tone: "critical", text: e instanceof Error ? e.message : "failed" });
      } finally {
        setBusy(false);
      }
    },
    [load],
  );

  /** Answer from the run dialog: start a stopped algorithm, or restate the
   *  mode of one that is already stopped and staying that way. */
  const pickMode = useCallback(
    async (mode: "paper" | "live") => {
      setAsking(false);
      const running = algo?.runtime.running;
      if (running) {
        await act(() => apiPost(`/algos/${id}/mode`, { mode }), `Set to ${mode}.`);
      } else {
        await act(() => apiPost(`/algos/${id}/start`, { mode }), `Started in ${mode}.`);
      }
    },
    [act, algo?.runtime.running, id],
  );

  const showReport = useCallback(
    async (versionId: number) => {
      if (openReport === versionId) {
        setOpenReport(null);
        return;
      }
      setOpenReport(versionId);
      if (reports[versionId]) return;
      try {
        const v = await apiGet<{ gate_report: string | null }>(
          `/algos/${id}/versions/${versionId}`,
        );
        if (v.gate_report) {
          setReports((r) => ({ ...r, [versionId]: JSON.parse(v.gate_report!) as Report }));
        }
      } catch {
        /* the row still shows its status */
      }
    },
    [id, openReport, reports],
  );

  if (!algo) {
    return notice ? (
      <div className="rounded-lg border border-critical/40 bg-critical/10 px-4 py-3 text-xs text-critical">
        {notice.text}
      </div>
    ) : (
      <p className="text-xs text-ink-muted">Loading…</p>
    );
  }

  const running = algo.runtime.running;
  const live = algo.mode === "live";

  return (
    <div className="space-y-5">
      <RunModeDialog
        name={algo.name}
        open={asking}
        busy={busy}
        onPick={(mode) => void pickMode(mode)}
        onCancel={() => setAsking(false)}
      />

      {notice && (
        <div
          role="status"
          className={`rounded-lg border px-4 py-3 text-xs ${
            notice.tone === "good"
              ? "border-good/40 bg-good/10 text-good"
              : "border-critical/40 bg-critical/10 text-critical"
          }`}
        >
          {notice.text}
        </div>
      )}

      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h1 className="truncate text-lg font-semibold text-ink">{algo.name}</h1>
          <p className="mt-0.5 text-2xs text-ink-muted">
            {algo.kind === "builtin" ? "Built-in build" : `uploaded · ${algo.id}`}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone={live ? "critical" : "neutral"}>{live ? "Real money" : "Paper"}</Badge>
          <Badge tone={running ? "good" : "neutral"} dot={running}>
            {running ? `running · pid ${algo.runtime.pid}` : "stopped"}
          </Badge>
        </div>
      </div>

      <div className="grid gap-5 lg:grid-cols-2">
        <Card title="Control">
          <dl className="divide-y divide-hairline">
            <Field label="Mode" value={live ? "LIVE — real orders" : "Paper — no orders"} />
            <Field label="Active version" value={algo.active ? `v${algo.active.version}` : "none"} />
            <Field label="Gate" value={gateLabel(algo.gate.status)} />
          </dl>

          <div className="mt-4 flex flex-wrap gap-2">
            {running ? (
              <Button variant="danger" disabled={busy} onClick={() => act(() => apiPost(`/algos/${id}/stop`), "Stopped.")}>
                Stop
              </Button>
            ) : (
              <Button variant="primary" disabled={busy} onClick={() => setAsking(true)}>
                Start
              </Button>
            )}
            <Button
              disabled={busy || running || live}
              onClick={() => act(() => apiPost(`/algos/${id}/mode`, { mode: "paper" }), "Set to paper.")}
            >
              Set to paper
            </Button>
            {algo.kind !== "builtin" &&
              (confirmingDelete ? (
                <>
                  <Button
                    variant="danger"
                    disabled={busy}
                    onClick={() => {
                      setConfirmingDelete(false);
                      void act(() => apiDelete(`/algos/${id}`), "Removed.");
                    }}
                  >
                    {running ? "Stop and remove" : "Really remove"}
                  </Button>
                  <Button variant="ghost" disabled={busy} onClick={() => setConfirmingDelete(false)}>
                    Keep
                  </Button>
                </>
              ) : (
                <Button variant="ghost" disabled={busy} onClick={() => setConfirmingDelete(true)}>
                  Remove
                </Button>
              ))}
          </div>
        </Card>

        <Card title="Mode" subtitle="Which money this algorithm trades">
          <p className="text-xs text-ink-secondary">
            You are asked every time you start it, and the answer sticks until you
            change it. It is currently set to{" "}
            <strong className={live ? "text-critical" : "text-ink"}>
              {live ? "real money" : "paper"}
            </strong>
            .
          </p>
          <div className="mt-4 flex flex-wrap gap-2">
            <Button
              disabled={busy || running || !live}
              onClick={() => act(() => apiPost(`/algos/${id}/mode`, { mode: "paper" }), "Set to paper.")}
            >
              Set to paper
            </Button>
            <Button
              variant="danger"
              disabled={busy || running || live || !!algo.shadow_of}
              onClick={() => setAsking(true)}
            >
              Set to real money
            </Button>
          </div>
          {running && (
            <p className="mt-3 text-2xs text-ink-muted">
              Stop it first — a running algorithm holds its mode for the session.
            </p>
          )}
        </Card>
      </div>

      {!algo.shadow_of && <ShadowPanel algoId={id} />}

      <Card title="Versions" subtitle="Every upload, with the verdict it received">
        {!algo.versions.length ? (
          <Empty>No uploaded versions — this is the built-in build.</Empty>
        ) : (
          <ul className="divide-y divide-hairline">
            {algo.versions.map((v) => (
              <li key={v.id} className="py-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="min-w-0">
                    <p className="text-xs font-medium text-ink">
                      v{v.version}
                      {algo.active_version === v.id && (
                        <span className="ml-2 text-2xs uppercase tracking-[0.1em] text-brand">active</span>
                      )}
                    </p>
                    <p className="mt-0.5 text-2xs text-ink-muted">
                      {istDateTime(v.created_ts)} · {v.uploaded_by ?? "unknown"} ·{" "}
                      <span className="font-mono">{v.sha256.slice(0, 12)}</span>
                    </p>
                  </div>
                  <div className="flex items-center gap-2">
                    <Badge
                      tone={
                        v.status === "passed" ? "good" : v.status === "failed" ? "warning" : "neutral"
                      }
                    >
                      {v.status}
                    </Badge>
                    {v.gate_report && (
                      <Button variant="ghost" onClick={() => void showReport(v.id)}>
                        {openReport === v.id ? "Hide report" : "Report"}
                      </Button>
                    )}
                    {algo.active_version !== v.id && (
                      <Button
                        disabled={busy || running}
                        onClick={() =>
                          act(
                            () => apiPost(`/algos/${id}/activate`, { version_id: v.id }),
                            `Activated v${v.version}.`,
                          )
                        }
                      >
                        Activate
                      </Button>
                    )}
                  </div>
                </div>
                {openReport === v.id && reports[v.id] && (
                  <div className="mt-3">
                    <GateReport report={reports[v.id]} />
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}
