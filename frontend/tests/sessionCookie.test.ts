import { test } from "node:test";
import assert from "node:assert/strict";

import {
  b64u,
  deviceCookieName,
  newDeviceSecret,
  openSession,
  sealSession,
  sessionCookieName,
  staleSessionCookieNames,
  unb64u,
  validDeviceSecret,
} from "../lib/session-cookie.ts";

function jwt(exp: number): string {
  const part = (o: object) => b64u(new TextEncoder().encode(JSON.stringify(o)));
  return `${part({ alg: "HS256", typ: "JWT" })}.${part({ sub: "sehej", exp })}.c2lnbmF0dXJl`;
}

function withSecret<T>(secret: string | undefined, run: () => Promise<T>): Promise<T> {
  const before = { s: process.env.MERIDIAN_SESSION_SECRET, r: process.env.MERIDIAN_RELAY_SECRET };
  delete process.env.MERIDIAN_RELAY_SECRET;
  if (secret === undefined) delete process.env.MERIDIAN_SESSION_SECRET;
  else process.env.MERIDIAN_SESSION_SECRET = secret;
  return run().finally(() => {
    if (before.s === undefined) delete process.env.MERIDIAN_SESSION_SECRET;
    else process.env.MERIDIAN_SESSION_SECRET = before.s;
    if (before.r !== undefined) process.env.MERIDIAN_RELAY_SECRET = before.r;
  });
}

const NOW = Date.UTC(2026, 10, 26, 6, 0, 0);
const TOKEN = jwt(NOW / 1000 + 3600);

test("a sealed cookie opens to the token, and nothing in it is readable", () =>
  withSecret("s".repeat(48), async () => {
    const cookie = await sealSession(TOKEN, 3600, NOW);
    assert.match(cookie, /^v1\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$/);
    assert.ok(!cookie.includes(TOKEN.split(".")[1]), "the token's claims must not show through");
    assert.equal(await openSession(cookie, NOW + 1000), TOKEN);
  }));

test("an altered, made-up or expired cookie opens to nothing", () =>
  withSecret("s".repeat(48), async () => {
    const cookie = await sealSession(TOKEN, 3600, NOW);
    const [v, iv, body] = cookie.split(".");
    const flipped = unb64u(body);
    flipped[3] ^= 1;
    assert.equal(await openSession(`${v}.${iv}.${b64u(flipped)}`, NOW), null);
    assert.equal(await openSession(TOKEN, NOW), null, "a bare token is not a sealed cookie");
    assert.equal(await openSession("anything", NOW), null);
    assert.equal(await openSession("", NOW), null);
    assert.equal(await openSession(cookie, NOW + 3601 * 1000), null);
  }));

test("a cookie sealed under another deployment's secret opens to nothing", async () => {
  const cookie = await withSecret("a".repeat(48), () => sealSession(TOKEN, 3600, NOW));
  await withSecret("b".repeat(48), async () => {
    assert.equal(await openSession(cookie, NOW), null);
  });
});

test("the relay secret seals when no session secret is set", async () => {
  const before = process.env.MERIDIAN_RELAY_SECRET;
  delete process.env.MERIDIAN_SESSION_SECRET;
  process.env.MERIDIAN_RELAY_SECRET = "r".repeat(64);
  try {
    const cookie = await sealSession(TOKEN, 3600, NOW);
    assert.ok(cookie.startsWith("v1."));
    assert.equal(await openSession(cookie, NOW), TOKEN);
  } finally {
    if (before === undefined) delete process.env.MERIDIAN_RELAY_SECRET;
    else process.env.MERIDIAN_RELAY_SECRET = before;
  }
});

test("without a secret the cookie is the bare token, checked for shape and expiry", () =>
  withSecret(undefined, async () => {
    assert.equal(await sealSession(TOKEN, 3600, NOW), TOKEN);
    assert.equal(await openSession(TOKEN, NOW), TOKEN);
    assert.equal(await openSession(jwt(NOW / 1000 - 1), NOW), null);
    assert.equal(await openSession("x", NOW), null);
    assert.equal(await openSession("a.b.c", NOW), null);
  }));

test("device secrets are long and random, and junk is not one", () => {
  const a = newDeviceSecret();
  const b = newDeviceSecret();
  assert.notEqual(a, b);
  assert.ok(validDeviceSecret(a) && a.length >= 43);
  assert.ok(!validDeviceSecret("short"));
  assert.ok(!validDeviceSecret("x".repeat(40) + "!"));
  assert.ok(!validDeviceSecret(undefined));
});

test("on Vercel the cookies carry the __Host- prefix", () => {
  const before = process.env.VERCEL;
  process.env.VERCEL = "1";
  try {
    assert.equal(sessionCookieName(), "__Host-meridian_session");
    assert.equal(deviceCookieName(), "__Host-meridian_device");
    assert.deepEqual(staleSessionCookieNames(), ["meridian_session"]);
  } finally {
    if (before === undefined) delete process.env.VERCEL;
    else process.env.VERCEL = before;
  }
  assert.equal(sessionCookieName(), "meridian_session");
});
