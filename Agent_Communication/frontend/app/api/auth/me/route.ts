import { cookies } from "next/headers";
import { NextResponse } from "next/server";

import { getAuthenticatedUser, SESSION_COOKIE } from "@/lib/auth";

export async function GET() {
  const token = (await cookies()).get(SESSION_COOKIE)?.value;
  if (!token) {
    return NextResponse.json({ detail: "Not authenticated." }, { status: 401 });
  }

  try {
    const user = await getAuthenticatedUser(token);
    if (!user) {
      const response = NextResponse.json({ detail: "Session expired." }, { status: 401 });
      response.cookies.delete(SESSION_COOKIE);
      return response;
    }
    return NextResponse.json({ user });
  } catch {
    return NextResponse.json(
      { detail: "The authentication service is unavailable." },
      { status: 503 },
    );
  }
}
