/** The desk's one date format (lib/format.ts): "06 Nov 2026", "06 Nov 2026, 12:14". */
import { test } from "node:test";
import assert from "node:assert/strict";

import { dateTimeLabel, dayLabel, istDateTime, tradeTime } from "../lib/format.ts";
import { calendarDay } from "../lib/holidays.ts";

test("a date reads day first, month in words, with the year", () => {
  assert.equal(dayLabel("2026-11-06"), "06 Nov 2026");
  assert.equal(dayLabel("2026-11-06T15:25:12"), "06 Nov 2026");
  assert.equal(dayLabel(null), "—");
});

test("a timestamp is the same date with the IST time, never re-zoned", () => {
  assert.equal(dateTimeLabel("2026-11-06T12:14:05"), "06 Nov 2026, 12:14");
  assert.equal(dateTimeLabel("2026-11-06 13:16"), "06 Nov 2026, 13:16");
  assert.equal(dateTimeLabel("2026-11-06"), "06 Nov 2026");
});

test("the blotter, version history and journal all use it", () => {
  assert.equal(tradeTime("2026-11-06 12:14"), "06 Nov 2026, 12:14");
  assert.equal(istDateTime("2026-10-21T10:45:00"), "21 Oct 2026, 10:45");
});

test("holidays keep their weekday, with the same day and month", () => {
  assert.equal(calendarDay("2026-10-02"), "Fri 02 Oct");
  assert.equal(calendarDay("2026-10-02", true), "Fri 02 Oct 2026");
});
