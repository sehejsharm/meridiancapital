"use client";

import Link from "next/link";

/**
 * What a broken page shows instead of a blank screen.
 *
 * This is a one-operator private desk, so the real error message is shown: it
 * is the fastest route to a fix, and there is no stranger to hide it from. The
 * commonest cause in practice is the dashboard (which Vercel redeploys on every
 * push) running ahead of the control plane (which moves only when the VM is
 * updated), so that case gets named outright.
 */
export function CrashNotice({
  error,
  retry,
  standalone = false,
}: {
  error: Error & { digest?: string };
  retry: () => void;
  standalone?: boolean;
}) {
  const message = error?.message || "Unknown error";
  const looksLikeVersionSkew =
    /reading '|undefined is not an object|is not a function|Unexpected token|is not iterable/i.test(message);

  return (
    <div
      role="alert"
      className={`mx-auto max-w-xl rounded-lg border border-critical/40 bg-surface p-5 ${standalone ? "mt-[15vh]" : ""}`}
    >
      <p className="text-2xs font-semibold uppercase tracking-[0.16em] text-critical">
        This page hit an error
      </p>
      <p className="mt-2 text-sm text-ink">
        Nothing has been sent to the broker because of it — engines run on the VM and keep
        running whatever this page does.
      </p>

      {looksLikeVersionSkew && (
        <p className="mt-3 rounded-md border border-warning/40 bg-warning/10 px-3 py-2 text-xs text-warning">
          This usually means the dashboard is newer than the control plane on your VM. Update
          the VM (fetch main, run install.sh, restart meridian-api) and reload.
        </p>
      )}

      <pre className="mt-3 max-h-40 overflow-auto whitespace-pre-wrap break-words rounded-md bg-surface-raised p-3 text-2xs text-ink-secondary">
        {message}
        {error?.digest ? `\n\ndigest ${error.digest}` : ""}
      </pre>

      <div className="mt-4 flex flex-wrap gap-2">
        <button
          type="button"
          onClick={() => retry()}
          className="inline-flex min-h-[44px] items-center rounded-md border border-brand bg-brand px-4 text-xs font-semibold uppercase tracking-[0.1em] text-[color:var(--plane)]"
        >
          Try again
        </button>
        {/* A plain anchor, not a client transition: if the router itself is
            what broke, a full navigation still gets you to the stop button. */}
        <a
          href="/controls"
          className="inline-flex min-h-[44px] items-center rounded-md border border-hairline bg-surface-raised px-4 text-xs font-semibold uppercase tracking-[0.1em] text-ink"
        >
          Go to Controls
        </a>
        {!standalone && (
          <Link
            href="/"
            className="inline-flex min-h-[44px] items-center px-3 text-xs uppercase tracking-[0.1em] text-ink-muted underline underline-offset-4"
          >
            Deck
          </Link>
        )}
      </div>
    </div>
  );
}
