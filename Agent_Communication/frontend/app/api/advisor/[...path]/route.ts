import { cookies } from "next/headers";
import { NextRequest, NextResponse } from "next/server";

import {
  advisorApiBaseUrl,
  getAuthenticatedUser,
  SESSION_COOKIE,
} from "@/lib/auth";

type RouteContext = { params: Promise<{ path: string[] }> };

const ALLOWED_PATH =
  /^(?:health|api\/(?:chat|transcribe|tts|admin\/approvals(?:\/[0-9a-f-]+(?:\/decision)?)?))$/i;

async function proxyAdvisorRequest(
  request: NextRequest,
  context: RouteContext,
) {
  const { path } = await context.params;
  const upstreamPath = path.join("/");
  if (!ALLOWED_PATH.test(upstreamPath)) {
    return NextResponse.json({ detail: "Advisor route not found." }, { status: 404 });
  }

  const token = (await cookies()).get(SESSION_COOKIE)?.value;
  if (!token) {
    return NextResponse.json({ detail: "Not authenticated." }, { status: 401 });
  }

  let user;
  try {
    user = await getAuthenticatedUser(token);
  } catch {
    return NextResponse.json(
      { detail: "The authentication service is unavailable." },
      { status: 503 },
    );
  }
  if (!user) {
    const response = NextResponse.json({ detail: "Session expired." }, { status: 401 });
    response.cookies.delete(SESSION_COOKIE);
    return response;
  }

  if (upstreamPath.startsWith("api/admin/approvals") && user.role !== "approver") {
    return NextResponse.json({ detail: "Approver role required." }, { status: 403 });
  }

  const headers = new Headers();
  const contentType = request.headers.get("content-type");
  if (contentType) headers.set("Content-Type", contentType);
  headers.set("Authorization", `Bearer ${token}`);

  let upstream: Response;
  try {
    upstream = await fetch(
      `${advisorApiBaseUrl()}/${upstreamPath}${request.nextUrl.search}`,
      {
        method: request.method,
        headers,
        body: request.method === "GET" ? undefined : await request.arrayBuffer(),
        cache: "no-store",
        signal: AbortSignal.timeout(120000),
      },
    );
  } catch {
    return NextResponse.json(
      { detail: "The Customer Advisor service is unavailable." },
      { status: 503 },
    );
  }

  const responseHeaders = new Headers();
  const upstreamContentType = upstream.headers.get("content-type");
  if (upstreamContentType) responseHeaders.set("Content-Type", upstreamContentType);
  responseHeaders.set("Cache-Control", "no-store");
  return new NextResponse(await upstream.arrayBuffer(), {
    status: upstream.status,
    headers: responseHeaders,
  });
}

export const GET = proxyAdvisorRequest;
export const POST = proxyAdvisorRequest;
