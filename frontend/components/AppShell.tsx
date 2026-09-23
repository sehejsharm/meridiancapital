"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";

import { Wordmark } from "@/components/Logo";
import {
  IconAlgos,
  IconControls,
  IconDeck,
  IconJournal,
  IconMore,
  IconReports,
  IconSignOut,
  IconTrades,
  IconTune,
} from "@/components/NavIcons";
import { StalenessDot } from "@/components/StalenessMonitor";
import { Badge } from "@/components/ui";
import { signOut } from "@/lib/client-api";
import { useLiveFeed } from "@/lib/LiveContext";
import { istTime } from "@/lib/format";

const NAV = [
  { href: "/", label: "Deck", short: "Deck", Icon: IconDeck },
  { href: "/algos", label: "Algorithms", short: "Algos", Icon: IconAlgos },
  { href: "/trades", label: "Blotter", short: "Trades", Icon: IconTrades },
  { href: "/reports", label: "Reports", short: "Reports", Icon: IconReports },
  { href: "/journal", label: "Journal", short: "Log", Icon: IconJournal },
  { href: "/strategy", label: "Strategy", short: "Tune", Icon: IconTune },
  { href: "/controls", label: "Controls", short: "Controls", Icon: IconControls },
];

/**
 * A phone gets five tabs, not seven: seven at a usable width is wider than the
 * screen, and the one pushed off the edge was Controls — the page with the
 * emergency stop. Controls is therefore always a tab; the pages you visit
 * rather than act from sit behind More.
 */
const TABS = ["/", "/algos", "/trades", "/controls"];
const MORE = NAV.filter((n) => !TABS.includes(n.href));

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

      <header className="pt-safe relative z-10 border-b border-hairline bg-surface/80 backdrop-blur">
        <div className="mx-auto flex max-w-[1400px] items-center gap-3 px-4 py-3 sm:px-6">
          <Link href="/" className="-my-1.5 shrink-0 py-1.5">
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
            <StalenessDot />
            <ConnectionChip state={connection} ts={snapshot?.ts} />
            <button
              type="button"
              onClick={() => void signOut()}
              className="hidden shrink-0 rounded-md px-2 py-1 text-2xs uppercase tracking-[0.12em] text-ink-muted transition-colors hover:text-ink lg:inline-flex"
            >
              Sign out
            </button>
          </div>
        </div>
        <div className="h-px rule-gold" />
      </header>

      <main className="relative z-10 mx-auto max-w-[1400px] px-4 py-5 sm:px-6">
        {children}
      </main>

      <footer className="pb-tabbar relative z-10 mx-auto max-w-[1400px] px-4 text-2xs text-ink-muted sm:px-6 lg:pb-8">
        Meridian Capital · {snapshot?.engine.banner ?? "GANESH KAVACH 50K"} · all money figures
        read from Angel One
      </footer>

      <TabBar pathname={pathname} />
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

function TabBar({ pathname }: { pathname: string }) {
  const [moreOpen, setMoreOpen] = useState(false);
  const moreActive = MORE.some((n) => isActive(pathname, n.href));

  // Navigating closes the sheet; so does the back gesture's Escape equivalent.
  useEffect(() => setMoreOpen(false), [pathname]);
  useEffect(() => {
    if (!moreOpen) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setMoreOpen(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [moreOpen]);

  return (
    <>
      {moreOpen && (
        <div
          className="fixed inset-0 z-30 bg-black/60 backdrop-blur-sm lg:hidden"
          onClick={() => setMoreOpen(false)}
          aria-hidden="true"
        />
      )}

      {/* The sheet sits directly above the bar so the thumb travels upward
          from the button that opened it. */}
      <div
        id="more-sheet"
        role="menu"
        aria-label="More sections"
        hidden={!moreOpen}
        className="fixed inset-x-3 z-40 rounded-xl border border-hairline bg-surface p-2 shadow-2xl lg:hidden"
        style={{ bottom: "calc(64px + env(safe-area-inset-bottom))" }}
      >
        {MORE.map(({ href, label, Icon }) => {
          const active = isActive(pathname, href);
          return (
            <Link
              key={href}
              href={href}
              role="menuitem"
              aria-current={active ? "page" : undefined}
              className={`flex min-h-[52px] items-center gap-3 rounded-lg px-3 text-sm transition-colors ${
                active ? "bg-brand-dim text-brand" : "text-ink hover:bg-surface-raised"
              }`}
            >
              <Icon className="shrink-0" />
              {label}
            </Link>
          );
        })}
        <div className="my-1 h-px bg-hairline" />
        <button
          type="button"
          role="menuitem"
          onClick={() => void signOut()}
          className="flex min-h-[52px] w-full items-center gap-3 rounded-lg px-3 text-sm text-ink-secondary transition-colors hover:bg-surface-raised"
        >
          <IconSignOut className="shrink-0" />
          Sign out
        </button>
      </div>

      <nav
        aria-label="Sections"
        className="fixed inset-x-0 bottom-0 z-40 border-t border-hairline bg-surface/95 backdrop-blur lg:hidden"
        style={{ paddingBottom: "env(safe-area-inset-bottom)" }}
      >
        <ul className="mx-auto grid max-w-lg grid-cols-5">
          {TABS.map((href) => {
            const item = NAV.find((n) => n.href === href)!;
            const active = isActive(pathname, href);
            return (
              <li key={href}>
                <Link
                  href={href}
                  aria-current={active ? "page" : undefined}
                  className={`flex h-14 flex-col items-center justify-center gap-1 text-[10px] font-medium uppercase tracking-[0.08em] transition-colors ${
                    active ? "text-brand" : "text-ink-muted"
                  }`}
                >
                  <item.Icon />
                  {item.short}
                </Link>
              </li>
            );
          })}
          <li>
            <button
              type="button"
              aria-expanded={moreOpen}
              aria-controls="more-sheet"
              onClick={() => setMoreOpen((v) => !v)}
              className={`flex h-14 w-full flex-col items-center justify-center gap-1 text-[10px] font-medium uppercase tracking-[0.08em] transition-colors ${
                moreOpen || moreActive ? "text-brand" : "text-ink-muted"
              }`}
            >
              <IconMore />
              More
            </button>
          </li>
        </ul>
      </nav>
    </>
  );
}
