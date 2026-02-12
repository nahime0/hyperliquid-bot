import { NextRequest, NextResponse } from "next/server";
import { getRecentTrades, getTradesBySymbol } from "@/lib/queries";

export const dynamic = "force-dynamic";

export function GET(request: NextRequest) {
  const { searchParams } = request.nextUrl;
  const symbol = searchParams.get("symbol");
  const limit = parseInt(searchParams.get("limit") ?? "50", 10);
  const offset = parseInt(searchParams.get("offset") ?? "0", 10);

  try {
    if (symbol) {
      return NextResponse.json(getTradesBySymbol(symbol, limit));
    }
    return NextResponse.json(getRecentTrades(limit, offset));
  } catch {
    return NextResponse.json({ error: "Database unavailable" }, { status: 503 });
  }
}
