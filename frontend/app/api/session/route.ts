/**
 * Sign-in, renewal and sign-out, relayed to the control plane.
 *
 *   POST   → the PIN. From an approved device: a session. From any other device
 *            with the right PIN: 403 with a code to show while an operator approves it.
 *   PUT    → renew a session that is still valid.
 *   DELETE → sign out: the session ends on the control plane, not just here.
 */

import { NextRequest, NextResponse } from "next/server";

import { clientIp, overLimit } from "@/lib/edge-throttle";
import { HONEYPOT_FIELD } from "@/lib/honeypot";
import { sameOrigin } from "@/lib/request-guard";
import {
  ConfigError,
  clearSessionCookie,
  clientIpHeaders,
  controlPlane,
  currentDevice,
  deviceHeaders,
  keepDevice,
  sessionToken,
  setSessionCookie,
} from "@/lib/server-api";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const NO_STORE = { "Cache-Control": "no-store" };

function foreign() {
  return NextResponse.json({ detail: "refused: this request did not come from the desk itself" }, { status: 403 });
}

function unreachable(error: unknown) {
  if (error instanceof ConfigError) return NextResponse.json({ detail: error.message }, { status: 503 });
  return NextResponse.json({ detail: "control plane unreachable" }, { status: 502 });
}

export async function POST(request: NextRequest) {
  if (!sameOrigin(request.headers)) return foreign();
  const wait = overLimit(clientIp(request));
  if (wait !== null) {
    return NextResponse.json(
      { detail: `too many attempts; retry in ${wait}s` },
      { status: 429, headers: { "Retry-After": String(wait) } },
    );
  }

  let password: string;
  try {
    const body = (await request.json()) as Record<string, unknown>;
    // Anything in the honeypot is a bot. It gets the same answer a wrong
    // password would, so it learns nothing, and the control plane never sees
    // it — a bot must not be able to spend the operator's lockout budget.
    if (typeof body[HONEYPOT_FIELD] === "string" && body[HONEYPOT_FIELD] !== "") {
      await new Promise((r) => setTimeout(r, 600));
      return NextResponse.json({ detail: "invalid password" }, { status: 401 });
    }
    if (typeof body.password !== "string" || !body.password || body.password.length > 512) {
      return NextResponse.json({ detail: "password is required" }, { status: 400 });
    }
    password = body.password;
  } catch {
    return NextResponse.json({ detail: "malformed request" }, { status: 400 });
  }

  const device = currentDevice(request);
  try {
    const res = await controlPlane("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json", ...clientIpHeaders(request), ...deviceHeaders(device, request) },
      body: JSON.stringify({ password }),
    });
    const data = (await res.json().catch(() => ({}))) as Record<string, unknown>;
    let response: NextResponse;
    if (res.ok && typeof data.token === "string") {
      response = NextResponse.json({ ok: true }, { headers: NO_STORE });
      await setSessionCookie(response, data.token, Number(data.expires_in) || 3600);
    } else if (res.status === 403 && data.pending === true) {
      // The right PIN on a device nobody has approved yet: show the code.
      response = NextResponse.json(
        { pending: true, code: data.code, expires_in: data.expires_in, detail: data.detail },
        { status: 403, headers: NO_STORE },
      );
    } else {
      response = NextResponse.json(
        { detail: typeof data.detail === "string" ? data.detail : "sign-in failed" },
        { status: res.status, headers: NO_STORE },
      );
    }
    keepDevice(response, device);
    return response;
  } catch (error) {
    return unreachable(error);
  }
}

/** Renew a session that is still valid: an open dashboard is not signed out mid-session. */
export async function PUT(request: NextRequest) {
  if (!sameOrigin(request.headers)) return foreign();
  const token = await sessionToken();
  if (!token) return NextResponse.json({ detail: "not signed in" }, { status: 401 });
  const device = currentDevice(request);
  try {
    const res = await controlPlane("/api/auth/renew", {
      method: "POST",
      headers: { Authorization: `Bearer ${token}`, ...clientIpHeaders(request), ...deviceHeaders(device, request) },
    });
    if (!res.ok) {
      const response = NextResponse.json(
        { detail: res.status === 401 ? "session expired" : "could not renew the session" },
        { status: res.status },
      );
      if (res.status === 401) clearSessionCookie(response);
      return response;
    }
    const renewed = (await res.json()) as { token: string; expires_in: number };
    const response = NextResponse.json({ ok: true, expires_in: renewed.expires_in }, { headers: NO_STORE });
    await setSessionCookie(response, renewed.token, renewed.expires_in);
    keepDevice(response, device);
    return response;
  } catch (error) {
    return unreachable(error);
  }
}

export async function DELETE(request: NextRequest) {
  if (!sameOrigin(request.headers)) return foreign();
  const token = await sessionToken();
  if (token) {
    // End it on the control plane too, so the token is dead wherever a copy of it is.
    try {
      await controlPlane("/api/auth/logout", {
        method: "POST",
        headers: { Authorization: `Bearer ${token}`, ...clientIpHeaders(request) },
        signal: AbortSignal.timeout(5000),
      });
    } catch {
      /* the cookie goes regardless */
    }
  }
  const response = NextResponse.json({ ok: true }, { headers: NO_STORE });
  clearSessionCookie(response);
  return response;
}
