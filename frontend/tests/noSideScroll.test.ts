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
