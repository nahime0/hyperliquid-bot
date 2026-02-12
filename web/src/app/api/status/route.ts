import { NextResponse } from "next/server";
import { getLatestCycleSummary, getLatestBalance, getOpenPositions, getBotState } from "@/lib/queries";

export const dynamic = "force-dynamic";

export function GET() {
  try {
    const cycle = getLatestCycleSummary();
    const balance = getLatestBalance();
    const openCount = getOpenPositions().length;

    // Enrich with live bot_status from bot_state table
    let botStatus: Record<string, unknown> | null = null;
    try {
      const raw = getBotState("bot_status");
      if (raw) {
        botStatus = JSON.parse(raw);
      }
    } catch {
      // ignore parse errors
    }

    if (!cycle && !botStatus) {
      return NextResponse.json({ error: "No cycle data yet" }, { status: 503 });
    }

    return NextResponse.json({
      cycle: cycle?.cycle ?? (botStatus?.cycle_count as number | undefined) ?? 0,
      timestamp: cycle?.timestamp ?? (botStatus?.updated_at as string | undefined) ?? "",
      duration_sec: cycle?.duration_sec ?? null,
      balance_usdc: cycle?.balance_usdc ?? balance?.total_usdc ?? 0,
      peak_balance: balance?.peak_balance ?? 0,
      drawdown_pct: cycle?.drawdown_pct ?? 0,
      daily_drawdown_pct: cycle?.daily_drawdown_pct ?? 0,
      open_positions: openCount,
      capital_utilization: cycle?.capital_utilization ?? 0,
      kill_switch: !!cycle?.kill_switch,
      daily_paused: !!cycle?.daily_paused,
      coins_monitored: cycle?.coins_monitored ?? 0,
      signals_generated: cycle?.signals_generated ?? 0,
      decisions_approved: cycle?.decisions_approved ?? 0,
      decisions_blocked: cycle?.decisions_blocked ?? 0,
      trades_executed: cycle?.trades_executed ?? 0,
      // Extra fields from bot_status (live state)
      mode: (botStatus?.mode as string | undefined) ?? null,
      paper: (botStatus?.paper as boolean | undefined) ?? null,
      no_ai: (botStatus?.no_ai as boolean | undefined) ?? null,
      strategy_mode: (botStatus?.strategy_mode as string | undefined) ?? null,
      leverage: (botStatus?.leverage as number | undefined) ?? null,
    });
  } catch {
    return NextResponse.json({ error: "Database unavailable" }, { status: 503 });
  }
}
