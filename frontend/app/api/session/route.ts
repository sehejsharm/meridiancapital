import { NextRequest, NextResponse } from "next/server";

import { clientIp, overLimit } from "@/lib/edge-throttle";
import { HONEYPOT_FIELD } from "@/lib/honeypot";
import { ApiError, ConfigError, SESSION_COOKIE, clientIpHeaders, login } from "@/lib/server-api";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(request: NextRequest) {
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
    if (typeof body.password !== "string" || !body.password) {
      return NextResponse.json({ detail: "password is required" }, { status: 400 });
    }
    password = body.password;
  } catch {
    return NextResponse.json({ detail: "malformed request" }, { status: 400 });
  }

  try {
    const { token, expires_in } = await login(password, clientIpHeaders(request));
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
  } catch (error) {
    if (error instanceof ConfigError) {
      return NextResponse.json({ detail: error.message }, { status: 503 });
    }
    if (error instanceof ApiError) {
      return NextResponse.json({ detail: error.message }, { status: error.status });
    }
    return NextResponse.json({ detail: "control plane unreachable" }, { status: 502 });
  }
}

export async function DELETE() {
  const response = NextResponse.json({ ok: true });
  response.cookies.set({ name: SESSION_COOKIE, value: "", path: "/", maxAge: 0 });
  return response;
}
