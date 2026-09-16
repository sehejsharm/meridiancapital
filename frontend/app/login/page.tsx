"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useRef, useState } from "react";

import { Logo } from "@/components/Logo";
import { PinPad } from "@/components/PinPad";
import { Button } from "@/components/ui";
import {
  createCredential,
  describeWebAuthnError,
  getAssertion,
  platformAuthenticatorAvailable,
} from "@/lib/webauthn";

const PIN_LENGTH = 4;

function LoginForm() {
  const router = useRouter();
  const params = useSearchParams();
  const [pin, setPin] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [faceIdReady, setFaceIdReady] = useState(false);
  const attempted = useRef(false);

  const goOn = useCallback(() => {
    const next = params.get("next");
    router.replace(next && next.startsWith("/") ? next : "/");
    router.refresh();
  }, [params, router]);

  // Offer Face ID only when this browser can do platform biometrics AND a
  // device is actually enrolled — a button that always fails is worse than no
  // button.
  useEffect(() => {
    void (async () => {
      if (!(await platformAuthenticatorAvailable())) return;
      try {
        const res = await fetch("/api/session/passkey", { method: "GET", cache: "no-store" });
        if (!res.ok) return;
        const body = (await res.json()) as { available?: boolean };
        setFaceIdReady(Boolean(body.available));
      } catch {
        /* the PIN still works */
      }
    })();
  }, []);

  const submitPin = useCallback(
    async (value: string) => {
      setBusy(true);
      setError(null);
      try {
        const res = await fetch("/api/session", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ password: value }),
        });
        if (!res.ok) {
          const body = (await res.json().catch(() => ({}))) as { detail?: string };
          setError(body.detail ?? "sign-in failed");
          setPin("");
          return;
        }
        goOn();
      } catch {
        setError("could not reach the control plane");
        setPin("");
      } finally {
        setBusy(false);
      }
    },
    [goOn],
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

  // On a phone the expected gesture is Face ID, so try it once automatically
  // rather than making the operator reach for a button they were always going
  // to press. A cancellation falls back to the PIN and is not an error.
  useEffect(() => {
    if (!faceIdReady || attempted.current) return;
    attempted.current = true;
    void (async () => {
      try {
        await signInWithFaceId();
      } catch {
        /* handled inside */
      }
    })();
  }, [faceIdReady, signInWithFaceId]);

  return (
    <div className="w-full max-w-sm">
      <div className="flex flex-col items-center text-center">
        <Logo size={80} className="text-brand" />
        <h1 className="mt-4 text-lg font-semibold tracking-[0.22em] text-ink">MERIDIAN</h1>
        <p className="text-2xs tracking-[0.34em] text-brand">CAPITAL</p>
      </div>

      <p className="mt-7 text-center text-2xs uppercase tracking-[0.16em] text-ink-muted">
        Operator PIN
      </p>

      <div className="mt-5">
        <PinPad
          value={pin}
          onChange={(next) => {
            setPin(next);
            if (error) setError(null);
          }}
          onComplete={submitPin}
          length={PIN_LENGTH}
          disabled={busy}
        />
      </div>

      {error && (
        <p role="alert" className="mt-4 text-center text-xs text-critical">
          {error}
        </p>
      )}

      {faceIdReady && (
        <div className="mt-6">
          <Button variant="default" full onClick={signInWithFaceId} disabled={busy}>
            {busy ? "Waiting…" : "Use Face ID"}
          </Button>
        </div>
      )}

      <p className="mt-7 text-center text-2xs leading-relaxed text-ink-muted">
        This dashboard controls a live trading account.
        <br />
        Sessions expire after 12 hours.
      </p>
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
