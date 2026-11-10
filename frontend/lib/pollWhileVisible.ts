import { startVisiblePoller } from "@/lib/visiblePoller";

/**
 * Run `run` now, then every `ms` while the page is visible; returns the stop
 * function, so it drops into an effect in place of setInterval:
 *
 *     useEffect(() => pollWhileVisible(() => void load(), 30_000), [load]);
 *
 * A hidden page makes no requests; shown again, it reads at once if what it
 * holds is older than `ms` (lib/visiblePoller.ts has the reasoning and tests).
 */
export function pollWhileVisible(run: () => void, ms: number): () => void {
  const poller = startVisiblePoller(run, ms, {
    isVisible: () => document.visibilityState === "visible",
    onVisibility: (cb) => {
      document.addEventListener("visibilitychange", cb);
      window.addEventListener("pageshow", cb);
      return () => {
        document.removeEventListener("visibilitychange", cb);
        window.removeEventListener("pageshow", cb);
      };
    },
    setInterval: (cb, t) => window.setInterval(cb, t),
    clearInterval: (h) => window.clearInterval(h as number),
    now: () => Date.now(),
  });
  return poller.stop;
}
