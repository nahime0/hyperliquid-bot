import { NextRequest, NextResponse } from "next/server";
import { getAiDecisions, getAiDecisionCount, getAiStats } from "@/lib/queries";

export const dynamic = "force-dynamic";

export function GET(request: NextRequest) {
  const { searchParams } = request.nextUrl;
  const limit = parseInt(searchParams.get("limit") ?? "50", 10);
  const offset = parseInt(searchParams.get("offset") ?? "0", 10);
  const statsOnly = searchParams.get("stats") === "true";

  try {
    if (statsOnly) {
      return NextResponse.json(getAiStats());
    }
    const decisions = getAiDecisions(limit, offset);
    const total = getAiDecisionCount();
    return NextResponse.json({ decisions, total, limit, offset });
  } catch {
    return NextResponse.json({ error: "Database unavailable" }, { status: 503 });
  }
}
