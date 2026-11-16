/** Exit settings as data (programs from v14): how the Controls card reads and words them. */

export interface ExitSetting { key: string; label: string; low: number; high: number; built_in: number | null; next_start: number | null; minutes: boolean }
export interface ExitSettingsView { supported: boolean; running: boolean; phrase: string; settings: ExitSetting[]; changed: string[]; problem: string | null }

/** 0.45 as "45%", 180 minutes as "180 min". */
export function shown(s: Pick<ExitSetting, "minutes">, value: number | null): string {
  if (value === null || !Number.isFinite(value)) return "—";
  return s.minutes ? `${Math.round(value)} min` : `${Math.round(value * 1000) / 10}%`;
}

/** What the operator typed ("25", "25%", "210") as the value the server stores, or null when it is not a number. */
export function typed(s: Pick<ExitSetting, "minutes">, text: string): number | null {
  const n = Number(text.replace(/[%\s]|min/gi, ""));
  if (text.trim() === "" || !Number.isFinite(n)) return null;
  return s.minutes ? n : Math.round(n * 10) / 1000;
}

/** Why a typed value cannot be sent, or null when it can. */
export function problemWith(s: ExitSetting, text: string): string | null {
  const v = typed(s, text);
  if (v === null) return "enter a number";
  if (s.minutes && v !== Math.round(v)) return "whole minutes only";
  if (v < s.low || v > s.high) return `between ${shown(s, s.low)} and ${shown(s, s.high)}`;
  return null;
}

/** Only the settings whose typed value differs from what the program would start with. */
export function changes(settings: ExitSetting[], form: Record<string, string>): Record<string, number> {
  const out: Record<string, number> = {};
  for (const s of settings) {
    const v = typed(s, form[s.key] ?? "");
    if (v !== null && s.next_start !== null && Math.abs(v - s.next_start) > 1e-9) out[s.key] = v;
  }
  return out;
}
