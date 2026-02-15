import { NextRequest, NextResponse } from "next/server";
import { getConfig, updateConfig } from "@/lib/queries";

export const dynamic = "force-dynamic";

export function GET() {
  try {
    const config = getConfig();
    return NextResponse.json(config);
  } catch {
    return NextResponse.json({ error: "Config unavailable" }, { status: 503 });
  }
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

export async function PUT(request: NextRequest) {
  const body = await request.json();
  if (!body || typeof body !== "object") {
    return NextResponse.json({ error: "Invalid body" }, { status: 400 });
  }

  // Retry up to 3 times if DB is locked (bot holds write lock during ticks)
  for (let attempt = 1; attempt <= 3; attempt++) {
    try {
      const { changed } = updateConfig(body);
      return NextResponse.json({ ok: true, changed });
    } catch (e) {
      const msg = String(e);
      if (msg.includes("database is locked") && attempt < 3) {
        console.warn(`[PUT /api/config] DB locked, retry ${attempt}/3...`);
        await sleep(1000 * attempt);
        continue;
      }
      console.error("[PUT /api/config] Error:", e);
      return NextResponse.json({ error: msg }, { status: 500 });
    }
  }

  return NextResponse.json({ error: "database is locked (retries exhausted)" }, { status: 503 });
}
