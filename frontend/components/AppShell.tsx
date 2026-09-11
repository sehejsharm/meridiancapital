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
  { href: "/", label: "Desk" },
  { href: "/trades", label: "Blotter" },
  { href: "/journal", label: "Journal" },
  { href: "/controls", label: "Controls" },
];

export function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const { snapshot, status, connection } = useLiveFeed();

  const mode = snapshot?.engine.mode ?? status?.engine.mode ?? "paper";
  const running = status?.engine.running ?? false;
  const phase = running ? (snapshot?.engine.phase ?? "STARTING") : "STOPPED";

  return (
    <div className="min-h-screen">
      <div className="pointer-events-none fixed inset-0 plane-grid" aria-hidden="true" />

      <header className="relative z-10 border-b border-hairline bg-surface/80 backdrop-blur">
        <div className="mx-auto flex max-w-[1400px] flex-wrap items-center gap-x-6 gap-y-3 px-4 py-3 sm:px-6">
          <Link href="/" className="shrink-0">
            <Wordmark />
          </Link>

          <nav className="order-3 flex w-full gap-1 sm:order-none sm:w-auto">
            {NAV.map((item) => {
              const active =
                item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  className={`rounded-md px-3 py-1.5 text-xs font-medium uppercase tracking-[0.12em] transition-colors ${
                    active
                      ? "bg-brand-dim text-brand"
                      : "text-ink-muted hover:text-ink"
                  }`}
                >
                  {item.label}
                </Link>
              );
            })}
          </nav>

          <div className="ml-auto flex items-center gap-2">
            <Badge tone={mode === "live" ? "critical" : "neutral"}>
              {mode === "live" ? "Live trading" : "Paper"}
            </Badge>
            <Badge tone={running ? "good" : "warning"} dot={running}>
              {phase}
            </Badge>
            <ConnectionChip state={connection} ts={snapshot?.ts} />
            <button
              type="button"
              onClick={() => void signOut()}
              className="rounded-md px-2 py-1 text-2xs uppercase tracking-[0.12em] text-ink-muted transition-colors hover:text-ink"
            >
              Sign out
            </button>
          </div>
        </div>
        <div className="h-px rule-gold" />
      </header>

      <main className="relative z-10 mx-auto max-w-[1400px] px-4 py-6 sm:px-6">{children}</main>

      <footer className="relative z-10 mx-auto max-w-[1400px] px-4 pb-8 text-2xs text-ink-muted sm:px-6">
        Meridian Capital · {snapshot?.engine.banner ?? "GANESH KAVACH 50K"} · all money figures
        read from Angel One
      </footer>
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
