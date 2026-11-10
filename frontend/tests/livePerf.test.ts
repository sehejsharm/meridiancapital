/**
 * The two things that keep an idle dashboard idle (10 Nov 2026):
 * a status push that only moved the server's clock is not a change
 * (lib/liveStatus.ts), and nothing polls while the page is hidden
 * (lib/visiblePoller.ts).
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import { STATUS_REFRESH_MS, stableKey, statusChanged } from "../lib/liveStatus.ts";
import { startVisiblePoller, type PollerEnv } from "../lib/visiblePoller.ts";

const status = (now: string, uptime: number, running = true) => ({
  engine: { running, pid: 42, uptime_sec: uptime },
  schedule: { enabled: true, now_ist: now, last_decision: "in the session window" },
  fleet: { algos: [{ algo_id: "og", running, uptime_sec: uptime, mode: "live" }], running: 1 },
});

test("a status that only moved its clock and uptimes is the same status", () => {
  assert.equal(stableKey(status("2026-11-10T10:00:01", 60)), stableKey(status("2026-11-10T10:00:02", 61)));
});

test("anything real is a change", () => {
  assert.notEqual(stableKey(status("2026-11-10T10:00:01", 60)), stableKey(status("2026-11-10T10:00:01", 60, false)));
});

test("key order does not matter", () => {
  assert.equal(stableKey({ a: 1, b: { c: 2, d: 3 } }), stableKey({ b: { d: 3, c: 2 }, a: 1 }));
});

test("the first status, a changed one, and a stale copy are taken; a clock-only push is not", () => {
  const k = stableKey(status("2026-11-10T10:00:01", 60));
  assert.ok(statusChanged(null, k, 0, 1000));
  assert.ok(!statusChanged(k, k, 1000, 2000));
  assert.ok(statusChanged(k, stableKey(status("x", 0, false)), 1000, 2000));
  assert.ok(statusChanged(k, k, 1000, 1000 + STATUS_REFRESH_MS), "so the date never goes stale");
});

// ── the poller, with a hand-driven clock and visibility ──────────────────────
function world() {
  let visible = true;
  let t = 0;
  let timer: { cb: () => void; ms: number; next: number } | null = null;
  const subs = new Set<() => void>();
  const env: PollerEnv = {
    isVisible: () => visible,
    onVisibility: (cb) => { subs.add(cb); return () => subs.delete(cb); },
    setInterval: (cb, ms) => { timer = { cb, ms, next: t + ms }; return timer; },
    clearInterval: () => { timer = null; },
    now: () => t,
  };
  return {
    env,
    advance(ms: number) {
      const end = t + ms;
      while (timer && timer.next <= end) { t = timer.next; timer.next += timer.ms; timer.cb(); }
      t = end;
    },
    show(v: boolean) { visible = v; subs.forEach((cb) => cb()); },
    armed: () => timer !== null,
    listeners: () => subs.size,
  };
}

test("it reads at once, then on the interval", () => {
  const w = world(); let n = 0;
  startVisiblePoller(() => n++, 10_000, w.env);
  assert.equal(n, 1);
  w.advance(30_000);
  assert.equal(n, 4);
});

test("a hidden page makes no requests at all", () => {
  const w = world(); let n = 0;
  startVisiblePoller(() => n++, 10_000, w.env);
  w.show(false);
  w.advance(600_000);                       // ten minutes in a background tab
  assert.equal(n, 1);
  assert.ok(!w.armed());
});

test("coming back reads immediately and resumes", () => {
  const w = world(); let n = 0;
  startVisiblePoller(() => n++, 10_000, w.env);
  w.show(false); w.advance(600_000);
  w.show(true);
  assert.equal(n, 2, "one read the moment it is shown");
  w.advance(20_000);
  assert.equal(n, 4);
});

test("a quick glance away does not cause an extra read", () => {
  const w = world(); let n = 0;
  startVisiblePoller(() => n++, 10_000, w.env);
  w.advance(2_000); w.show(false); w.advance(1_000); w.show(true);
  assert.equal(n, 1, "the copy on screen is 3 seconds old: no need");
  w.advance(10_000);
  assert.equal(n, 2);
});

test("a page opened in the background reads once it is first shown", () => {
  const w = world(); let n = 0;
  w.show(false);
  startVisiblePoller(() => n++, 10_000, w.env, false);
  w.advance(60_000);
  assert.equal(n, 0);
  w.show(true);
  assert.equal(n, 1);
});

test("stopping removes the timer and the listener", () => {
  const w = world(); let n = 0;
  const p = startVisiblePoller(() => n++, 10_000, w.env);
  p.stop();
  w.advance(60_000); w.show(false); w.show(true);
  assert.equal(n, 1);
  assert.ok(!w.armed());
  assert.equal(w.listeners(), 0);
});
