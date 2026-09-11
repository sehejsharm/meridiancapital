/**
 * Mints a short-lived WebSocket ticket and returns the full socket URL.
 *
 * Vercel's serverless runtime cannot hold a long-lived socket, so the browser
 * connects straight to the Oracle host. It gets a ~60s single-purpose ticket
 * rather than the session token, so a URL captured in a log is worthless within
 * the minute and can never be replayed against the REST API.
 */

import { NextResponse } from "next/server";

import { ApiError, callApi, websocketUrl } from "@/lib/server-api";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST() {
  try {
    const { ticket, expires_in } = await callApi<{ ticket: string; expires_in: number }>(
      "/api/auth/ws-ticket",
      { method: "POST" },
    );
    return NextResponse.json({ url: websocketUrl(ticket), expiresIn: expires_in });
  } catch (error) {
    if (error instanceof ApiError) {
      return NextResponse.json({ detail: error.message }, { status: error.status });
    }
    return NextResponse.json({ detail: "control plane unreachable" }, { status: 502 });
  }
}
