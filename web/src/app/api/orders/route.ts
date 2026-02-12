import { NextRequest, NextResponse } from "next/server";
import { getOrders, getOrderCount } from "@/lib/queries";

export const dynamic = "force-dynamic";

export function GET(request: NextRequest) {
  const { searchParams } = request.nextUrl;
  const limit = parseInt(searchParams.get("limit") ?? "50", 10);
  const offset = parseInt(searchParams.get("offset") ?? "0", 10);

  try {
    const orders = getOrders(limit, offset);
    const total = getOrderCount();
    return NextResponse.json({ orders, total, limit, offset });
  } catch {
    return NextResponse.json({ error: "Database unavailable" }, { status: 503 });
  }
}
