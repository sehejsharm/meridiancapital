"use client";

import { useCallback, useEffect, useState } from "react";

import { Badge, Button, Card, Empty } from "@/components/ui";
import { apiDelete, apiGet, apiPost } from "@/lib/client-api";
import { istDateTime } from "@/lib/format";
import {
  createCredential,
  describeWebAuthnError,
  platformAuthenticatorAvailable,
} from "@/lib/webauthn";

interface Passkey {
  credential_id: string;
  label: string;
  created_ts: string;
  last_used_ts: string | null;
  sign_count: number;
}

/**
 * Enrolling and revoking Face ID devices.
 *
 * Enrolment lives behind an authenticated session on purpose — adding a passkey
 * issues another key to the account, so it has to be something only someone
 * already signed in can do.
 */
export function PasskeyManager() {
  const [keys, setKeys] = useState<Passkey[] | null>(null);
  const [supported, setSupported] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<{ tone: "good" | "critical"; text: string } | null>(null);

  const load = useCallback(async () => {
    try {
      const body = await apiGet<{ passkeys: Passkey[] }>("/auth/passkeys");
      setKeys(body.passkeys);
    } catch (e) {
      setNotice({ tone: "critical", text: e instanceof Error ? e.message : "could not load devices" });
      setKeys([]);
    }
  }, []);

  useEffect(() => {
    void load();
    void platformAuthenticatorAvailable().then(setSupported);
  }, [load]);

  const enrol = useCallback(async () => {
    setBusy(true);
    setNotice(null);
    try {
      const { handle, options } = await apiPost<{
        handle: string;
        options: Parameters<typeof createCredential>[0];
      }>("/auth/passkeys/register/options");

      const credential = await createCredential(options);
      const label =
        typeof navigator !== "undefined" && /iPhone|iPad/.test(navigator.userAgent)
          ? "iPhone (Face ID)"
          : "This device";

      await apiPost("/auth/passkeys/register", { handle, credential, label });
      setNotice({ tone: "good", text: "Device enrolled. Face ID will work at sign-in." });
      await load();
    } catch (e) {
      setNotice({ tone: "critical", text: describeWebAuthnError(e) });
    } finally {
      setBusy(false);
    }
  }, [load]);

  const revoke = useCallback(
    async (id: string) => {
      setBusy(true);
      setNotice(null);
      try {
        await apiDelete(`/auth/passkeys/${encodeURIComponent(id)}`);
        setNotice({ tone: "good", text: "Device removed." });
        await load();
      } catch (e) {
        setNotice({ tone: "critical", text: e instanceof Error ? e.message : "could not remove" });
      } finally {
        setBusy(false);
      }
    },
    [load],
  );

  return (
    <Card
      title="Face ID sign-in"
      subtitle="Devices that can sign in without the PIN"
      action={<Badge tone={keys?.length ? "good" : "neutral"}>{keys?.length ?? 0} enrolled</Badge>}
    >
      {notice && (
        <div
          role="status"
          className={`mb-3 rounded-md border px-3 py-2 text-xs ${
            notice.tone === "good"
              ? "border-good/40 bg-good/10 text-good"
              : "border-critical/40 bg-critical/10 text-critical"
          }`}
        >
          {notice.text}
        </div>
      )}

      <p className="mb-3 text-2xs leading-relaxed text-ink-muted">
        The key stays in this device&apos;s secure enclave and never leaves it. Face ID unlocks it
        locally and the device signs a challenge — no biometric data reaches the server, and
        nothing stored here could reconstruct a face.
      </p>

      {!keys ? (
        <p className="text-xs text-ink-muted">Loading…</p>
      ) : !keys.length ? (
        <Empty>No devices enrolled. The PIN is the only way in.</Empty>
      ) : (
        <ul className="divide-y divide-hairline">
          {keys.map((k) => (
            <li key={k.credential_id} className="flex items-center justify-between gap-3 py-2.5">
              <div className="min-w-0">
                <p className="truncate text-xs text-ink">{k.label}</p>
                <p className="text-2xs text-ink-muted">
                  added {istDateTime(k.created_ts)}
                  {k.last_used_ts ? ` · last used ${istDateTime(k.last_used_ts)}` : " · never used"}
                </p>
              </div>
              <Button variant="ghost" disabled={busy} onClick={() => void revoke(k.credential_id)}>
                Remove
              </Button>
            </li>
          ))}
        </ul>
      )}

      <div className="mt-4">
        {supported ? (
          <Button variant="primary" onClick={enrol} disabled={busy}>
            {busy ? "Waiting for the device…" : "Enrol this device"}
          </Button>
        ) : (
          <p className="text-2xs text-ink-muted">
            This browser has no platform biometrics. Open the dashboard on your phone to enrol
            Face ID.
          </p>
        )}
      </div>
    </Card>
  );
}
