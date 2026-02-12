import { NextRequest, NextResponse } from "next/server";
import { getCoin, getTradesBySymbol, getPositionsBySymbol } from "@/lib/queries";
import { getEvents } from "@/lib/queries";

export const dynamic = "force-dynamic";

export function GET(
  _request: NextRequest,
  { params }: { params: Promise<{ symbol: string }> }
) {
  return params.then(({ symbol }) => {
    try {
      const coin = getCoin(symbol);
      if (!coin) {
        return NextResponse.json({ error: "Coin not found" }, { status: 404 });
      }
      const trades = getTradesBySymbol(symbol, 50);
      const positions = getPositionsBySymbol(symbol);
      const events = getEvents({ symbol, limit: 100 });
      return NextResponse.json({ coin, trades, positions, events });
    } catch {
      return NextResponse.json({ error: "Database unavailable" }, { status: 503 });
    }
  });
}
