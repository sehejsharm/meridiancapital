"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

import { Logo } from "@/components/Logo";
import { Button } from "@/components/ui";

function LoginForm() {
  const router = useRouter();
  const params = useSearchParams();
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const res = await fetch("/api/session", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ password }),
      });
      if (!res.ok) {
        const body = (await res.json().catch(() => ({}))) as { detail?: string };
        setError(body.detail ?? "sign-in failed");
        return;
      }
      const next = params.get("next");
      router.replace(next && next.startsWith("/") ? next : "/");
      router.refresh();
    } catch {
      setError("could not reach the control plane");
    } finally {
      setBusy(false);
    }
  };

  return (
    <form onSubmit={submit} className="w-full max-w-sm">
      <div className="flex flex-col items-center text-center">
        <Logo size={92} className="text-brand" />
        <h1 className="mt-5 text-lg font-semibold tracking-[0.22em] text-ink">MERIDIAN</h1>
        <p className="text-2xs tracking-[0.34em] text-brand">CAPITAL</p>
      </div>

      <label className="mt-8 block text-2xs uppercase tracking-[0.14em] text-ink-muted">
        Operator password
        <input
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          autoComplete="current-password"
          autoFocus
          required
          className="mt-2 w-full rounded-md border border-hairline bg-surface px-3 py-2.5 text-sm text-ink outline-none transition-colors focus:border-brand"
        />
      </label>

      {error && (
        <p role="alert" className="mt-3 text-xs text-critical">
          {error}
        </p>
      )}

      <div className="mt-5">
        <Button type="submit" variant="primary" full disabled={busy || !password}>
          {busy ? "Signing in…" : "Enter the desk"}
        </Button>
      </div>

      <p className="mt-6 text-center text-2xs leading-relaxed text-ink-muted">
        This dashboard controls a live trading account.
        <br />
        Sessions expire after 12 hours.
      </p>
    </form>
  );
}

export default function LoginPage() {
  return (
    <main className="relative flex min-h-screen items-center justify-center px-4 py-10">
      <div className="pointer-events-none absolute inset-0 plane-grid" aria-hidden="true" />
      <div className="relative z-10 w-full max-w-sm rounded-xl border border-hairline bg-surface/90 p-8 backdrop-blur">
        <Suspense fallback={null}>
          <LoginForm />
        </Suspense>
      </div>
    </main>
  );
}
