/**
 * Face ID sign-in, relayed to the control plane.
 *
 * Separate from the authenticated proxy because these three calls happen
 * *before* there is a session: this route and /api/session are the only
 * unauthenticated ones the middleware lets through. It forwards no cookie and
 * no token — the only thing that proves anything here is the signature the
 * device produces over a challenge the control plane issued.
 *
 *   GET  → is Face ID offered at all
 *   POST → start a ceremony (challenge)
 *   PUT  → finish it, and set the session cookie on success
 */

import { NextRequest, NextResponse } from "next/server";

import { SESSION_COOKIE, apiBase } from "@/lib/server-api";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

async function forward(path: string, init?: RequestInit) {
  const res = await fetch(`${apiBase()}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    cache: "no-store",
  });
  const body = await res.text();
  return { status: res.status, body };
}

export async function GET() {
  try {
    const { status, body } = await forward("/api/auth/passkeys/available");
    return new NextResponse(body, {
      status,
      headers: { "Content-Type": "application/json", "Cache-Control": "no-store" },
    });
  } catch {
    // Not being able to ask is not a failure worth showing — the PIN still works.
    return NextResponse.json({ available: false, configured: false, enrolled: 0 });
  }
}

export async function POST() {
  try {
    const { status, body } = await forward("/api/auth/passkeys/authenticate/options", {
      method: "POST",
      body: "{}",
    });
    return new NextResponse(body, {
      status,
      headers: { "Content-Type": "application/json", "Cache-Control": "no-store" },
    });
  } catch {
    return NextResponse.json({ detail: "control plane unreachable" }, { status: 502 });
  }
}

export async function PUT(request: NextRequest) {
  let payload: string;
  try {
    payload = await request.text();
    JSON.parse(payload);
  } catch {
    return NextResponse.json({ detail: "malformed request" }, { status: 400 });
  }

  try {
    const { status, body } = await forward("/api/auth/passkeys/authenticate", {
      method: "POST",
      body: payload,
    });
    if (status !== 200) {
      return new NextResponse(body, {
        status,
        headers: { "Content-Type": "application/json" },
      });
    }

    const { token, expires_in } = JSON.parse(body) as { token: string; expires_in: number };
    const response = NextResponse.json({ ok: true });
    response.cookies.set({
      name: SESSION_COOKIE,
      value: token,
      httpOnly: true,
      secure: process.env.NODE_ENV === "production",
      sameSite: "lax",
      path: "/",
      maxAge: expires_in,
    });
    return response;
  } catch {
    return NextResponse.json({ detail: "control plane unreachable" }, { status: 502 });
  }
}
