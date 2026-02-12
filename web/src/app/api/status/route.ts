import { NextResponse } from "next/server";
import fs from "fs";
import path from "path";

const STATUS_PATH = path.resolve(process.cwd(), "..", "data", "bot_status.json");

export const dynamic = "force-dynamic";

export function GET() {
  try {
    const raw = fs.readFileSync(STATUS_PATH, "utf-8");
    const data = JSON.parse(raw);
    return NextResponse.json(data);
  } catch {
    return NextResponse.json({ error: "Bot status unavailable" }, { status: 503 });
  }
}
