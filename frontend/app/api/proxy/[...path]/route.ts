/**
 * Relays dashboard calls to the control plane with the session token attached.
 *
 * Only paths under /api/ are forwarded, so a crafted path cannot reach the
 * WebSocket route or anything else the control plane exposes.
 */

import { NextRequest, NextResponse } from "next/server";

import { ApiError, apiBase, sessionToken } from "@/lib/server-api";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const ALLOWED_METHODS = new Set(["GET", "POST", "DELETE"]);

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

  try {
    const upstream = await fetch(target, {
      method: request.method,
      headers: {
        Authorization: `Bearer ${token}`,
        ...(body ? { "Content-Type": "application/json" } : {}),
      },
      body: body || undefined,
      cache: "no-store",
    });

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

    return new NextResponse(payload, { status: upstream.status, headers });
  } catch (error) {
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
