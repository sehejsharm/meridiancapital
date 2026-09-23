import Link from "next/link";

import { Logo } from "@/components/Logo";

export const metadata = { title: "Off the chart · Meridian Capital" };

/**
 * Every unmatched URL lands here. Signed-out visitors never do — the proxy
 * sends them to /login first — so this only ever greets the operator, and it
 * does not need to explain what the desk is.
 */
export default function NotFound() {
  return (
    <main className="pt-safe relative flex min-h-[100dvh] items-center justify-center px-6 py-12">
      <div className="pointer-events-none fixed inset-0 plane-grid" aria-hidden="true" />

      <div className="relative w-full max-w-sm text-center">
        <Logo size={72} className="mx-auto" />

        <p className="mt-8 text-2xs font-semibold uppercase tracking-[0.2em] text-brand">
          404 · off the chart
        </p>
        <h1 className="mt-3 text-xl font-semibold text-ink">There is nothing at this address.</h1>
        <p className="mt-2 text-sm leading-relaxed text-ink-secondary">
          The link may be out of date, or the algorithm it pointed to has been removed.
        </p>

        <div className="mt-8 flex flex-col gap-2.5 sm:flex-row sm:justify-center">
          <Link
            href="/"
            className="inline-flex min-h-[44px] items-center justify-center rounded-md border border-brand bg-brand px-5 text-xs font-semibold uppercase tracking-[0.1em] text-[color:var(--plane)] transition-colors hover:bg-brand-bright"
          >
            Back to the deck
          </Link>
          <Link
            href="/algos"
            className="inline-flex min-h-[44px] items-center justify-center rounded-md border border-hairline bg-surface-raised px-5 text-xs font-semibold uppercase tracking-[0.1em] text-ink transition-colors hover:border-brand/50"
          >
            Algorithms
          </Link>
        </div>
      </div>
    </main>
  );
}
