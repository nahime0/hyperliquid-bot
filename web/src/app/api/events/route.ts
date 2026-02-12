import { NextRequest, NextResponse } from "next/server";
import { getEvents, getEventCount } from "@/lib/queries";

export const dynamic = "force-dynamic";

export function GET(request: NextRequest) {
  const { searchParams } = request.nextUrl;
  const limit = parseInt(searchParams.get("limit") ?? "50", 10);
  const offset = parseInt(searchParams.get("offset") ?? "0", 10);
  const event_type = searchParams.get("event_type") ?? undefined;
  const symbol = searchParams.get("symbol") ?? undefined;
  const cycle = searchParams.get("cycle") ? parseInt(searchParams.get("cycle")!, 10) : undefined;

  try {
    const events = getEvents({ limit, offset, event_type, symbol, cycle });
    const total = getEventCount({ event_type, symbol, cycle });
    return NextResponse.json({ events, total, limit, offset });
  } catch {
    return NextResponse.json({ error: "Database unavailable" }, { status: 503 });
  }
}
