/** The command palette's entries and matching (pure, so it is tested). */
export interface Command {
  id: string;
  label: string;
  hint: string;
  href: string;
  keywords?: string;
}

export function baseCommands(todayIso: string): Command[] {
  return [
    { id: "deck", label: "Deck", hint: "Go to", href: "/" },
    { id: "algos", label: "Algorithms", hint: "Go to", href: "/algos" },
    { id: "trades", label: "Blotter", hint: "Go to", href: "/trades", keywords: "trades" },
    { id: "reports", label: "Reports", hint: "Go to", href: "/reports" },
    { id: "analytics", label: "Analytics", hint: "Go to", href: "/analytics", keywords: "stats heatmap kelly monte carlo" },
    { id: "journal-today", label: "Today's journal", hint: todayIso, href: "/journal", keywords: "log events" },
    { id: "reliability", label: "Reliability", hint: "Go to", href: "/reliability", keywords: "errors crashes uptime" },
    { id: "strategy", label: "Strategy", hint: "Go to", href: "/strategy", keywords: "parameters config" },
    { id: "controls", label: "Controls", hint: "Halt, flatten, automation", href: "/controls", keywords: "halt flatten kill automation" },
    { id: "nuke", label: "Emergency stop…", hint: "Opens Controls — the button is there", href: "/controls", keywords: "stop everything nuke" },
  ];
}

/** Start or stop an algorithm is one keystroke away on its page — never fired from a search box. */
export function algoCommands(algos: { id: string; name: string; running: boolean }[]): Command[] {
  return algos.map((a) => ({
    id: `algo-${a.id}`,
    label: `${a.running ? "Stop" : "Start"} ${a.name}…`,
    hint: "Opens its page; you confirm there",
    href: `/algos/${encodeURIComponent(a.id)}`,
    keywords: a.id,
  }));
}

/** Every word typed must appear in the label, hint or keywords; earlier label matches rank first. */
export function matchCommands(all: Command[], query: string): Command[] {
  const words = query.toLowerCase().split(/\s+/).filter(Boolean);
  if (!words.length) return all;
  return all
    .map((c) => {
      const hay = `${c.label} ${c.hint} ${c.keywords ?? ""}`.toLowerCase();
      if (!words.every((w) => hay.includes(w))) return null;
      return { c, rank: c.label.toLowerCase().indexOf(words[0]) };
    })
    .filter((x): x is { c: Command; rank: number } => x !== null)
    .sort((a, b) => (a.rank < 0 ? 99 : a.rank) - (b.rank < 0 ? 99 : b.rank))
    .map((x) => x.c);
}
