import { NextRequest, NextResponse } from "next/server";
import { getConfigHistory, getConfigHistoryCount } from "@/lib/queries";

export const dynamic = "force-dynamic";

export function GET(request: NextRequest) {
  try {
    const { searchParams } = new URL(request.url);
    const limit = parseInt(searchParams.get("limit") ?? "100", 10);
    const offset = parseInt(searchParams.get("offset") ?? "0", 10);
    const field_name = searchParams.get("field") ?? undefined;

    const history = getConfigHistory({ limit, offset, field_name });
    const total = getConfigHistoryCount(field_name);

    return NextResponse.json({ history, total });
  } catch {
    return NextResponse.json({ error: "History unavailable" }, { status: 503 });
  }
}
