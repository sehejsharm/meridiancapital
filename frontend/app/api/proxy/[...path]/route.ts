/**
 * Relays dashboard calls to the control plane with the session token attached.
 *
 * Only paths under /api/ are forwarded, so a crafted path cannot reach the
 * WebSocket route or anything else the control plane exposes.
 */

import { NextRequest, NextResponse } from "next/server";

import { ApiError, ConfigError, apiBase, sessionToken } from "@/lib/server-api";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const ALLOWED_METHODS = new Set(["GET", "POST", "DELETE"]);

/**
 * How long the relay waits for the control plane. Reads are polled, so a stuck one should fail
 * fast and let the next poll try again. Downloads (log CSVs, PDF reports) and actions are given
 * longer: cutting an action short would not undo it on the server, only lose its answer.
 */
function timeLimitMs(method: string, segments: string[]): number {
  if (method !== "GET") return 120_000;
  const last = segments[segments.length - 1] ?? "";
  if (/\.(csv|pdf|zip)$/i.test(last) || segments.includes("export") || segments.includes("download")) return 120_000;
  return 30_000;
}

async function relay(request: NextRequest, segments: string[]) {
  if (!ALLOWED_METHODS.has(request.method)) {
    return NextResponse.json({ detail: "method not allowed" }, { status: 405 });
  }

  const token = await sessionToken();
  if (!token) {
    return NextResponse.json({ detail: "not signed in" }, { status: 401 });
  }

  // Reject traversal and any attempt to leave the API surface.
  if (segments.some((s) => s === "." || s === ".." || s.includes("/"))) {
    return NextResponse.json({ detail: "invalid path" }, { status: 400 });
  }

  const search = request.nextUrl.search;
  const target = `${apiBase()}/api/${segments.map(encodeURIComponent).join("/")}${search}`;

  let body: string | undefined;
  if (request.method === "POST") {
    body = await request.text();
  }

  const started = performance.now();
  const ifNoneMatch = request.method === "GET" ? request.headers.get("if-none-match") : null;
  const limitMs = timeLimitMs(request.method, segments);
  try {
    const upstream = await fetch(target, {
      method: request.method,
      headers: {
        Authorization: `Bearer ${token}`,
        ...(body ? { "Content-Type": "application/json" } : {}),
        ...(ifNoneMatch ? { "If-None-Match": ifNoneMatch } : {}),
      },
      body: body || undefined,
      cache: "no-store",
      signal: AbortSignal.timeout(limitMs),
    });

    const timing = [upstream.headers.get("server-timing"), `relay;dur=${(performance.now() - started).toFixed(1)}`]
      .filter(Boolean)
      .join(", ");
    const etag = upstream.headers.get("etag");
    // Unchanged since the dashboard's last poll: a few bytes back instead of the whole answer.
    if (upstream.status === 304) {
      return new NextResponse(null, {
        status: 304,
        headers: { "Cache-Control": "no-store", "Server-Timing": timing, ...(etag ? { ETag: etag } : {}) },
      });
    }

    // Read as bytes, not text: a PDF re-encoded through a JS string is a
    // corrupt PDF. This is a pass-through for every content type.
    const payload = await upstream.arrayBuffer();
    const headers = new Headers({
      "Content-Type": upstream.headers.get("content-type") ?? "application/json",
      "Cache-Control": "no-store",
    });
    // Downloads name themselves upstream; without this the browser saves the
    // route segment instead of the report filename.
    const disposition = upstream.headers.get("content-disposition");
    if (disposition) headers.set("Content-Disposition", disposition);
    if (etag) headers.set("ETag", etag);
    // Where a slow call spent its time: "app" is the control plane's own work,
    // "relay" the whole trip from Vercel to the VM and back. A large relay with
    // a small app is the network or DNS, not the server.
    headers.set("Server-Timing", timing);

    return new NextResponse(payload, { status: upstream.status, headers });
  } catch (error) {
    if (error instanceof ConfigError) {
      return NextResponse.json({ detail: error.message }, { status: 503 });
    }
    // The VM did not answer in time: say so at once instead of leaving the page waiting
    // (and the next poll queued behind it) until the hosting platform gives up.
    if (error instanceof DOMException && (error.name === "TimeoutError" || error.name === "AbortError")) {
      return NextResponse.json(
        { detail: `control plane did not answer within ${Math.round(limitMs / 1000)} s` },
        { status: 504 },
      );
    }
    if (error instanceof ApiError) {
      return NextResponse.json({ detail: error.message }, { status: error.status });
    }
    return NextResponse.json({ detail: "control plane unreachable" }, { status: 502 });
  }
}

type Ctx = { params: Promise<{ path: string[] }> };

export async function GET(request: NextRequest, ctx: Ctx) {
  return relay(request, (await ctx.params).path);
}

export async function POST(request: NextRequest, ctx: Ctx) {
  return relay(request, (await ctx.params).path);
}

export async function DELETE(request: NextRequest, ctx: Ctx) {
  return relay(request, (await ctx.params).path);
}
