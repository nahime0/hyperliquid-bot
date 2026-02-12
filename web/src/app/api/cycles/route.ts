import { NextRequest, NextResponse } from "next/server";
import { getCycleSummaries } from "@/lib/queries";

export const dynamic = "force-dynamic";

export function GET(request: NextRequest) {
  const limit = parseInt(request.nextUrl.searchParams.get("limit") ?? "50", 10);

  try {
    return NextResponse.json(getCycleSummaries(limit));
  } catch {
    return NextResponse.json({ error: "Database unavailable" }, { status: 503 });
  }
}
