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
  ask_close: number;
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
  action: "BUY" | "SHORT" | "SELL" | "HOLD" | "CLOSE" | "SCALE_UP" | "ADJUST" | "FLIP";
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
  strategy_type: string;
  confidence: number;
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

// ── Bot Config ──────────────────────────────────────────
export interface BotConfig {
  // main_
  main_trading_active: number;
  main_log_level: string;
  main_db_path: string;
  // hl_
  hl_testnet: number;
  hl_default_leverage: number;
  hl_margin_mode: string;
  hl_max_funding_rate: number;
  // ai_
  ai_advisor: string;
  ai_decision_interval: number;
  ai_min_confidence: number;
  ai_fallback_on_error: string;
  ai_log_reasoning: number;
  ai_model: string;
  ai_timeout: number;
  // risk_
  risk_max_trade_pct: number;
  risk_stop_loss_pct: number;
  risk_take_profit_pct: number;
  risk_auto_take_profit: number;
  risk_max_daily_drawdown_pct: number;
  risk_max_total_drawdown_pct: number;
  risk_max_open_positions: number;
  risk_dynamic_positions: number;
  risk_usdc_per_position: number;
  risk_target_utilization: number;
  risk_max_size_boost: number;
  risk_min_balance_usdc: number;
  risk_min_holding_minutes: number;
  risk_max_leverage: number;
  risk_liquidation_buffer_pct: number;
  risk_sl_tp_grace_seconds: number;
  risk_trailing_half_pct: number;
  risk_trailing_breakeven_pct: number;
  risk_trailing_start_pct: number;
  risk_trailing_distance_pct: number;
  risk_trailing_tight_pct: number;
  risk_trailing_tight_distance_pct: number;
  risk_time_stop_hours: number;
  risk_time_stop_min_pnl_pct: number;
  risk_symbol_cooldown_sec: number;
  risk_global_cooldown_sec: number;
  risk_global_cooldown_losses: number;
  risk_use_atr_sl: number;
  risk_atr_sl_multiplier: number;
  risk_atr_sl_min_pct: number;
  risk_atr_sl_max_pct: number;
  risk_risk_per_trade_pct: number;
  risk_partial_tp_enabled: number;
  risk_partial_tp_pct: number;
  risk_partial_tp_trigger_pct: number;
  risk_min_rr_ratio: number;
  risk_use_atr_trailing: number;
  risk_atr_trailing_multiplier: number;
  risk_atr_trailing_tight_multiplier: number;
  risk_atr_trailing_min_pct: number;
  risk_atr_trailing_max_pct: number;
  // market_
  market_min_pair_volume: number;
  market_max_coins: number;
  market_max_spread_pct: number;
  market_coin_blacklist: string;
  // strat_
  strat_active_strategies: string;
  strat_min_candle_volume_usdc: number;
  strat_primary_interval: string;
  strat_mr_interval: string;
  strat_trend_interval: string;
  // rsidiv_
  rsidiv_period: number;
  rsidiv_swing_window: number;
  rsidiv_long_exit: number;
  rsidiv_short_exit: number;
  // tf_
  tf_allow_short: number;
  tf_change_threshold: number;
  // btd_
  btd_dip_min_pct: number;
  btd_dip_max_pct: number;
  btd_volume_spike: number;
  // ema_
  ema_cross_fast: number;
  ema_cross_slow: number;
  ema_cross_max_bars: number;
  // bbs_
  bbs_squeeze_percentile: number;
  bbs_lookback_bars: number;
  bbs_min_volume_spike: number;
  // macd_
  macd_div_swing_window: number;
  macd_div_recency: number;
  // vs_
  vs_spike_threshold: number;
  vs_wick_ratio: number;
  // bo_
  bo_lookback_hours: number;
  bo_min_breakout_pct: number;
  bo_confirm_bars: number;
  // fr_
  fr_extreme_negative: number;
  fr_extreme_positive: number;
  fr_normalize_threshold: number;
  // btc_
  btc_min_move_pct: number;
  btc_min_lag_pct: number;
  btc_catch_up_pct: number;
  // sm_
  sm_min_session_change: number;
  sm_entry_window_hours: number;
  sm_exit_hours: number;
  // mtf_
  mtf_rsi_oversold: number;
  mtf_rsi_overbought: number;
  mtf_rsi_slope_bars: number;
  // telegram_
  telegram_bot_token: string;
  telegram_chat_id: string;
  // meta
  updated_at: string;
}

export interface ConfigHistoryEntry {
  id: number;
  timestamp: string;
  field_name: string;
  old_value: string | null;
  new_value: string;
  source: string;
}
