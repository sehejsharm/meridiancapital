/**
 * Tab-bar glyphs. Inline so the bar paints with the page — an icon font or a
 * sprite request would leave five blank squares on a slow connection, and the
 * tab bar is the one thing that has to work first.
 */

type P = { className?: string };
const base = {
  width: 22,
  height: 22,
  viewBox: "0 0 24 24",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.6,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
  "aria-hidden": true,
};

export function IconDeck({ className }: P) {
  return (
    <svg {...base} className={className}>
      <rect x="3" y="3" width="7" height="9" rx="1.5" />
      <rect x="14" y="3" width="7" height="5" rx="1.5" />
      <rect x="14" y="12" width="7" height="9" rx="1.5" />
      <rect x="3" y="16" width="7" height="5" rx="1.5" />
    </svg>
  );
}

export function IconAlgos({ className }: P) {
  return (
    <svg {...base} className={className}>
      <path d="M8 6 3 12l5 6" />
      <path d="m16 6 5 6-5 6" />
      <path d="m14 4-4 16" />
    </svg>
  );
}

export function IconTrades({ className }: P) {
  return (
    <svg {...base} className={className}>
      <path d="M4 7h13" />
      <path d="m14 4 3 3-3 3" />
      <path d="M20 17H7" />
      <path d="m10 14-3 3 3 3" />
    </svg>
  );
}

export function IconControls({ className }: P) {
  return (
    <svg {...base} className={className}>
      <path d="M12 3v8" />
      <path d="M6.3 6.3a8 8 0 1 0 11.4 0" />
    </svg>
  );
}

export function IconMore({ className }: P) {
  return (
    <svg {...base} className={className}>
      <circle cx="5" cy="12" r="1.3" fill="currentColor" />
      <circle cx="12" cy="12" r="1.3" fill="currentColor" />
      <circle cx="19" cy="12" r="1.3" fill="currentColor" />
    </svg>
  );
}

export function IconReports({ className }: P) {
  return (
    <svg {...base} className={className}>
      <path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" />
      <path d="M14 3v5h5" />
      <path d="M9 17v-3M12 17v-6M15 17v-2" />
    </svg>
  );
}

export function IconJournal({ className }: P) {
  return (
    <svg {...base} className={className}>
      <path d="M8 6h12M8 12h12M8 18h12" />
      <path d="M4 6h.01M4 12h.01M4 18h.01" />
    </svg>
  );
}

export function IconTune({ className }: P) {
  return (
    <svg {...base} className={className}>
      <path d="M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12M20 18h0" />
      <circle cx="16" cy="6" r="2" />
      <circle cx="10" cy="12" r="2" />
      <circle cx="18" cy="18" r="2" />
    </svg>
  );
}

export function IconSignOut({ className }: P) {
  return (
    <svg {...base} className={className}>
      <path d="M15 4h3a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-3" />
      <path d="m10 16-4-4 4-4" />
      <path d="M6 12h10" />
    </svg>
  );
}
