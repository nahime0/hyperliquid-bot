// DB row types matching Python schema

export interface Trade {
  id: number;
  timestamp: string;
  symbol: string;
  side: "BUY" | "SELL" | "SHORT" | "CLOSE";
  price: number;
  quantity: number;
  fee: number;
  fee_asset: string;
  pnl: number | null;
  strategy: string | null;
  order_id: string | null;
  notes: string | null;
  direction: string | null;
}

export interface BalanceSnapshot {
  id: number;
  timestamp: string;
  total_usdc: number;
  positions: string;
  peak_balance: number;
}

export interface Position {
  id: number;
  symbol: string;
  entry_price: number;
  quantity: number;
  stop_loss: number | null;
  take_profit: number | null;
  strategy: string | null;
  status: "OPEN" | "CLOSED";
  opened_at: string;
  closed_at: string | null;
  exit_price: number | null;
  pnl: number | null;
  close_reason: string | null;
  direction: string;
  leverage: number;
  liquidation_price: number | null;
  funding_paid: number;
  min_price_seen: number | null;
  max_price_seen: number | null;
  trailing_sl: number | null;
  original_sl: number | null;
}

export interface Coin {
  symbol: string;
  first_seen: string;
  last_seen: string;
  is_active: number;
  sz_decimals: number | null;
  avg_volume_24h: number;
}

export interface Event {
  id: number;
  timestamp: string;
  cycle: number;
  symbol: string;
  event_type: string;
  source: string;
  action: string | null;
  confidence: number | null;
  reasoning: string | null;
  details: string;
  position_id: number | null;
  trade_id: number | null;
}

export interface CycleSummary {
  id: number;
  timestamp: string;
  cycle: number;
  duration_sec: number | null;
  balance_usdc: number | null;
  drawdown_pct: number | null;
  daily_drawdown_pct: number | null;
  open_positions: number | null;
  capital_utilization: number | null;
  coins_monitored: number | null;
  signals_generated: number;
  decisions_approved: number;
  decisions_blocked: number;
  trades_executed: number;
  kill_switch: number;
  daily_paused: number;
}

export interface AiDecision {
  id: number;
  timestamp: string;
  snapshot_hash: string | null;
  action: "BUY" | "SHORT" | "SELL" | "HOLD" | "CLOSE" | "SCALE_UP" | "ADJUST";
  symbol: string;
  confidence: number;
  reasoning: string | null;
  raw_response: string | null;
  executed: number;
  execution_result: string | null;
  tier: string | null;
  cost_usd: number | null;
}

export interface Order {
  id: number;
  order_id: string;
  timestamp: string;
  symbol: string;
  side: "BUY" | "SELL" | "SHORT" | "CLOSE";
  order_type: string;
  price: number | null;
  quantity: number;
  status: "NEW" | "PARTIALLY_FILLED" | "FILLED" | "CANCELED";
  strategy: string | null;
  filled_price: number | null;
  filled_quantity: number | null;
  updated_at: string | null;
}

export interface DeferredOpportunity {
  id: number;
  symbol: string;
  original_action: string;
  deferred_at_cycle: number;
  conditions: string | null;
  type: "opportunity" | "hold";
  created_at: string;
}

// Dashboard status — sourced from DB (cycle_summaries + balance_snapshots + bot_state)
export interface DashboardStatus {
  // Latest cycle_summary
  cycle: number;
  timestamp: string;
  duration_sec: number | null;
  balance_usdc: number;
  peak_balance: number;
  drawdown_pct: number;
  daily_drawdown_pct: number;
  open_positions: number;
  capital_utilization: number;
  kill_switch: boolean;
  daily_paused: boolean;
  coins_monitored: number;
  signals_generated: number;
  decisions_approved: number;
  decisions_blocked: number;
  trades_executed: number;
  // Extra fields from bot_state
  mode: string | null;
  paper: boolean | null;
  no_ai: boolean | null;
  strategy_mode: string | null;
  leverage: number | null;
}

export interface TradeStats {
  total_trades: number;
  win_rate: number;
  avg_win: number;
  avg_loss: number;
  total_pnl: number;
  profit_factor: number;
}

export type AggregateStats = TradeStats & {
  total_pnl_all: number;
  current_balance: number;
  peak_balance: number;
  open_position_count: number;
};
