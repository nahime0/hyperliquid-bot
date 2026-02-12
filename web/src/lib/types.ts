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

// bot_status.json shape
export interface BotStatus {
  updated_at: string;
  cycle_count: number;
  mode: "testnet" | "mainnet";
  paper: boolean;
  no_ai: boolean;
  leverage: number;
  margin_mode: string;
  risk_metrics: RiskMetrics;
  strategy_mode: string;
  strategies: Record<string, unknown>;
}

export interface RiskMetrics {
  current_balance: number;
  peak_balance: number;
  drawdown_pct: number;
  daily_drawdown_pct: number;
  open_positions: number;
  max_open_positions: number;
  kill_switch: boolean;
  kill_reason: string;
  daily_paused: boolean;
  daily_pause_reason: string;
  max_trade_pct: number;
  stop_loss_pct: number;
  take_profit_pct: number;
  min_balance_usdc: number;
  capital_utilization: number;
  total_margin_used: number;
  available_margin: number;
  target_utilization: number;
}

export interface TradeStats {
  total_trades: number;
  win_rate: number;
  avg_win: number;
  avg_loss: number;
  total_pnl: number;
  profit_factor: number;
}
