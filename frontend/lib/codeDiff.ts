import type { AlgoVersion } from "@/lib/types";

/**
 * The code viewer's diff, as the server sends it (GET /api/algos/{id}/diff).
 * Every credential line is blanked on the server before the diff is taken;
 * the browser never receives the original line (backend/app/code_redact.py).
 */
export interface DiffLine {
  kind: " " | "+" | "-";
  a: number | null;
  b: number | null;
  text: string;
}

export interface DiffHunk {
  a_start: number;
  a_len: number;
  b_start: number;
  b_len: number;
  lines: DiffLine[];
}

export interface CodeDiff {
  a: Pick<AlgoVersion, "id" | "version" | "created_ts" | "sha256">;
  b: Pick<AlgoVersion, "id" | "version" | "created_ts" | "sha256">;
  hunks: DiffHunk[];
  added: number;
  removed: number;
  identical: boolean;
  lines: { a: number; b: number };
  hidden: { a: number; b: number };
}

export const HIDDEN_MARKER = "credential line hidden";

/** Compare the newest upload with the one before it, older on the left. */
export function defaultPair(versions: AlgoVersion[]): { a: number; b: number } | null {
  if (versions.length < 2) return null;
  const sorted = [...versions].sort((x, y) => y.version - x.version);
  return { a: sorted[1].id, b: sorted[0].id };
}

/** "@@ -12,7 +12,8 @@", as in any unified diff. */
export function hunkHeader(h: DiffHunk): string {
  return `@@ -${h.a_start},${h.a_len} +${h.b_start},${h.b_len} @@`;
}

/** A line the server blanked because it held, or might have held, a credential. */
export function isHidden(text: string): boolean {
  return text.includes(HIDDEN_MARKER);
}
