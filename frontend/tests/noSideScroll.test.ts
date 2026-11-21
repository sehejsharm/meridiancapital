/**
 * Nothing on the dashboard scrolls sideways, on a phone or a desktop (the
 * operator's rule, 7 Nov 2026). Content too wide for the screen wraps or drops
 * columns; the page itself is clipped in app/globals.css. This keeps sideways
 * scroll boxes from creeping back into the code.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";

const ROOTS = ["app", "components"];
// A box that scrolls on both axes, or only sideways, and a table forced wider than its box.
const FORBIDDEN = [
  /\boverflow-x-(auto|scroll)\b/,
  /(?<![-\w])overflow-(auto|scroll)\b/,
  /<table[^>]*\bmin-w-\[/,
  /\bwhitespace-pre(?![-\w])/,
];

function files(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) return files(p);
    return /\.(tsx|ts)$/.test(name) ? [p] : [];
  });
}

test("no element scrolls sideways", () => {
  const hits: string[] = [];
  for (const f of ROOTS.flatMap(files)) {
    readFileSync(f, "utf8").split("\n").forEach((line, i) => {
      for (const re of FORBIDDEN) if (re.test(line)) hits.push(`${f}:${i + 1}: ${line.trim().slice(0, 100)}`);
    });
  }
  assert.deepEqual(hits, [], "wrap the content or hide columns on small screens instead");
});

test("the page itself is clipped sideways", () => {
  const css = readFileSync("app/globals.css", "utf8");
  assert.match(css, /overflow-x:\s*clip/);
  assert.match(css, /overscroll-behavior:\s*none/);
});

test("a grid that sets columns for wide screens also sets them for phones", () => {
  // `grid xl:grid-cols-[3fr_2fr]` leaves phones with an implicit column that Safari sizes to the widest
  // thing inside it (a chart's declared width, a long table), so the cards run off the screen and the
  // clipped page cuts them off (21 Nov 2026, the Deck's option-flow and liquidity cards on an iPhone).
  // Chromium does not reproduce it, so no browser check here can catch it: this rule does.
  const hits: string[] = [];
  for (const f of ROOTS.flatMap(files)) {
    readFileSync(f, "utf8").split("\n").forEach((line, i) => {
      for (const m of line.matchAll(/className=(?:"([^"]*)"|\{`([^`]*)`\})/g)) {
        const cls = (m[1] ?? m[2] ?? "").split(/\s+/);
        if (!cls.includes("grid")) continue;
        const wide = cls.some((c) => /^(sm|md|lg|xl|2xl):grid-cols-/.test(c));
        const base = cls.some((c) => /^grid-cols-/.test(c));
        if (wide && !base) hits.push(`${f}:${i + 1}: ${line.trim().slice(0, 110)}`);
      }
    });
  }
  assert.deepEqual(hits, [], "add grid-cols-1 (or the phone's own column count) beside the wide-screen columns");
});

test("a chart's svg takes its width from the page, never a fixed width attribute", () => {
  // An svg carrying width={W} declares itself W pixels wide to the layout; on a phone that widens the column.
  // (An icon's width={size}, a few pixels, is fine.)
  const hits: string[] = [];
  for (const f of ROOTS.flatMap(files)) {
    const src = readFileSync(f, "utf8");
    for (const m of src.matchAll(/<svg\b[^>]*?\swidth=\{(?!size\})[A-Za-z_][\w.]*\}/gs)) hits.push(`${f}: ${m[0].replace(/\s+/g, " ").slice(0, 90)}`);
  }
  assert.deepEqual(hits, [], "use viewBox with className=\"w-full\" instead");
});
