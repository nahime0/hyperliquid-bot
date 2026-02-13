import { NextRequest, NextResponse } from "next/server";
import { setAskClose } from "@/lib/queries";

export const dynamic = "force-dynamic";

export async function POST(request: NextRequest) {
  try {
    const body = await request.json();
    const positionId = body?.position_id;

    if (typeof positionId !== "number" || !Number.isInteger(positionId)) {
      return NextResponse.json(
        { error: "position_id (integer) is required" },
        { status: 400 },
      );
    }

    const updated = setAskClose(positionId);

    if (!updated) {
      return NextResponse.json(
        { error: "Position not found or already closed" },
        { status: 404 },
      );
    }

    return NextResponse.json({ ok: true, position_id: positionId });
  } catch {
    return NextResponse.json(
      { error: "Failed to request close" },
      { status: 500 },
    );
  }
}
