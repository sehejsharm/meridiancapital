"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useRef, useState } from "react";

import { Logo } from "@/components/Logo";
import { PinPad } from "@/components/PinPad";
import { Button } from "@/components/ui";
import { HONEYPOT_FIELD } from "@/lib/honeypot";
import { safeNext } from "@/lib/request-guard";
import { playSplash } from "@/lib/splash";
import {
  describeWebAuthnError,
  getAssertion,
  platformAuthenticatorAvailable,
} from "@/lib/webauthn";

type Mode = "passphrase" | "keypad";
const MODE_KEY = "meridian.login.mode";
// While a new device waits for an operator's approval it asks this often.
const CLAIM_EVERY_MS = 3000;

/** A device that entered the right PIN and is waiting for an operator to approve it. */
type Waiting = { code: string; until: number };

function LoginForm() {
  const router = useRouter();
  const params = useSearchParams();
  const [mode, setMode] = useState<Mode>("passphrase");
  const [secret, setSecret] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [faceIdReady, setFaceIdReady] = useState(false);
  const [waiting, setWaiting] = useState<Waiting | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const attempted = useRef(false);
  const trap = useRef("");

  // Remember how this operator signs in. Per-browser convenience only — wrapped
  // because storage throws in a private window.
  useEffect(() => {
    try {
      const saved = localStorage.getItem(MODE_KEY);
      if (saved === "keypad" || saved === "passphrase") setMode(saved);
    } catch {
      /* the default is fine */
    }
  }, []);

  const chooseMode = useCallback((next: Mode) => {
    setMode(next);
    setSecret("");
    setError(null);
    try {
      localStorage.setItem(MODE_KEY, next);
    } catch {
      /* not worth surfacing */
    }
  }, []);

  const goOn = useCallback(() => {
    // Only ever a path on this site: "?next=//elsewhere.example" must not send a
    // freshly signed-in operator off to someone else's page.
    const next = safeNext(params.get("next"));
    // The mark plays over the move, and the desk loads underneath it.
    playSplash();
    router.replace(next);
    router.refresh();
  }, [params, router]);

  // Waiting for approval: ask every few seconds, and sign in the moment it is given.
  useEffect(() => {
    if (!waiting) return;
    let stopped = false;
    const tick = async () => {
      setNow(Date.now());
      if (Date.now() >= waiting.until) {
        setWaiting(null);
        setError("the approval request ran out — enter the PIN again for a new code");
        return;
      }
      try {
        const res = await fetch("/api/session/claim", { method: "POST", cache: "no-store" });
        if (stopped) return;
        if (res.status === 200) {
          setWaiting(null);
          goOn();
          return;
        }
        if (res.status !== 202) {
          const body = (await res.json().catch(() => ({}))) as { detail?: string };
          setWaiting(null);
          setError(body.detail ?? "this device was not approved");
        }
      } catch {
        /* a dropped poll; the next one tries again */
      }
    };
    const timer = window.setInterval(() => void tick(), CLAIM_EVERY_MS);
    return () => {
      stopped = true;
      window.clearInterval(timer);
    };
  }, [waiting, goOn]);

  useEffect(() => {
    void (async () => {
      if (!(await platformAuthenticatorAvailable())) return;
      try {
        const res = await fetch("/api/session/passkey", { method: "GET", cache: "no-store" });
        if (!res.ok) return;
        const body = (await res.json()) as { available?: boolean };
        setFaceIdReady(Boolean(body.available));
      } catch {
        /* the password still works */
      }
    })();
  }, []);

  const submit = useCallback(
    async (value: string) => {
      if (!value) return;
      setBusy(true);
      setError(null);
      try {
        const res = await fetch("/api/session", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ password: value, [HONEYPOT_FIELD]: trap.current }),
        });
        if (res.status === 403) {
          const body = (await res.json().catch(() => ({}))) as {
            detail?: string;
            pending?: boolean;
            code?: string;
            expires_in?: number;
          };
          if (body.pending && body.code) {
            // The right PIN on a device no operator has approved yet.
            setSecret("");
            setWaiting({ code: body.code, until: Date.now() + (body.expires_in ?? 600) * 1000 });
            return;
          }
          setError(body.detail ?? "sign-in refused");
          if (mode === "keypad") setSecret("");
          return;
        }
        if (!res.ok) {
          const body = (await res.json().catch(() => ({}))) as { detail?: string };
          setError(body.detail ?? "sign-in failed");
          // Clear the keypad so the next attempt starts clean, but keep a typed
          // password: retyping twelve characters because of a server-side
          // problem is a punishment for someone else's fault.
          if (mode === "keypad") setSecret("");
          return;
        }
        goOn();
      } catch {
        setError("could not reach the control plane");
        if (mode === "keypad") setSecret("");
      } finally {
        setBusy(false);
      }
    },
    [goOn, mode],
  );

  const signInWithFaceId = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      const optionsRes = await fetch("/api/session/passkey", { method: "POST" });
      if (!optionsRes.ok) {
        const body = (await optionsRes.json().catch(() => ({}))) as { detail?: string };
        throw new Error(body.detail ?? "could not start Face ID");
      }
      const { handle, options } = (await optionsRes.json()) as {
        handle: string;
        options: Parameters<typeof getAssertion>[0];
      };
      const credential = await getAssertion(options);

      const verifyRes = await fetch("/api/session/passkey", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ handle, credential }),
      });
      if (!verifyRes.ok) {
        const body = (await verifyRes.json().catch(() => ({}))) as { detail?: string };
        throw new Error(body.detail ?? "Face ID was refused");
      }
      goOn();
    } catch (e) {
      setError(describeWebAuthnError(e));
    } finally {
      setBusy(false);
    }
  }, [goOn]);

  // On a phone the expected gesture is Face ID, so try it once rather than
  // making the operator reach for a button they were always going to press.
  useEffect(() => {
    if (!faceIdReady || attempted.current) return;
    attempted.current = true;
    void signInWithFaceId();
  }, [faceIdReady, signInWithFaceId]);

  if (waiting) {
    return <AwaitingApproval waiting={waiting} now={now} onCancel={() => setWaiting(null)} />;
  }

  return (
    <div className="w-full max-w-sm">
      <div className="flex flex-col items-center text-center">
        <button type="button" onClick={playSplash} aria-label="Meridian Capital" className="rounded-md">
          <Logo size={80} className="text-brand" />
        </button>
        <h1 className="mt-4 text-lg font-semibold tracking-[0.22em] text-ink">MERIDIAN</h1>
        <p className="text-2xs tracking-[0.34em] text-brand">CAPITAL</p>
      </div>

      {/* Decoy for form-filling bots. Off-screen rather than display:none —
          the cruder bots skip hidden fields but fill anything laid out — and
          out of the tab order and the accessibility tree, so no person ever
          reaches it. */}
      <input
        type="text"
        name={HONEYPOT_FIELD}
        tabIndex={-1}
        autoComplete="off"
        aria-hidden="true"
        defaultValue=""
        onChange={(e) => {
          trap.current = e.target.value;
        }}
        className="pointer-events-none absolute -left-[9999px] top-0 h-px w-px opacity-0"
      />

      {mode === "passphrase" ? (
        <form
          className="mt-8"
          onSubmit={(e) => {
            e.preventDefault();
            void submit(secret);
          }}
        >
          <label
            htmlFor="operator-password"
            className="block text-2xs uppercase tracking-[0.14em] text-ink-muted"
          >
            Operator password
          </label>
          <input
            id="operator-password"
            type="password"
            value={secret}
            onChange={(e) => {
              setSecret(e.target.value);
              if (error) setError(null);
            }}
            autoComplete="current-password"
            autoFocus
            required
            maxLength={512}
            className="mt-2 w-full rounded-md border border-hairline bg-surface px-3 py-2.5 text-sm text-ink outline-none transition-colors focus:border-brand"
          />
          <p className="mt-1.5 text-2xs tabular-nums text-ink-muted">
            {secret.length} character{secret.length === 1 ? "" : "s"}
          </p>

          {error && (
            <p role="alert" className="mt-3 text-xs text-critical">
              {error}
            </p>
          )}

          <div className="mt-5">
            <Button type="submit" variant="primary" full disabled={busy || !secret}>
              {busy ? "Signing in…" : "Enter the desk"}
            </Button>
          </div>
        </form>
      ) : (
        <>
          <p className="mt-7 text-center text-2xs uppercase tracking-[0.16em] text-ink-muted">
            Operator PIN
          </p>
          <div className="mt-5">
            <PinPad
              value={secret}
              onChange={(next) => {
                setSecret(next);
                if (error) setError(null);
              }}
              onSubmit={() => void submit(secret)}
              disabled={busy}
            />
          </div>

          {error && (
            <p role="alert" className="mt-4 text-center text-xs text-critical">
              {error}
            </p>
          )}

          <div className="mt-5">
            <Button
              variant="primary"
              full
              onClick={() => void submit(secret)}
              disabled={busy || !secret}
            >
              {busy ? "Signing in…" : "Enter the desk"}
            </Button>
          </div>
        </>
      )}

      {faceIdReady && (
        <div className="mt-3">
          <Button variant="default" full onClick={signInWithFaceId} disabled={busy}>
            {busy ? "Waiting…" : "Use Face ID"}
          </Button>
        </div>
      )}

      <div className="mt-6 text-center">
        <button
          type="button"
          onClick={() => chooseMode(mode === "passphrase" ? "keypad" : "passphrase")}
          className="inline-flex min-h-[44px] items-center px-3 text-2xs uppercase tracking-[0.12em] text-ink-muted underline underline-offset-4 transition-colors hover:text-brand"
        >
          {mode === "passphrase" ? "Use the number keypad" : "Use a password instead"}
        </button>
      </div>

      <p className="mt-6 text-center text-2xs leading-relaxed text-ink-muted">
        This dashboard controls a live trading account.
        <br />
        Sessions expire after 12 hours.
      </p>
    </div>
  );
}

function AwaitingApproval({ waiting, now, onCancel }: { waiting: Waiting; now: number; onCancel: () => void }) {
  const left = Math.max(0, Math.ceil((waiting.until - now) / 1000));
  return (
    <div className="w-full max-w-sm text-center">
      <div className="flex flex-col items-center">
        <Logo size={56} className="text-brand" />
        <h1 className="mt-4 text-sm font-semibold uppercase tracking-[0.18em] text-ink">This device needs approval</h1>
      </div>
      <p className="mt-4 text-xs leading-relaxed text-ink-secondary">
        The PIN was right, but this device has not been approved yet. An operator approves it by typing this code:
      </p>
      <p
        className="mt-5 select-all rounded-lg border border-brand/40 bg-brand-dim px-4 py-4 font-mono text-3xl font-semibold tracking-[0.2em] text-brand"
        aria-label={`Approval code ${waiting.code.split("").join(" ")}`}
      >
        {waiting.code}
      </p>
      <ul className="mt-5 space-y-2 text-left text-2xs leading-relaxed text-ink-muted">
        <li>
          <span className="text-ink-secondary">On a signed-in device:</span> Controls, Devices and sessions, then
          type the code.
        </li>
        <li>
          <span className="text-ink-secondary">On the server:</span>{" "}
          <code className="break-all rounded bg-surface-raised px-1 py-0.5 text-ink">
            sudo bash ~/meridiancapital/backend/deploy/devices.sh approve {waiting.code}
          </code>
        </li>
      </ul>
      <p className="mt-5 text-2xs tabular-nums text-ink-muted" role="status">
        Waiting… this page signs in by itself once approved. The code lasts {Math.floor(left / 60)}:
        {String(left % 60).padStart(2, "0")} more.
      </p>
      <div className="mt-5">
        <Button variant="default" full onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </div>
  );
}

export default function LoginPage() {
  return (
    <main className="relative flex min-h-screen items-center justify-center px-4 py-10">
      <div className="pointer-events-none absolute inset-0 plane-grid" aria-hidden="true" />
      <div className="relative z-10 w-full max-w-sm rounded-xl border border-hairline bg-surface/90 p-7 backdrop-blur">
        <Suspense fallback={null}>
          <LoginForm />
        </Suspense>
      </div>
    </main>
  );
}
