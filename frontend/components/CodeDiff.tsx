"use client";

import { useEffect, useState } from "react";

import { Badge, Card, Empty, Skeleton } from "@/components/ui";
import { apiGet } from "@/lib/client-api";
import { type CodeDiff as Diff, defaultPair, hunkHeader, isHidden } from "@/lib/codeDiff";
import { istDateTime } from "@/lib/format";
import type { AlgoVersion } from "@/lib/types";

const ROW = {
  " ": "",
  "+": "bg-good/10",
  "-": "bg-critical/10",
} as const;
const SIGN = { " ": "text-ink-muted", "+": "text-good", "-": "text-critical" } as const;

/**
 * What changed between two uploads of a program. The server blanks every line
 * that holds, or might hold, an Angel credential before it takes the diff, so
 * the browser only ever receives redacted code.
 */
export function CodeDiff({ algoId, versions }: { algoId: string; versions: AlgoVersion[] }) {
  const initial = defaultPair(versions);
  const [a, setA] = useState(initial?.a ?? 0);
  const [b, setB] = useState(initial?.b ?? 0);
  const [whole, setWhole] = useState(false);
  const [diff, setDiff] = useState<Diff | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!a || !b) return;
    let live = true;
    setDiff(null);
    setError(null);
    apiGet<Diff>(`/algos/${encodeURIComponent(algoId)}/diff?a=${a}&b=${b}&context=${whole ? 100000 : 3}`)
      .then((d) => live && setDiff(d))
      .catch((e) => live && setError(e instanceof Error ? e.message : "could not compare the versions"));
    return () => {
      live = false;
    };
  }, [algoId, a, b, whole]);

  if (!initial) return null;
  const sorted = [...versions].sort((x, y) => y.version - x.version);
  const select = (value: number, set: (n: number) => void, label: string) => (
    <label className="flex flex-col gap-1">
      <span className="text-2xs uppercase tracking-[0.12em] text-ink-muted">{label}</span>
      <select
        value={value}
        onChange={(e) => set(Number(e.target.value))}
        className="rounded-md border border-hairline bg-surface-raised px-3 py-2 text-xs text-ink outline-none focus:border-brand"
      >
        {sorted.map((v) => (
          <option key={v.id} value={v.id}>
            v{v.version} · {istDateTime(v.created_ts)}
          </option>
        ))}
      </select>
    </label>
  );

  return (
    <Card
      title="Compare versions"
      subtitle="The code that changed between two uploads. Lines with credentials are blanked on the server and never sent."
    >
      <div className="space-y-4">
        <div className="flex flex-wrap items-end gap-3">
          {select(a, setA, "From")}
          <button
            type="button"
            aria-label="Swap the two versions"
            onClick={() => {
              setA(b);
              setB(a);
            }}
            className="mb-0.5 rounded-md border border-hairline px-2.5 py-2 text-xs text-ink-muted hover:text-ink touch:min-h-[40px]"
          >
            ⇄
          </button>
          {select(b, setB, "To")}
          <label className="mb-2 inline-flex items-center gap-2 text-xs text-ink-secondary">
            <input type="checkbox" checked={whole} onChange={(e) => setWhole(e.target.checked)} className="accent-[color:var(--brand)]" />
            Whole file
          </label>
        </div>

        {error ? (
          <p role="alert" className="text-xs text-critical">{error}</p>
        ) : !diff ? (
          <Skeleton rows={6} />
        ) : (
          <>
            <div className="flex flex-wrap items-center gap-2 text-2xs text-ink-muted">
              <Badge tone="good">+{diff.added}</Badge>
              <Badge tone="critical">−{diff.removed}</Badge>
              <span>
                v{diff.a.version} → v{diff.b.version} · {diff.lines.b} lines · {diff.hidden.b} credential line
                {diff.hidden.b === 1 ? "" : "s"} hidden
              </span>
            </div>
            {diff.identical ? (
              <Empty>
                No differences in the code shown. Lines holding credentials are hidden on both sides and not compared.
              </Empty>
            ) : (
              <div className="max-h-[32rem] overflow-auto rounded-md border border-hairline">
                <table className="w-full border-collapse font-mono text-[11px] leading-5">
                  <tbody>
                    {diff.hunks.map((h, hi) => (
                      <HunkRows key={hi} hunk={h} showHeader={!whole} />
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </>
        )}
      </div>
    </Card>
  );
}

function HunkRows({ hunk, showHeader }: { hunk: Diff["hunks"][number]; showHeader: boolean }) {
  return (
    <>
      {showHeader && (
        <tr className="bg-surface-raised text-ink-muted">
          <td colSpan={4} className="px-3 py-1">{hunkHeader(hunk)}</td>
        </tr>
      )}
      {hunk.lines.map((l, i) => {
        const hidden = isHidden(l.text);
        return (
          <tr key={i} className={ROW[l.kind]}>
            <td className="w-10 select-none px-2 text-right align-top text-ink-muted">{l.a ?? ""}</td>
            <td className="w-10 select-none px-2 text-right align-top text-ink-muted">{l.b ?? ""}</td>
            <td className={`w-4 select-none align-top ${SIGN[l.kind]}`}>{l.kind === " " ? "" : l.kind === "-" ? "−" : "+"}</td>
            <td className={`whitespace-pre pr-4 align-top ${hidden ? "italic text-ink-muted" : "text-ink"}`}>{l.text}</td>
          </tr>
        );
      })}
    </>
  );
}
