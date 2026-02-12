import { NextResponse } from "next/server";
import { getDeferredOpportunities } from "@/lib/queries";

export const dynamic = "force-dynamic";

export function GET() {
  try {
    return NextResponse.json(getDeferredOpportunities());
  } catch {
    return NextResponse.json({ error: "Database unavailable" }, { status: 503 });
  }
}
