import { test } from "node:test";
import assert from "node:assert/strict";

import { deviceLabel, safeNext, sameOrigin } from "../lib/request-guard.ts";

const h = (o: Record<string, string>) => new Headers(o);

test("a request from the desk's own page is same-origin", () => {
  assert.ok(sameOrigin(h({ host: "desk.example.com", origin: "https://desk.example.com", "sec-fetch-site": "same-origin" })));
  assert.ok(sameOrigin(h({ host: "internal", "x-forwarded-host": "desk.example.com", origin: "https://desk.example.com" })));
  assert.ok(sameOrigin(h({ host: "127.0.0.1:3100", origin: "http://127.0.0.1:3100" })));
});

test("another site cannot make the operator's browser act on the desk", () => {
  assert.ok(!sameOrigin(h({ host: "desk.example.com", origin: "https://evil.example", "sec-fetch-site": "cross-site" })));
  assert.ok(!sameOrigin(h({ host: "desk.example.com", origin: "https://evil.example" })));
  assert.ok(!sameOrigin(h({ host: "desk.example.com", "sec-fetch-site": "cross-site" })));
  assert.ok(!sameOrigin(h({ host: "desk.example.com", "sec-fetch-site": "same-site", origin: "https://blog.example.com" })));
  assert.ok(!sameOrigin(h({ host: "desk.example.com", origin: "null" })));
  assert.ok(!sameOrigin(h({ origin: "https://desk.example.com" })), "nothing to compare with is a refusal");
});

test("after sign-in the desk only ever goes to one of its own pages", () => {
  assert.equal(safeNext("/trades"), "/trades");
  assert.equal(safeNext("/trades/12?tab=path#top"), "/trades/12?tab=path#top");
  for (const bad of ["//evil.example", "/\\evil.example", "https://evil.example", "javascript:alert(1)",
                     "evil.example", "/%0d%0a", "\\\\evil.example", "/\t/evil.example", ""]) {
    const went = safeNext(bad);
    assert.ok(went.startsWith("/") && !went.startsWith("//"), `${bad} -> ${went}`);
    assert.ok(!went.includes("evil.example") || bad === "/%0d%0a", `${bad} -> ${went}`);
  }
  assert.equal(safeNext(null), "/");
  assert.equal(safeNext("x".repeat(3000)), "/");
});

test("devices are named by platform and browser", () => {
  assert.equal(
    deviceLabel("Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1"),
    "iPhone · Safari",
  );
  assert.equal(
    deviceLabel("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"),
    "Mac · Chrome",
  );
  assert.equal(
    deviceLabel("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0"),
    "Windows PC · Edge",
  );
  assert.equal(deviceLabel("Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Mobile Safari/537.36"), "Android phone · Chrome");
  assert.equal(deviceLabel(null), "Unknown device · browser");
});
