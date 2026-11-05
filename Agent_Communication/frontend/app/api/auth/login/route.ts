import { NextRequest, NextResponse } from "next/server";

import { advisorApiBaseUrl, getAuthenticatedUser, SESSION_COOKIE } from "@/lib/auth";

type LoginPayload = {
  username?: unknown;
  password?: unknown;
};

export async function POST(request: NextRequest) {
  let payload: LoginPayload;
  try {
    payload = (await request.json()) as LoginPayload;
  } catch {
    return NextResponse.json({ detail: "Invalid login request." }, { status: 400 });
  }

  if (
    typeof payload.username !== "string" ||
    typeof payload.password !== "string" ||
    !payload.username.trim() ||
    !payload.password
  ) {
    return NextResponse.json(
      { detail: "Username and password are required." },
      { status: 422 },
    );
  }

  let loginResponse: Response;
  try {
    loginResponse = await fetch(`${advisorApiBaseUrl()}/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        username: payload.username.trim(),
        password: payload.password,
      }),
      cache: "no-store",
      signal: AbortSignal.timeout(10000),
    });
  } catch {
    return NextResponse.json(
      { detail: "The Customer Advisor authentication service is unavailable." },
      { status: 503 },
    );
  }

  const tokenPayload = (await loginResponse.json().catch(() => null)) as
    | { access_token?: unknown; expires_at?: unknown; detail?: unknown }
    | null;
  if (!loginResponse.ok) {
    const detail =
      typeof tokenPayload?.detail === "string"
        ? tokenPayload.detail
        : "Login failed. Check your username and password.";
    return NextResponse.json({ detail }, { status: loginResponse.status });
  }

  if (
    typeof tokenPayload?.access_token !== "string" ||
    typeof tokenPayload.expires_at !== "string"
  ) {
    return NextResponse.json(
      { detail: "The authentication service returned an invalid response." },
      { status: 502 },
    );
  }

  let user;
  try {
    user = await getAuthenticatedUser(tokenPayload.access_token);
  } catch {
    return NextResponse.json(
      { detail: "Could not verify the new login session." },
      { status: 503 },
    );
  }
  if (!user) {
    return NextResponse.json(
      { detail: "The authentication service returned an invalid session." },
      { status: 502 },
    );
  }

  const expiresAt = Date.parse(tokenPayload.expires_at);
  if (!Number.isFinite(expiresAt) || expiresAt <= Date.now()) {
    return NextResponse.json(
      { detail: "The authentication service returned an expired session." },
      { status: 502 },
    );
  }

  const response = NextResponse.json({ user });
  response.cookies.set(SESSION_COOKIE, tokenPayload.access_token, {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    sameSite: "lax",
    path: "/",
    expires: new Date(expiresAt),
  });
  return response;
}
