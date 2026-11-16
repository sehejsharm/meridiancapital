import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import { ageText, keyBytes, parseLastFigures, pushSupport } from "../lib/phone.ts";

const base = { hasServiceWorker: true, hasPushManager: true, hasNotification: true, permission: "default", isIos: false, standalone: false };

test("an iPhone must add the desk to its home screen before notifications can be switched on", () => {
  assert.equal(pushSupport({ ...base, isIos: true, hasPushManager: false }), "install-first");
  assert.equal(pushSupport({ ...base, isIos: true, standalone: true }), "ready");
  assert.equal(pushSupport(base), "ready");
  assert.equal(pushSupport({ ...base, hasPushManager: false }), "unsupported");
  assert.equal(pushSupport({ ...base, permission: "denied" }), "blocked");
});

test("the server's key becomes the 65 bytes the browser asks for", () => {
  const key = keyBytes("BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4");
  assert.equal(key.length, 65);
  assert.equal(key[0], 4);
});

test("how old the offline figures are", () => {
  const now = Date.parse("2026-11-16T10:00:00Z");
  assert.equal(ageText("2026-11-16T09:59:40Z", now), "under a minute ago");
  assert.equal(ageText("2026-11-16T09:57:00Z", now), "3 min ago");
  assert.equal(ageText("2026-11-16T07:55:00Z", now), "2 h 5 min ago");
  assert.equal(ageText("2026-11-14T09:00:00Z", now), "2 days ago");
  assert.equal(ageText("nonsense", now), "at an unknown time");
});

test("stored figures are read back, and anything damaged is ignored", () => {
  assert.equal(parseLastFigures(null), null);
  assert.equal(parseLastFigures("{oops"), null);
  assert.deepEqual(parseLastFigures(JSON.stringify({ ts: "2026-11-16T09:00:00Z", day_pl: 450, equity: "x", position: null })),
    { ts: "2026-11-16T09:00:00Z", day_pl: 450, equity: null, position: null, unrealised: null });
});

test("the service worker never keeps a page or an API answer", () => {
  const sw = readFileSync(new URL("../public/sw.js", import.meta.url), "utf8");
  const puts = sw.match(/cache\.(put|add|addAll)\([^)]*\)/g) ?? [];
  assert.ok(puts.length >= 3);
  for (const p of puts) assert.ok(/OFFLINE|icons|req, res\.clone\(\)|\(f\)/.test(p), p);
  assert.match(sw, /url\.pathname\.startsWith\("\/_next\/static\/"\)/);   // the only thing answered from the cache
  assert.ok(!/\/api/.test(sw.replace(/\/\*[\s\S]*?\*\/|\/\/.*$/gm, "")));   // the API is not mentioned in code at all
});
