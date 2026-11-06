/** The Reliability page's wording (lib/reliability.ts). */
import { test } from "node:test";
import assert from "node:assert/strict";

import { trendText, uptimeTone } from "../lib/reliability.ts";

test("the week-on-week trend says which way and how much", () => {
  assert.equal(trendText({ this_week: 14, last_week: 10, change_pct: 40 }), "14 this week, up 40% on last week's 10");
  assert.equal(trendText({ this_week: 5, last_week: 10, change_pct: -50 }), "5 this week, down 50% on last week's 10");
  assert.equal(trendText({ this_week: 3, last_week: 0, change_pct: null }), "3 this week, none last week");
  assert.equal(trendText(null), "not enough history for a week-on-week comparison");
});

test("uptime is judged against market hours", () => {
  assert.equal(uptimeTone(100), "good");
  assert.equal(uptimeTone(95), "warning");
  assert.equal(uptimeTone(76.4), "critical", "21 Oct 2026's crash loop");
  assert.equal(uptimeTone(null), "neutral");
});
