import { NextRequest, NextResponse } from "next/server";
import { getOpenPositions, getClosedPositions } from "@/lib/queries";

export const dynamic = "force-dynamic";

export function GET(request: NextRequest) {
  const { searchParams } = request.nextUrl;
  const status = searchParams.get("status") ?? "open";
  const limit = parseInt(searchParams.get("limit") ?? "50", 10);
  const offset = parseInt(searchParams.get("offset") ?? "0", 10);

  try {
    if (status === "closed") {
      return NextResponse.json(getClosedPositions(limit, offset));
    }
    return NextResponse.json(getOpenPositions());
  } catch {
    return NextResponse.json({ error: "Database unavailable" }, { status: 503 });
  }
}
