import { test } from "node:test";
import assert from "node:assert/strict";

import { EtagCache } from "../lib/etagCache.ts";

test("a 304 hands back the very object kept with the ETag", () => {
  const cache = new EtagCache();
  const answer = { rows: [1, 2, 3] };
  cache.keep("/api/proxy/trades", 'W/"abc"', answer);
  assert.equal(cache.etag("/api/proxy/trades"), 'W/"abc"');
  assert.equal(cache.data("/api/proxy/trades"), answer);
  assert.equal(cache.etag("/api/proxy/other"), undefined);
  assert.equal(cache.has("/api/proxy/other"), false);
});

test("an answer without an ETag drops the kept one instead of pairing old tag with new data", () => {
  const cache = new EtagCache();
  cache.keep("/u", 'W/"1"', { v: 1 });
  cache.keep("/u", null, { v: 2 });
  assert.equal(cache.etag("/u"), undefined);
  assert.equal(cache.has("/u"), false);
});

test("bounded: the least recently used URL goes first", () => {
  const cache = new EtagCache(2);
  cache.keep("/a", "a", 1);
  cache.keep("/b", "b", 2);
  cache.data("/a"); // /a is still being polled
  cache.keep("/c", "c", 3);
  assert.equal(cache.has("/a"), true);
  assert.equal(cache.has("/b"), false);
  assert.equal(cache.has("/c"), true);
  cache.clear();
  assert.equal(cache.has("/a"), false);
});
