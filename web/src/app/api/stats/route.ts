import { NextResponse } from "next/server";
import { getAggregateStats } from "@/lib/queries";

export const dynamic = "force-dynamic";

export function GET() {
  try {
    return NextResponse.json(getAggregateStats());
  } catch {
    return NextResponse.json({ error: "Database unavailable" }, { status: 503 });
  }
}
