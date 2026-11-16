import type { HostReport } from "./types";

export type Tone = "good" | "warning" | "critical" | "neutral";

function ago(hours: number): string {
  if (hours < 1) return "under an hour ago";
  if (hours < 36) return `${Math.round(hours)} hours ago`;
  return `${Math.round(hours / 24)} days ago`;
}

/** One line and a tone for each thing the desk's housekeeping looks after. */
export function serverLines(h: HostReport): { key: string; label: string; text: string; tone: Tone }[] {
  const out: { key: string; label: string; text: string; tone: Tone }[] = [];
  const m = h.machine;
  const warn = (kind: string) => m.warnings.find((w) => w.kind === kind);
  const tone = (kind: string): Tone => (warn(kind) ? (warn(kind)?.level === "critical" ? "critical" : "warning") : "good");
  out.push({ key: "cpu", label: "Processor", tone: tone("cpu"),
    text: m.cpu.load_pct != null ? `${m.cpu.load_pct.toFixed(0)}% of ${m.cpu.cores} core${m.cpu.cores === 1 ? "" : "s"} (5-minute load)` : "load unavailable" });
  out.push({ key: "memory", label: "Memory", tone: m.memory ? tone("memory") : "neutral",
    text: m.memory ? `${m.memory.used_pct.toFixed(0)}% used of ${(m.memory.total_mb / 1024).toFixed(1)} GB` : "unavailable" });
  out.push({ key: "disk", label: "Disk", tone: m.disk ? tone("disk") : "neutral",
    text: m.disk && m.disk.free_pct != null ? `${m.disk.free_pct.toFixed(0)}% free (${m.disk.free_gb} of ${m.disk.total_gb} GB); warns below 20%` : "unavailable" });
  const u = h.upkeep;
  if (!u) return out;
  const hb = u.heartbeat;
  out.push({ key: "heartbeat", label: "Outside monitor",
    tone: !hb.configured ? "warning" : hb.last_error ? "critical" : hb.last_ok ? "good" : "neutral",
    text: !hb.configured ? "not set up: nothing outside the VM will notice if it goes down"
      : hb.last_error ? `the last ping failed (${hb.last_error})`
      : hb.last_ok ? `pinged at ${hb.last_ok.slice(11, 16)} IST` : "waiting for the first ping" });
  const b = u.backups;
  const bProblem = b.problems.find((p) => p.kind.startsWith("backup") || p.kind.startsWith("off_machine"));
  out.push({ key: "backup", label: "Backup",
    tone: !b.backup ? "warning" : bProblem ? (bProblem.level === "critical" ? "critical" : "warning") : "good",
    text: !b.backup ? "none recorded yet (runs nightly at 22:30 IST)"
      : `${ago(b.backup.age_hours ?? 0)}, ${b.backup.off_machine === "ok" ? "copy sent off the machine" : b.backup.off_machine === "failed" ? "OFF-MACHINE COPY FAILED" : "kept on the VM only"}` });
  const r = b.restore_test;
  out.push({ key: "restore", label: "Restore test",
    tone: !r ? "warning" : !r.ok ? "critical" : (r.age_days ?? 0) > 45 ? "warning" : "good",
    text: !r ? "not run yet (monthly, on the 1st)" : r.ok ? `passed ${ago((r.age_days ?? 0) * 24)}` : `FAILED: ${r.detail}` });
  const c = u.close_check;
  out.push({ key: "close", label: "After-close check",
    tone: !c ? "neutral" : c.status === "ok" ? "good" : c.status === "problem" ? "critical" : "warning",
    text: !c ? "has not run yet (15:35 on trading days)"
      : c.status === "ok"
        ? `${c.day}: flat, ${c.trades} real-money trade${c.trades === 1 ? "" : "s"}${c.independent ? " agree with Angel's books" : "; Angel's books were not read"}`
        : `${c.day}: ${c.findings[0]?.text ?? "needs a look"}${c.findings.length > 1 ? ` (+${c.findings.length - 1} more)` : ""}` });
  return out;
}
