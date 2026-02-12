import { NextRequest, NextResponse } from "next/server";
import { getEquityCurve } from "@/lib/queries";

export const dynamic = "force-dynamic";

export function GET(request: NextRequest) {
  const limit = parseInt(request.nextUrl.searchParams.get("limit") ?? "500", 10);

  try {
    const snapshots = getEquityCurve(limit);
    // Return in chronological order for charts
    return NextResponse.json(snapshots.reverse());
  } catch {
    return NextResponse.json({ error: "Database unavailable" }, { status: 503 });
  }
}
