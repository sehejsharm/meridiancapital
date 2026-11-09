/** The deck's notice for Muhurat trading and other special sessions (lib/holidays.ts). */
import { test } from "node:test";
import assert from "node:assert/strict";

import { specialNotice } from "../lib/holidays.ts";

const muhurat = { day: "2026-11-08", open: "14:20", close: "15:30", label: "Muhurat trading (Diwali)" };
const today = { ...muhurat, runs_from: "13:50", stops_at: "15:25" };

test("the day before, it is announced with its times", () => {
  const n = specialNotice({ now_ist: "2026-11-07T11:00:00", special_today: null, special_upcoming: [muhurat] })!;
  assert.equal(n.tone, "ahead");
  assert.equal(n.title, "Muhurat trading (Diwali) on Sun 08 Nov, 14:20–15:30 IST");
  assert.match(n.detail, /nothing starts on its own/);
});

test("on the day it says when to start and when it stops", () => {
  const n = specialNotice({ now_ist: "2026-11-08T10:00:00", special_today: today, in_special_window: false, special_upcoming: [muhurat] })!;
  assert.equal(n.title, "Muhurat trading (Diwali) today, 14:20–15:30 IST");
  assert.match(n.detail, /from 13:50; it runs until 15:25/);
});

test("during the window it points at Switch", () => {
  const n = specialNotice({ now_ist: "2026-11-08T14:30:00", special_today: today, in_special_window: true })!;
  assert.match(n.title, /now/);
  assert.match(n.detail, /Switch/);
});

test("once it is over, nothing is said", () => {
  assert.equal(specialNotice({ now_ist: "2026-11-08T15:26:00", special_today: today, in_special_window: false }), null);
  assert.equal(specialNotice({ now_ist: "2026-11-09T10:00:00", special_today: null, special_upcoming: [] }), null);
  assert.equal(specialNotice(undefined), null);
});
