import { NextRequest, NextResponse } from "next/server";
import { getCoins } from "@/lib/queries";

export const dynamic = "force-dynamic";

export function GET(request: NextRequest) {
  const activeOnly = request.nextUrl.searchParams.get("all") !== "true";

  try {
    return NextResponse.json(getCoins(activeOnly));
  } catch {
    return NextResponse.json({ error: "Database unavailable" }, { status: 503 });
  }
}
