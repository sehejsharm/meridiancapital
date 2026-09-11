import { NextRequest, NextResponse } from "next/server";

import { ApiError, SESSION_COOKIE, login } from "@/lib/server-api";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(request: NextRequest) {
  let password: string;
  try {
    const body = (await request.json()) as { password?: unknown };
    if (typeof body.password !== "string" || !body.password) {
      return NextResponse.json({ detail: "password is required" }, { status: 400 });
    }
    password = body.password;
  } catch {
    return NextResponse.json({ detail: "malformed request" }, { status: 400 });
  }

  try {
    const { token, expires_in } = await login(password);
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
