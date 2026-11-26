"use client";

import { useCallback, useEffect, useState } from "react";

import { Badge, Button, Card, Empty } from "@/components/ui";
import { ClientApiError, apiDelete, apiGet, apiPost, signOut } from "@/lib/client-api";
import { dateTimeLabel } from "@/lib/format";
import { pollWhileVisible } from "@/lib/pollWhileVisible";

interface DeviceRow {
  id: string;
  label: string;
  status: string;
  approved_ms: number | null;
  approved_by: string | null;
  approved_via: string | null;
  last_seen_ms: number | null;
  last_ip: string | null;
  first_ip: string | null;
  created_ms: number;
  current: boolean;
  expires_in?: number;
}

interface Listing {
  approved: DeviceRow[];
  pending: DeviceRow[];
}

/** Epoch milliseconds as the desk's IST date and time. */
function ist(ms: number | null | undefined): string {
  if (!ms) return "—";
  return dateTimeLabel(new Date(ms + 330 * 60_000).toISOString().slice(0, 16));
}

/**
 * Who can get in. A device signs in only once an operator has approved it here
 * (or on the server), by typing the code the device shows after the right PIN.
 */
export function DevicesCard() {
  const [listing, setListing] = useState<Listing | null>(null);
  const [unsupported, setUnsupported] = useState(false);
  const [note, setNote] = useState<{ tone: "good" | "critical"; text: string } | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [codes, setCodes] = useState<Record<string, string>>({});
  const [confirming, setConfirming] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setListing(await apiGet<Listing>("/auth/devices"));
      setUnsupported(false);
    } catch (e) {
      if (e instanceof ClientApiError && e.status === 404) setUnsupported(true);
    }
  }, []);

  useEffect(() => pollWhileVisible(() => void load(), 10_000), [load]);

  const act = async (key: string, action: () => Promise<Listing & { signed_out?: boolean }>, done: string) => {
    setBusy(key);
    setNote(null);
    try {
      const next = await action();
      if (next.signed_out) {
        await signOut();
        return;
      }
      setListing({ approved: next.approved, pending: next.pending });
      setNote({ tone: "good", text: done });
      setConfirming(null);
    } catch (e) {
      setNote({ tone: "critical", text: e instanceof Error ? e.message : "request failed" });
    } finally {
      setBusy(null);
    }
  };

  const signOutEverywhere = async () => {
    setBusy("all");
    try {
      await apiPost("/auth/sessions/revoke-all");
    } catch {
      /* this session is among those ended: the sign-in page follows either way */
    }
    await signOut();
  };

  const pending = listing?.pending ?? [];
  return (
    <Card
      title="Devices and sessions"
      subtitle="The PIN works only on a device an operator approved. A new device that enters the right PIN shows a code; type it here to let it in."
      action={pending.length > 0 ? <Badge tone="warning" dot>{`${pending.length} waiting`}</Badge> : undefined}
    >
      {unsupported && (
        <p className="text-xs leading-relaxed text-warning">
          The server does not know about devices yet. Update it outside market hours (
          <code>git pull && sudo bash backend/deploy/install.sh</code>) to require approval for new devices.
        </p>
      )}

      {note && (
        <p role="status" className={`mb-3 text-xs ${note.tone === "good" ? "text-good" : "text-critical"}`}>
          {note.text}
        </p>
      )}

      {pending.length > 0 && (
        <div className="mb-5 space-y-3">
          <div className="text-2xs font-semibold uppercase tracking-[0.14em] text-warning">Waiting for approval</div>
          {pending.map((d) => (
            <div key={d.id} className="rounded-md border border-warning/40 bg-warning/5 p-3">
              <div className="text-sm text-ink">{d.label}</div>
              <div className="mt-0.5 text-2xs text-ink-muted">
                from {d.last_ip ?? "?"} · entered the right PIN · code expires in {Math.ceil((d.expires_in ?? 0) / 60)} min
              </div>
              <p className="mt-2 text-2xs leading-relaxed text-ink-secondary">
                Only approve a device you are holding or can see: type the code shown on its screen. If you did not
                ask for this, refuse it and change the PIN.
              </p>
              <div className="mt-2 flex flex-wrap gap-2">
                <input
                  value={codes[d.id] ?? ""}
                  onChange={(e) => setCodes((c) => ({ ...c, [d.id]: e.target.value.toUpperCase().slice(0, 12) }))}
                  placeholder="ABCD-EFGH"
                  aria-label={`Code shown on ${d.label}`}
                  autoComplete="off"
                  spellCheck={false}
                  className="min-w-0 flex-1 rounded-md border border-hairline bg-surface-raised px-3 py-2 font-mono text-sm tracking-[0.12em] text-ink outline-none placeholder:text-ink-muted focus:border-brand"
                />
                <Button
                  variant="primary"
                  disabled={busy !== null || (codes[d.id] ?? "").replace(/[^A-Z0-9]/g, "").length < 8}
                  onClick={() =>
                    void act(
                      `approve-${d.id}`,
                      () => apiPost(`/auth/devices/${encodeURIComponent(d.id)}/approve`, { code: codes[d.id] }),
                      `${d.label} approved — it signs in by itself.`,
                    )
                  }
                >
                  Approve
                </Button>
                <Button
                  variant="danger"
                  disabled={busy !== null}
                  onClick={() =>
                    void act(`deny-${d.id}`, () => apiPost(`/auth/devices/${encodeURIComponent(d.id)}/deny`), `${d.label} refused.`)
                  }
                >
                  Refuse
                </Button>
              </div>
            </div>
          ))}
        </div>
      )}

      <div className="text-2xs font-semibold uppercase tracking-[0.14em] text-ink-muted">Approved devices</div>
      {listing && listing.approved.length === 0 && <Empty>No device is approved yet.</Empty>}
      <ul className="mt-2 divide-y divide-hairline">
        {(listing?.approved ?? []).map((d) => (
          <li key={d.id} className="flex flex-wrap items-start justify-between gap-x-3 gap-y-2 py-2.5">
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2 text-sm text-ink">
                {d.label}
                {d.current && <Badge tone="brand">This device</Badge>}
              </div>
              <div className="mt-0.5 text-2xs leading-relaxed text-ink-muted">
                Approved {ist(d.approved_ms)} ({d.approved_via ?? "—"}) · last seen {ist(d.last_seen_ms)} from {d.last_ip ?? "?"}
              </div>
            </div>
            {confirming === d.id ? (
              <div className="flex gap-2">
                <Button
                  variant="danger"
                  disabled={busy !== null}
                  onClick={() =>
                    void act(
                      `revoke-${d.id}`,
                      () => apiDelete(`/auth/devices/${encodeURIComponent(d.id)}`),
                      `${d.label} removed; its sessions have ended.`,
                    )
                  }
                >
                  {d.current ? "Remove and sign out" : "Remove"}
                </Button>
                <Button variant="ghost" onClick={() => setConfirming(null)}>
                  Keep
                </Button>
              </div>
            ) : (
              <Button variant="ghost" disabled={busy !== null} onClick={() => setConfirming(d.id)}>
                Remove…
              </Button>
            )}
          </li>
        ))}
      </ul>

      <div className="mt-4 flex flex-wrap items-center gap-3 border-t border-hairline pt-4">
        {confirming === "all" ? (
          <>
            <Button variant="danger" disabled={busy !== null} onClick={() => void signOutEverywhere()}>
              Sign out every device now
            </Button>
            <Button variant="ghost" onClick={() => setConfirming(null)}>
              Cancel
            </Button>
          </>
        ) : (
          <Button disabled={busy !== null || unsupported} onClick={() => setConfirming("all")}>
            Sign out everywhere…
          </Button>
        )}
        <span className="min-w-0 flex-1 text-2xs leading-relaxed text-ink-muted">
          Ends every session on every device, this one included. Approved devices sign in again with the PIN. Use it if
          a device was lost or the PIN may have leaked — then change the PIN on the server.
        </span>
      </div>
    </Card>
  );
}
