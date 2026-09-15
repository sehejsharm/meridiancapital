"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";

import { Wordmark } from "@/components/Logo";
import { Badge } from "@/components/ui";
import { signOut } from "@/lib/client-api";
import { useLiveFeed } from "@/lib/LiveContext";
import { istTime } from "@/lib/format";

const NAV = [
  { href: "/", label: "Deck", short: "Deck" },
  { href: "/algos", label: "Algorithms", short: "Algos" },
  { href: "/trades", label: "Blotter", short: "Trades" },
  { href: "/reports", label: "Reports", short: "Reports" },
  { href: "/journal", label: "Journal", short: "Log" },
  { href: "/strategy", label: "Strategy", short: "Tune" },
  { href: "/controls", label: "Controls", short: "Control" },
];

function isActive(pathname: string, href: string): boolean {
  return href === "/" ? pathname === "/" : pathname.startsWith(href);
}

export function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const { snapshot, status, connection } = useLiveFeed();

  const mode = snapshot?.engine.mode ?? status?.engine.mode ?? "paper";
  const running = status?.engine.running ?? false;
  const phase = running ? (snapshot?.engine.phase ?? "STARTING") : "STOPPED";
  const liveCount = status?.fleet?.live_running ?? 0;

  return (
    <div className="min-h-screen">
      <div className="pointer-events-none fixed inset-0 plane-grid" aria-hidden="true" />

      {/* Real money is the one state worth interrupting the layout for. */}
      {liveCount > 0 && (
        <div className="relative z-20 bg-critical/15 px-4 py-1.5 text-center text-2xs font-semibold uppercase tracking-[0.14em] text-critical">
          {liveCount} algorithm{liveCount === 1 ? "" : "s"} trading real money
        </div>
      )}

      <header className="relative z-10 border-b border-hairline bg-surface/80 backdrop-blur">
        <div className="mx-auto flex max-w-[1400px] items-center gap-3 px-4 py-3 sm:px-6">
          <Link href="/" className="shrink-0">
            <Wordmark />
          </Link>

          {/* Full rail from lg up; below that the bottom bar carries navigation. */}
          <nav aria-label="Sections" className="hidden gap-1 lg:flex">
            {NAV.map((item) => (
              <Link
                key={item.href}
                href={item.href}
                aria-current={isActive(pathname, item.href) ? "page" : undefined}
                className={`rounded-md px-3 py-1.5 text-xs font-medium uppercase tracking-[0.12em] transition-colors ${
                  isActive(pathname, item.href)
                    ? "bg-brand-dim text-brand"
                    : "text-ink-muted hover:text-ink"
                }`}
              >
                {item.label}
              </Link>
            ))}
          </nav>

          <div className="ml-auto flex min-w-0 items-center gap-2">
            <Badge tone={mode === "live" ? "critical" : "neutral"}>
              {mode === "live" ? "Live" : "Paper"}
            </Badge>
            <span className="hidden sm:inline-flex">
              <Badge tone={running ? "good" : "warning"} dot={running}>
                {phase}
              </Badge>
            </span>
            <ConnectionChip state={connection} ts={snapshot?.ts} />
            <button
              type="button"
              onClick={() => void signOut()}
              className="shrink-0 rounded-md px-2 py-1 text-2xs uppercase tracking-[0.12em] text-ink-muted transition-colors hover:text-ink"
            >
              Sign out
            </button>
          </div>
        </div>
        <div className="h-px rule-gold" />
      </header>

      <main className="relative z-10 mx-auto max-w-[1400px] px-4 py-5 pb-24 sm:px-6 lg:pb-6">
        {children}
      </main>

      <footer className="relative z-10 mx-auto max-w-[1400px] px-4 pb-24 text-2xs text-ink-muted sm:px-6 lg:pb-8">
        Meridian Capital · {snapshot?.engine.banner ?? "GANESH KAVACH 50K"} · all money figures
        read from Angel One
      </footer>

      {/* Thumb-reachable navigation on a phone, with safe-area padding so the
          last row is not sitting under the home indicator. */}
      <nav
        aria-label="Sections"
        className="fixed inset-x-0 bottom-0 z-20 border-t border-hairline bg-surface/95 backdrop-blur lg:hidden"
        style={{ paddingBottom: "env(safe-area-inset-bottom)" }}
      >
        <ul className="mx-auto flex max-w-[1400px] items-stretch overflow-x-auto">
          {NAV.map((item) => {
            const active = isActive(pathname, item.href);
            return (
              <li key={item.href} className="flex-1">
                <Link
                  href={item.href}
                  aria-current={active ? "page" : undefined}
                  className={`flex min-h-[52px] min-w-[64px] flex-col items-center justify-center gap-0.5 px-2 py-2 text-2xs font-medium uppercase tracking-[0.1em] transition-colors ${
                    active ? "text-brand" : "text-ink-muted"
                  }`}
                >
                  <span
                    aria-hidden="true"
                    className={`h-0.5 w-6 rounded-full transition-colors ${
                      active ? "bg-brand" : "bg-transparent"
                    }`}
                  />
                  {item.short}
                </Link>
              </li>
            );
          })}
        </ul>
      </nav>
    </div>
  );
}

function ConnectionChip({ state, ts }: { state: string; ts?: string }) {
  const copy = {
    live: { tone: "good" as const, label: "Streaming" },
    polling: { tone: "warning" as const, label: "Polling" },
    connecting: { tone: "neutral" as const, label: "Connecting" },
    offline: { tone: "critical" as const, label: "Offline" },
  }[state] ?? { tone: "neutral" as const, label: state };

  return (
    <span className="hidden items-center gap-2 md:inline-flex">
      <Badge tone={copy.tone}>{copy.label}</Badge>
      {ts && <span className="text-2xs tabular-nums text-ink-muted">{istTime(ts)} IST</span>}
    </span>
  );
}
