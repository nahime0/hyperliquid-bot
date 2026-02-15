/** Map of bot_config DB column → human-readable label + section. */

export interface FieldInfo {
  label: string;
  section: string;
}

export const FIELD_LABELS: Record<string, FieldInfo> = {
  // General
  main_trading_active: { label: "Trading Active", section: "General" },
  main_log_level: { label: "Log Level", section: "General" },
  main_db_path: { label: "Database Path", section: "General" },
  // Hyperliquid
  hl_testnet: { label: "Testnet Mode", section: "Hyperliquid" },
  hl_default_leverage: { label: "Default Leverage", section: "Hyperliquid" },
  hl_margin_mode: { label: "Margin Mode", section: "Hyperliquid" },
  hl_max_funding_rate: { label: "Max Funding Rate", section: "Hyperliquid" },
  // AI Advisor
  ai_advisor: { label: "Advisor Backend", section: "AI Advisor" },
  ai_model: { label: "Model", section: "AI Advisor" },
  ai_decision_interval: { label: "Decision Interval", section: "AI Advisor" },
  ai_min_confidence: { label: "Min Confidence", section: "AI Advisor" },
  ai_timeout: { label: "Timeout", section: "AI Advisor" },
  ai_fallback_on_error: { label: "Fallback on Error", section: "AI Advisor" },
  ai_log_reasoning: { label: "Log Reasoning", section: "AI Advisor" },
  // Risk: Positions & Sizing
  risk_max_open_positions: { label: "Max Open Positions", section: "Positions & Sizing" },
  risk_dynamic_positions: { label: "Dynamic Positions", section: "Positions & Sizing" },
  risk_usdc_per_position: { label: "USDC per Position Slot", section: "Positions & Sizing" },
  risk_max_trade_pct: { label: "Max Trade Size", section: "Positions & Sizing" },
  risk_risk_per_trade_pct: { label: "Risk per Trade", section: "Positions & Sizing" },
  risk_max_leverage: { label: "Max Leverage", section: "Positions & Sizing" },
  risk_target_utilization: { label: "Target Utilization", section: "Positions & Sizing" },
  risk_max_size_boost: { label: "Max Size Boost", section: "Positions & Sizing" },
  risk_min_balance_usdc: { label: "Min Balance", section: "Positions & Sizing" },
  risk_min_holding_minutes: { label: "Min Holding Time", section: "Positions & Sizing" },
  risk_liquidation_buffer_pct: { label: "Liquidation Buffer", section: "Positions & Sizing" },
  risk_min_rr_ratio: { label: "Min R:R Ratio", section: "Positions & Sizing" },
  // Risk: Stop Loss & Take Profit
  risk_stop_loss_pct: { label: "Default Stop Loss", section: "SL & TP" },
  risk_take_profit_pct: { label: "Default Take Profit", section: "SL & TP" },
  risk_auto_take_profit: { label: "Auto Take Profit", section: "SL & TP" },
  risk_sl_tp_grace_seconds: { label: "SL/TP Grace Period", section: "SL & TP" },
  risk_use_atr_sl: { label: "ATR-based SL", section: "SL & TP" },
  risk_atr_sl_multiplier: { label: "ATR SL Multiplier", section: "SL & TP" },
  risk_atr_sl_min_pct: { label: "ATR SL Floor", section: "SL & TP" },
  risk_atr_sl_max_pct: { label: "ATR SL Ceiling", section: "SL & TP" },
  risk_partial_tp_enabled: { label: "Partial TP", section: "SL & TP" },
  risk_partial_tp_pct: { label: "Partial TP Size", section: "SL & TP" },
  risk_partial_tp_trigger_pct: { label: "Partial TP Trigger", section: "SL & TP" },
  // Risk: Trailing Stop
  risk_trailing_half_pct: { label: "Half-way SL Move", section: "Trailing Stop" },
  risk_trailing_breakeven_pct: { label: "Breakeven Move", section: "Trailing Stop" },
  risk_trailing_start_pct: { label: "Trailing Start", section: "Trailing Stop" },
  risk_trailing_distance_pct: { label: "Trail Distance", section: "Trailing Stop" },
  risk_trailing_tight_pct: { label: "Tight Trail Trigger", section: "Trailing Stop" },
  risk_trailing_tight_distance_pct: { label: "Tight Trail Distance", section: "Trailing Stop" },
  risk_use_atr_trailing: { label: "ATR-based Trailing", section: "Trailing Stop" },
  risk_atr_trailing_multiplier: { label: "ATR Trail Multiplier", section: "Trailing Stop" },
  risk_atr_trailing_tight_multiplier: { label: "ATR Tight Multiplier", section: "Trailing Stop" },
  risk_atr_trailing_min_pct: { label: "ATR Trail Floor", section: "Trailing Stop" },
  risk_atr_trailing_max_pct: { label: "ATR Trail Ceiling", section: "Trailing Stop" },
  // Risk: Drawdown & Cooldown
  risk_max_daily_drawdown_pct: { label: "Max Daily Drawdown", section: "Drawdown & Cooldown" },
  risk_max_total_drawdown_pct: { label: "Max Total Drawdown", section: "Drawdown & Cooldown" },
  risk_time_stop_hours: { label: "Time Stop", section: "Drawdown & Cooldown" },
  risk_time_stop_min_pnl_pct: { label: "Time Stop Min PnL", section: "Drawdown & Cooldown" },
  risk_symbol_cooldown_sec: { label: "Symbol Cooldown", section: "Drawdown & Cooldown" },
  risk_global_cooldown_sec: { label: "Global Cooldown", section: "Drawdown & Cooldown" },
  risk_global_cooldown_losses: { label: "Cooldown Trigger Losses", section: "Drawdown & Cooldown" },
  // Market Discovery
  market_min_pair_volume: { label: "Min 24h Volume", section: "Market" },
  market_max_coins: { label: "Max Coins", section: "Market" },
  market_max_spread_pct: { label: "Max Spread", section: "Market" },
  market_coin_blacklist: { label: "Coin Blacklist", section: "Market" },
  // Strategy: General
  strat_active_strategies: { label: "Active Strategies", section: "Strategy" },
  strat_min_candle_volume_usdc: { label: "Min Candle Volume", section: "Strategy" },
  strat_primary_interval: { label: "Primary Interval", section: "Strategy" },
  strat_mr_interval: { label: "MR Interval", section: "Strategy" },
  strat_trend_interval: { label: "Trend Interval", section: "Strategy" },
  // RSI Divergence
  rsidiv_period: { label: "RSI Period", section: "RSI Divergence" },
  rsidiv_swing_window: { label: "Swing Window", section: "RSI Divergence" },
  rsidiv_long_exit: { label: "Long Exit RSI", section: "RSI Divergence" },
  rsidiv_short_exit: { label: "Short Exit RSI", section: "RSI Divergence" },
  // Trend Following
  tf_allow_short: { label: "Allow Short", section: "Trend Following" },
  tf_change_threshold: { label: "4h Change Threshold", section: "Trend Following" },
  // Buy the Dip
  btd_dip_min_pct: { label: "Min Dip %", section: "Buy the Dip" },
  btd_dip_max_pct: { label: "Max Dip %", section: "Buy the Dip" },
  btd_volume_spike: { label: "Volume Spike", section: "Buy the Dip" },
  // EMA Crossover
  ema_cross_fast: { label: "Fast EMA", section: "EMA Crossover" },
  ema_cross_slow: { label: "Slow EMA", section: "EMA Crossover" },
  ema_cross_max_bars: { label: "Max Bars Since Cross", section: "EMA Crossover" },
  // BB Squeeze
  bbs_squeeze_percentile: { label: "Squeeze Percentile", section: "BB Squeeze" },
  bbs_lookback_bars: { label: "Lookback Bars", section: "BB Squeeze" },
  bbs_min_volume_spike: { label: "Min Volume Spike", section: "BB Squeeze" },
  // MACD Divergence
  macd_div_swing_window: { label: "Swing Window", section: "MACD Divergence" },
  macd_div_recency: { label: "Max Recency", section: "MACD Divergence" },
  // Volume Spike
  vs_spike_threshold: { label: "Spike Threshold", section: "Volume Spike" },
  vs_wick_ratio: { label: "Wick/Body Ratio", section: "Volume Spike" },
  // S/R Breakout
  bo_lookback_hours: { label: "S/R Lookback", section: "S/R Breakout" },
  bo_min_breakout_pct: { label: "Min Breakout %", section: "S/R Breakout" },
  bo_confirm_bars: { label: "Confirm Bars", section: "S/R Breakout" },
  // Funding Rate
  fr_extreme_negative: { label: "Extreme Negative", section: "Funding Rate" },
  fr_extreme_positive: { label: "Extreme Positive", section: "Funding Rate" },
  fr_normalize_threshold: { label: "Normalize Threshold", section: "Funding Rate" },
  // BTC Correlation
  btc_min_move_pct: { label: "Min BTC Move", section: "BTC Correlation" },
  btc_min_lag_pct: { label: "Min Lag", section: "BTC Correlation" },
  btc_catch_up_pct: { label: "Catch-up Threshold", section: "BTC Correlation" },
  // Session Momentum
  sm_min_session_change: { label: "Min Session Change", section: "Session Momentum" },
  sm_entry_window_hours: { label: "Entry Window", section: "Session Momentum" },
  sm_exit_hours: { label: "Max Hold Time", section: "Session Momentum" },
  // Multi-Timeframe
  mtf_rsi_oversold: { label: "RSI Oversold", section: "Multi-Timeframe" },
  mtf_rsi_overbought: { label: "RSI Overbought", section: "Multi-Timeframe" },
  mtf_rsi_slope_bars: { label: "RSI Slope Bars", section: "Multi-Timeframe" },
  // Telegram
  telegram_bot_token: { label: "Bot Token", section: "Telegram" },
  telegram_chat_id: { label: "Chat ID", section: "Telegram" },
};

export const PREFIX_COLORS: Record<string, string> = {
  main: "text-text-secondary bg-text-secondary/10",
  hl: "text-purple bg-purple/10",
  ai: "text-accent bg-accent/10",
  risk: "text-loss bg-loss/10",
  market: "text-profit bg-profit/10",
  strat: "text-warning bg-warning/10",
  rsidiv: "text-purple bg-purple/10",
  tf: "text-accent bg-accent/10",
  btd: "text-profit bg-profit/10",
  ema: "text-warning bg-warning/10",
  bbs: "text-accent bg-accent/10",
  macd: "text-purple bg-purple/10",
  vs: "text-loss bg-loss/10",
  bo: "text-profit bg-profit/10",
  fr: "text-warning bg-warning/10",
  btc: "text-[#f7931a] bg-[#f7931a]/10",
  sm: "text-accent bg-accent/10",
  mtf: "text-purple bg-purple/10",
  telegram: "text-[#0088cc] bg-[#0088cc]/10",
};

/** Get all unique section names for filtering */
export function getAllSections(): string[] {
  const sections = new Set(Object.values(FIELD_LABELS).map((f) => f.section));
  return [...sections].sort();
}
