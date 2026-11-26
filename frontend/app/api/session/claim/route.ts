/**
 * A device waiting for approval asks whether it has it yet. Once an operator has
 * approved it, this sets its session (once), so it does not have to type the PIN
 * again. Before a session exists, like the rest of /api/session.
 *
 *   200 → approved; the session cookie is set
 *   202 → still waiting
 *   403 → refused, or the request ran out: enter the PIN again
 */

import { NextRequest, NextResponse } from "next/server";

import { sameOrigin } from "@/lib/request-guard";
import { ConfigError, clientIpHeaders, controlPlane, deviceHeaders, setSessionCookie } from "@/lib/server-api";
import { deviceCookieName, validDeviceSecret } from "@/lib/session-cookie";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(request: NextRequest) {
  if (!sameOrigin(request.headers)) {
    return NextResponse.json({ detail: "refused: this request did not come from the desk itself" }, { status: 403 });
  }
  const secret = request.cookies.get(deviceCookieName())?.value;
  if (!validDeviceSecret(secret)) {
    return NextResponse.json({ detail: "this browser lost its device identity — enter the PIN again" }, { status: 403 });
  }
  try {
    const res = await controlPlane("/api/auth/device/claim", {
      method: "POST",
      headers: { ...clientIpHeaders(request), ...deviceHeaders({ secret, fresh: false }, request) },
    });
    const data = (await res.json().catch(() => ({}))) as Record<string, unknown>;
    if (res.status === 200 && typeof data.token === "string") {
      const response = NextResponse.json({ ok: true }, { headers: { "Cache-Control": "no-store" } });
      await setSessionCookie(response, data.token, Number(data.expires_in) || 3600);
      return response;
    }
    if (res.status === 202) {
      return NextResponse.json({ pending: true, expires_in: data.expires_in }, { status: 202 });
    }
    return NextResponse.json(
      { detail: typeof data.detail === "string" ? data.detail : "not approved" },
      { status: res.status === 404 ? 409 : res.status },
    );
  } catch (error) {
    if (error instanceof ConfigError) return NextResponse.json({ detail: error.message }, { status: 503 });
    return NextResponse.json({ detail: "control plane unreachable" }, { status: 502 });
  }
}
