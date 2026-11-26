/**
 * Face ID sign-in, relayed to the control plane.
 *
 * Separate from the authenticated proxy because these three calls happen
 * *before* there is a session: /api/session and its children are the only
 * unauthenticated routes the proxy lets through. It forwards no session — what
 * proves something here is the signature the device produces over a challenge
 * the control plane issued. A passkey that verifies approves this device.
 *
 *   GET  → is Face ID offered at all
 *   POST → start a ceremony (challenge)
 *   PUT  → finish it, and set the session cookie on success
 */

import { NextRequest, NextResponse } from "next/server";

import { clientIp, overLimit } from "@/lib/edge-throttle";
import { sameOrigin } from "@/lib/request-guard";
import {
  ConfigError,
  clientIpHeaders,
  controlPlane,
  currentDevice,
  deviceHeaders,
  keepDevice,
  setSessionCookie,
} from "@/lib/server-api";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const JSON_NO_STORE = { "Content-Type": "application/json", "Cache-Control": "no-store" };

function guard(request: NextRequest): NextResponse | null {
  if (!sameOrigin(request.headers)) {
    return NextResponse.json({ detail: "refused: this request did not come from the desk itself" }, { status: 403 });
  }
  // The same first line in front of the control plane as the PIN route.
  const wait = overLimit(clientIp(request));
  if (wait !== null) {
    return NextResponse.json(
      { detail: `too many attempts; retry in ${wait}s` },
      { status: 429, headers: { "Retry-After": String(wait) } },
    );
  }
  return null;
}

function unreachable(error: unknown) {
  if (error instanceof ConfigError) return NextResponse.json({ detail: error.message }, { status: 503 });
  return NextResponse.json({ detail: "control plane unreachable" }, { status: 502 });
}

export async function GET() {
  try {
    const res = await controlPlane("/api/auth/passkeys/available");
    return new NextResponse(await res.text(), { status: res.status, headers: JSON_NO_STORE });
  } catch {
    // Not being able to ask is not a failure worth showing — the PIN still works.
    return NextResponse.json({ available: false, configured: false });
  }
}

export async function POST(request: NextRequest) {
  const refused = guard(request);
  if (refused) return refused;
  try {
    const res = await controlPlane("/api/auth/passkeys/authenticate/options", {
      method: "POST",
      headers: { "Content-Type": "application/json", ...clientIpHeaders(request) },
      body: "{}",
    });
    return new NextResponse(await res.text(), { status: res.status, headers: JSON_NO_STORE });
  } catch (error) {
    return unreachable(error);
  }
}

export async function PUT(request: NextRequest) {
  const refused = guard(request);
  if (refused) return refused;
  let payload: string;
  try {
    payload = await request.text();
    if (payload.length > 64 * 1024) throw new Error("too large");
    JSON.parse(payload);
  } catch {
    return NextResponse.json({ detail: "malformed request" }, { status: 400 });
  }

  const device = currentDevice(request);
  try {
    const res = await controlPlane("/api/auth/passkeys/authenticate", {
      method: "POST",
      headers: { "Content-Type": "application/json", ...clientIpHeaders(request), ...deviceHeaders(device, request) },
      body: payload,
    });
    const body = await res.text();
    if (res.status !== 200) {
      return new NextResponse(body, { status: res.status, headers: JSON_NO_STORE });
    }
    const { token, expires_in } = JSON.parse(body) as { token: string; expires_in: number };
    const response = NextResponse.json({ ok: true }, { headers: { "Cache-Control": "no-store" } });
    await setSessionCookie(response, token, expires_in);
    keepDevice(response, device);
    return response;
  } catch (error) {
    return unreachable(error);
  }
}
