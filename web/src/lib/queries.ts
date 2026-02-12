import { getDb } from "./db";
import type {
  Trade,
  BalanceSnapshot,
  Position,
  Coin,
  Event,
  CycleSummary,
  TradeStats,
} from "./types";

// ── Positions ──────────────────────────────────────────────

export function getOpenPositions(): Position[] {
  return getDb()
    .prepare("SELECT * FROM positions WHERE status = 'OPEN' ORDER BY opened_at DESC")
    .all() as Position[];
}

export function getClosedPositions(limit = 50, offset = 0): Position[] {
  return getDb()
    .prepare("SELECT * FROM positions WHERE status = 'CLOSED' ORDER BY closed_at DESC LIMIT ? OFFSET ?")
    .all(limit, offset) as Position[];
}

export function getPositionsBySymbol(symbol: string): Position[] {
  return getDb()
    .prepare("SELECT * FROM positions WHERE symbol = ? ORDER BY id DESC")
    .all(symbol) as Position[];
}

// ── Trades ─────────────────────────────────────────────────

export function getRecentTrades(limit = 50, offset = 0): Trade[] {
  return getDb()
    .prepare("SELECT * FROM trades ORDER BY id DESC LIMIT ? OFFSET ?")
    .all(limit, offset) as Trade[];
}

export function getTradesBySymbol(symbol: string, limit = 50): Trade[] {
  return getDb()
    .prepare("SELECT * FROM trades WHERE symbol = ? ORDER BY id DESC LIMIT ?")
    .all(symbol, limit) as Trade[];
}

export function getTradeStats(): TradeStats {
  const rows = getDb()
    .prepare("SELECT pnl FROM trades WHERE pnl IS NOT NULL ORDER BY id DESC LIMIT 100")
    .all() as { pnl: number }[];

  const pnls = rows.map((r) => r.pnl);
  if (pnls.length === 0) {
    return { total_trades: 0, win_rate: 0, avg_win: 0, avg_loss: 0, total_pnl: 0, profit_factor: 0 };
  }

  const wins = pnls.filter((p) => p > 0);
  const losses = pnls.filter((p) => p <= 0);
  const totalWin = wins.reduce((a, b) => a + b, 0);
  const totalLoss = Math.abs(losses.reduce((a, b) => a + b, 0));

  return {
    total_trades: pnls.length,
    win_rate: pnls.length > 0 ? wins.length / pnls.length : 0,
    avg_win: wins.length > 0 ? totalWin / wins.length : 0,
    avg_loss: losses.length > 0 ? losses.reduce((a, b) => a + b, 0) / losses.length : 0,
    total_pnl: pnls.reduce((a, b) => a + b, 0),
    profit_factor: totalLoss > 0 ? totalWin / totalLoss : totalWin > 0 ? Infinity : 0,
  };
}

export function getTotalTradeCount(): number {
  const row = getDb()
    .prepare("SELECT COUNT(*) as cnt FROM trades")
    .get() as { cnt: number };
  return row.cnt;
}

// ── Balance / Equity ───────────────────────────────────────

export function getEquityCurve(limit = 500): BalanceSnapshot[] {
  return getDb()
    .prepare("SELECT * FROM balance_snapshots ORDER BY id DESC LIMIT ?")
    .all(limit) as BalanceSnapshot[];
}

export function getLatestBalance(): BalanceSnapshot | undefined {
  return getDb()
    .prepare("SELECT * FROM balance_snapshots ORDER BY id DESC LIMIT 1")
    .get() as BalanceSnapshot | undefined;
}

// ── Events ─────────────────────────────────────────────────

export function getEvents(params: {
  limit?: number;
  offset?: number;
  event_type?: string;
  symbol?: string;
  cycle?: number;
}): Event[] {
  const { limit = 50, offset = 0, event_type, symbol, cycle } = params;
  const conditions: string[] = [];
  const values: unknown[] = [];

  if (event_type) {
    conditions.push("event_type = ?");
    values.push(event_type);
  }
  if (symbol) {
    conditions.push("symbol = ?");
    values.push(symbol);
  }
  if (cycle !== undefined) {
    conditions.push("cycle = ?");
    values.push(cycle);
  }

  const where = conditions.length > 0 ? `WHERE ${conditions.join(" AND ")}` : "";
  values.push(limit, offset);

  return getDb()
    .prepare(`SELECT * FROM events ${where} ORDER BY id DESC LIMIT ? OFFSET ?`)
    .all(...values) as Event[];
}

export function getEventCount(params: { event_type?: string; symbol?: string; cycle?: number }): number {
  const { event_type, symbol, cycle } = params;
  const conditions: string[] = [];
  const values: unknown[] = [];

  if (event_type) {
    conditions.push("event_type = ?");
    values.push(event_type);
  }
  if (symbol) {
    conditions.push("symbol = ?");
    values.push(symbol);
  }
  if (cycle !== undefined) {
    conditions.push("cycle = ?");
    values.push(cycle);
  }

  const where = conditions.length > 0 ? `WHERE ${conditions.join(" AND ")}` : "";
  const row = getDb()
    .prepare(`SELECT COUNT(*) as cnt FROM events ${where}`)
    .get(...values) as { cnt: number };
  return row.cnt;
}

// ── Coins ──────────────────────────────────────────────────

export function getCoins(activeOnly = true): Coin[] {
  const where = activeOnly ? "WHERE is_active = 1" : "";
  return getDb()
    .prepare(`SELECT * FROM coins ${where} ORDER BY symbol`)
    .all() as Coin[];
}

export function getCoin(symbol: string): Coin | undefined {
  return getDb()
    .prepare("SELECT * FROM coins WHERE symbol = ?")
    .get(symbol) as Coin | undefined;
}

// ── Cycles ─────────────────────────────────────────────────

export function getCycleSummaries(limit = 50): CycleSummary[] {
  return getDb()
    .prepare("SELECT * FROM cycle_summaries ORDER BY cycle DESC LIMIT ?")
    .all(limit) as CycleSummary[];
}

// ── Aggregate stats ────────────────────────────────────────

export function getAggregateStats() {
  const totalTrades = getTotalTradeCount();
  const stats = getTradeStats();
  const latestBalance = getLatestBalance();
  const openPositions = getOpenPositions();

  const allPnl = getDb()
    .prepare("SELECT COALESCE(SUM(pnl), 0) as total FROM trades WHERE pnl IS NOT NULL")
    .get() as { total: number };

  return {
    ...stats,
    total_trades: totalTrades,
    total_pnl_all: allPnl.total,
    current_balance: latestBalance?.total_usdc ?? 0,
    peak_balance: latestBalance?.peak_balance ?? 0,
    open_position_count: openPositions.length,
  };
}
