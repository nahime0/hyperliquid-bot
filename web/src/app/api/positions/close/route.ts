import { NextRequest, NextResponse } from "next/server";
import { setAskClose } from "@/lib/queries";

export const dynamic = "force-dynamic";

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

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

    // Retry up to 3 times if DB is locked (bot holds write lock during ticks)
    for (let attempt = 1; attempt <= 3; attempt++) {
      try {
        const updated = setAskClose(positionId);

        if (!updated) {
          return NextResponse.json(
            { error: "Position not found or already closed" },
            { status: 404 },
          );
        }

        return NextResponse.json({ ok: true, position_id: positionId });
      } catch (e) {
        const msg = String(e);
        if (msg.includes("database is locked") && attempt < 3) {
          console.warn(`[POST /api/positions/close] DB locked, retry ${attempt}/3...`);
          await sleep(1000 * attempt);
          continue;
        }
        throw e;
      }
    }

    return NextResponse.json(
      { error: "database is locked (retries exhausted)" },
      { status: 503 },
    );
  } catch {
    return NextResponse.json(
      { error: "Failed to request close" },
      { status: 500 },
    );
  }
}
