"use client";

import { useState, useCallback, useEffect, useRef } from "react";
import { useConfig, saveConfig } from "@/hooks/useConfig";
import { Card, Skeleton } from "@/components/shared/Card";
import type { BotConfig } from "@/lib/types";

/* ═══════════════════════════════════════════════════════════
   Field metadata: defines the UI for each config field
   ═══════════════════════════════════════════════════════════ */

type FieldType = "number" | "integer" | "float" | "boolean" | "text" | "select" | "textarea";

interface FieldMeta {
  key: keyof BotConfig;
  label: string;
  type: FieldType;
  description?: string;
  options?: string[];     // for select
  min?: number;
  max?: number;
  step?: number;
  unit?: string;          // display suffix like "%" or "s"
}

interface Section {
  id: string;
  title: string;
  icon: React.ReactNode;
  description: string;
  fields: FieldMeta[];
}

const SECTIONS: Section[] = [
  {
    id: "general",
    title: "General",
    icon: <IconCog />,
    description: "Core bot settings",
    fields: [
      { key: "main_log_level", label: "Log Level", type: "select", options: ["DEBUG", "INFO", "WARNING", "ERROR"], description: "Python logging level" },
      { key: "main_db_path", label: "Database Path", type: "text", description: "SQLite database file path" },
    ],
  },
  {
    id: "hyperliquid",
    title: "Hyperliquid",
    icon: <IconLink />,
    description: "Exchange connection settings",
    fields: [
      { key: "hl_testnet", label: "Testnet Mode", type: "boolean", description: "Use testnet API endpoint" },
      { key: "hl_default_leverage", label: "Default Leverage", type: "integer", min: 1, max: 50, description: "Default leverage for new positions", unit: "x" },
      { key: "hl_margin_mode", label: "Margin Mode", type: "select", options: ["cross", "isolated"], description: "Cross or isolated margin" },
      { key: "hl_max_funding_rate", label: "Max Funding Rate", type: "float", step: 0.0001, min: 0, description: "Block entry if |funding| exceeds this" },
    ],
  },
  {
    id: "ai",
    title: "AI Advisor",
    icon: <IconSparkles />,
    description: "Claude AI decision engine",
    fields: [
      { key: "ai_advisor", label: "Advisor Backend", type: "select", options: ["claude", "cursor"], description: "Which AI backend to use" },
      { key: "ai_model", label: "Model", type: "select", options: ["opus", "sonnet", "haiku"], description: "Claude model to use" },
      { key: "ai_decision_interval", label: "Decision Interval", type: "integer", min: 10, max: 600, unit: "s", description: "Seconds between AI decision cycles" },
      { key: "ai_min_confidence", label: "Min Confidence", type: "float", min: 0, max: 1, step: 0.05, description: "Minimum score to pass to AI" },
      { key: "ai_timeout", label: "Timeout", type: "integer", min: 30, max: 600, unit: "s", description: "Max seconds to wait for AI response" },
      { key: "ai_fallback_on_error", label: "Fallback on Error", type: "select", options: ["HOLD", "SKIP"], description: "What to do if AI call fails" },
      { key: "ai_log_reasoning", label: "Log Reasoning", type: "boolean", description: "Save AI reasoning to database" },
    ],
  },
  {
    id: "risk-positions",
    title: "Risk: Positions & Sizing",
    icon: <IconShield />,
    description: "Position limits and capital allocation",
    fields: [
      { key: "risk_max_open_positions", label: "Max Open Positions", type: "integer", min: 1, max: 50, description: "Hard cap on simultaneous positions" },
      { key: "risk_dynamic_positions", label: "Dynamic Positions", type: "boolean", description: "Scale position slots with balance" },
      { key: "risk_usdc_per_position", label: "USDC per Position Slot", type: "float", min: 5, max: 1000, unit: "$", description: "One slot per N USDC of balance" },
      { key: "risk_max_trade_pct", label: "Max Trade Size", type: "float", min: 1, max: 50, step: 0.5, unit: "%", description: "Max % of balance per single trade" },
      { key: "risk_risk_per_trade_pct", label: "Risk per Trade", type: "float", min: 0.1, max: 5, step: 0.1, unit: "%", description: "Max % of bankroll to risk per trade" },
      { key: "risk_max_leverage", label: "Max Leverage", type: "integer", min: 1, max: 20, unit: "x", description: "Maximum allowed leverage" },
      { key: "risk_target_utilization", label: "Target Utilization", type: "float", min: 0.1, max: 1, step: 0.05, description: "Target fraction of balance as margin (0-1)" },
      { key: "risk_max_size_boost", label: "Max Size Boost", type: "float", min: 1, max: 5, step: 0.1, unit: "x", description: "Max multiplier on base sizing when under-utilized" },
      { key: "risk_min_balance_usdc", label: "Min Balance", type: "float", min: 0, unit: "$", description: "Minimum balance to keep trading" },
      { key: "risk_min_holding_minutes", label: "Min Holding Time", type: "integer", min: 0, max: 120, unit: "min", description: "Minimum minutes before AI can close" },
      { key: "risk_liquidation_buffer_pct", label: "Liquidation Buffer", type: "float", min: 1, max: 20, step: 0.5, unit: "%", description: "Safety buffer from liquidation price" },
      { key: "risk_min_rr_ratio", label: "Min R:R Ratio", type: "float", min: 0.5, max: 5, step: 0.1, description: "Minimum reward/risk ratio for entries" },
    ],
  },
  {
    id: "risk-sl-tp",
    title: "Risk: Stop Loss & Take Profit",
    icon: <IconTarget />,
    description: "SL/TP and ATR-based stops",
    fields: [
      { key: "risk_stop_loss_pct", label: "Default Stop Loss", type: "float", min: 0.1, max: 10, step: 0.1, unit: "%", description: "Default SL percentage" },
      { key: "risk_take_profit_pct", label: "Default Take Profit", type: "float", min: 0.1, max: 20, step: 0.1, unit: "%", description: "Default TP percentage" },
      { key: "risk_auto_take_profit", label: "Auto Take Profit", type: "boolean", description: "Auto-close at TP (vs rely on trailing)" },
      { key: "risk_sl_tp_grace_seconds", label: "SL/TP Grace Period", type: "integer", min: 0, max: 120, unit: "s", description: "Skip SL/TP check for newly opened positions" },
      { key: "risk_use_atr_sl", label: "ATR-based SL", type: "boolean", description: "Use ATR to compute dynamic stop loss" },
      { key: "risk_atr_sl_multiplier", label: "ATR SL Multiplier", type: "float", min: 0.5, max: 5, step: 0.1, description: "SL = price +/- (ATR * multiplier)" },
      { key: "risk_atr_sl_min_pct", label: "ATR SL Floor", type: "float", min: 0.1, max: 5, step: 0.1, unit: "%", description: "Min SL percentage from ATR" },
      { key: "risk_atr_sl_max_pct", label: "ATR SL Ceiling", type: "float", min: 0.5, max: 10, step: 0.1, unit: "%", description: "Max SL percentage from ATR" },
      { key: "risk_partial_tp_enabled", label: "Partial TP", type: "boolean", description: "Auto-close partial at trigger" },
      { key: "risk_partial_tp_pct", label: "Partial TP Size", type: "float", min: 10, max: 90, step: 5, unit: "%", description: "% of position to close" },
      { key: "risk_partial_tp_trigger_pct", label: "Partial TP Trigger", type: "float", min: 0.5, max: 10, step: 0.1, unit: "%", description: "Trigger when gain exceeds this" },
    ],
  },
  {
    id: "risk-trailing",
    title: "Risk: Trailing Stop",
    icon: <IconTrend />,
    description: "Trailing stop configuration",
    fields: [
      { key: "risk_trailing_half_pct", label: "Half-way SL Move", type: "float", min: 0.1, max: 5, step: 0.1, unit: "%", description: "Move SL to midpoint after this gain" },
      { key: "risk_trailing_breakeven_pct", label: "Breakeven Move", type: "float", min: 0.1, max: 5, step: 0.1, unit: "%", description: "Move SL to entry after this gain" },
      { key: "risk_trailing_start_pct", label: "Trailing Start", type: "float", min: 0.1, max: 10, step: 0.1, unit: "%", description: "Start trailing after this gain" },
      { key: "risk_trailing_distance_pct", label: "Trail Distance", type: "float", min: 0.1, max: 5, step: 0.1, unit: "%", description: "Normal trailing distance from peak" },
      { key: "risk_trailing_tight_pct", label: "Tight Trail Trigger", type: "float", min: 0.5, max: 10, step: 0.1, unit: "%", description: "Tighten trail after this gain" },
      { key: "risk_trailing_tight_distance_pct", label: "Tight Trail Distance", type: "float", min: 0.1, max: 3, step: 0.05, unit: "%", description: "Tight trailing distance" },
      { key: "risk_use_atr_trailing", label: "ATR-based Trailing", type: "boolean", description: "Use ATR for trailing distance" },
      { key: "risk_atr_trailing_multiplier", label: "ATR Trail Multiplier", type: "float", min: 0.5, max: 5, step: 0.1, description: "Normal trail = ATR * multiplier" },
      { key: "risk_atr_trailing_tight_multiplier", label: "ATR Tight Multiplier", type: "float", min: 0.5, max: 3, step: 0.1, description: "Tight trail = ATR * multiplier" },
      { key: "risk_atr_trailing_min_pct", label: "ATR Trail Floor", type: "float", min: 0.1, max: 3, step: 0.1, unit: "%", description: "Min trailing distance from ATR" },
      { key: "risk_atr_trailing_max_pct", label: "ATR Trail Ceiling", type: "float", min: 0.5, max: 5, step: 0.1, unit: "%", description: "Max trailing distance from ATR" },
    ],
  },
  {
    id: "risk-drawdown",
    title: "Risk: Drawdown & Cooldown",
    icon: <IconAlertTriangle />,
    description: "Kill switch thresholds and cooldowns",
    fields: [
      { key: "risk_max_daily_drawdown_pct", label: "Max Daily Drawdown", type: "float", min: 1, max: 20, step: 0.5, unit: "%", description: "Pause trading if daily DD exceeds this" },
      { key: "risk_max_total_drawdown_pct", label: "Max Total Drawdown", type: "float", min: 5, max: 50, step: 1, unit: "%", description: "Kill switch if total DD exceeds this" },
      { key: "risk_time_stop_hours", label: "Time Stop", type: "float", min: 0.5, max: 48, step: 0.5, unit: "h", description: "Close positions older than this" },
      { key: "risk_time_stop_min_pnl_pct", label: "Time Stop Min PnL", type: "float", min: 0, max: 5, step: 0.1, unit: "%", description: "Only time-stop if PnL below this" },
      { key: "risk_symbol_cooldown_sec", label: "Symbol Cooldown", type: "integer", min: 0, max: 7200, unit: "s", description: "Per-symbol cooldown after loss" },
      { key: "risk_global_cooldown_sec", label: "Global Cooldown", type: "integer", min: 0, max: 3600, unit: "s", description: "Global cooldown after consecutive losses" },
      { key: "risk_global_cooldown_losses", label: "Cooldown Trigger Losses", type: "integer", min: 1, max: 10, description: "Consecutive losses to trigger global cooldown" },
    ],
  },
  {
    id: "market",
    title: "Market Discovery",
    icon: <IconGlobe />,
    description: "Coin filtering and discovery",
    fields: [
      { key: "market_min_pair_volume", label: "Min 24h Volume", type: "float", min: 0, step: 1000, unit: "$", description: "Minimum 24h volume in USDC" },
      { key: "market_max_coins", label: "Max Coins", type: "integer", min: 5, max: 200, description: "Maximum perpetual coins to monitor" },
      { key: "market_max_spread_pct", label: "Max Spread", type: "float", min: 0.01, max: 5, step: 0.01, unit: "%", description: "Block entry if spread exceeds this" },
      { key: "market_coin_blacklist", label: "Coin Blacklist", type: "textarea", description: "Comma-separated list of blocked coins" },
    ],
  },
  {
    id: "strat-general",
    title: "Strategy: General",
    icon: <IconStrategy />,
    description: "Active strategies and shared timeframes",
    fields: [
      { key: "strat_active_strategies", label: "Active Strategies", type: "textarea", description: "Comma-separated list of enabled strategies" },
      { key: "strat_min_candle_volume_usdc", label: "Min Candle Volume", type: "float", min: 0, step: 500, unit: "$", description: "Skip coins with candle volume below this" },
      { key: "strat_primary_interval", label: "Primary Interval", type: "select", options: ["1m", "3m", "5m", "15m", "30m", "1h"], description: "Main timeframe (RSI Div)" },
      { key: "strat_mr_interval", label: "MR Interval", type: "select", options: ["5m", "15m", "30m", "1h"], description: "Mean Reversion timeframe" },
      { key: "strat_trend_interval", label: "Trend Interval", type: "select", options: ["15m", "30m", "1h", "4h"], description: "Trend filter timeframe" },
    ],
  },
  {
    id: "strat-rsidiv",
    title: "Strategy: RSI Divergence",
    icon: <IconChart />,
    description: "RSI divergence detection parameters",
    fields: [
      { key: "rsidiv_period", label: "RSI Period", type: "integer", min: 5, max: 50, description: "RSI calculation period" },
      { key: "rsidiv_swing_window", label: "Swing Window", type: "integer", min: 2, max: 10, description: "Window to detect swing points" },
      { key: "rsidiv_long_exit", label: "Long Exit RSI", type: "float", min: 40, max: 80, step: 1, description: "Exit long when RSI exceeds this" },
      { key: "rsidiv_short_exit", label: "Short Exit RSI", type: "float", min: 10, max: 50, step: 1, description: "Exit short when RSI drops below this" },
    ],
  },
  {
    id: "strat-tf",
    title: "Strategy: Trend Following",
    icon: <IconTrend />,
    description: "Trend detection thresholds",
    fields: [
      { key: "tf_allow_short", label: "Allow Short", type: "boolean", description: "Enable SHORT signals on trend following" },
      { key: "tf_change_threshold", label: "4h Change Threshold", type: "float", min: 1, max: 15, step: 0.5, unit: "%", description: "Min % price change over 4h to fire signal" },
    ],
  },
  {
    id: "strat-btd",
    title: "Strategy: Buy the Dip",
    icon: <IconArrowDown />,
    description: "Dip detection parameters",
    fields: [
      { key: "btd_dip_min_pct", label: "Min Dip %", type: "float", min: 0.1, max: 5, step: 0.1, unit: "%", description: "Minimum dip in 15 minutes" },
      { key: "btd_dip_max_pct", label: "Max Dip %", type: "float", min: 1, max: 20, step: 0.5, unit: "%", description: "Above this = crash, not dip" },
      { key: "btd_volume_spike", label: "Volume Spike", type: "float", min: 1, max: 5, step: 0.1, unit: "x", description: "Volume spike multiplier for dip" },
    ],
  },
  {
    id: "strat-ema",
    title: "Strategy: EMA Crossover",
    icon: <IconCross />,
    description: "EMA crossover parameters",
    fields: [
      { key: "ema_cross_fast", label: "Fast EMA", type: "integer", min: 3, max: 50, description: "Fast EMA period" },
      { key: "ema_cross_slow", label: "Slow EMA", type: "integer", min: 10, max: 100, description: "Slow EMA period" },
      { key: "ema_cross_max_bars", label: "Max Bars Since Cross", type: "integer", min: 1, max: 10, description: "Max bars since crossover" },
    ],
  },
  {
    id: "strat-bbs",
    title: "Strategy: BB Squeeze",
    icon: <IconCompress />,
    description: "Bollinger Band squeeze parameters",
    fields: [
      { key: "bbs_squeeze_percentile", label: "Squeeze Percentile", type: "float", min: 5, max: 50, step: 1, unit: "%", description: "Bandwidth percentile for squeeze" },
      { key: "bbs_lookback_bars", label: "Lookback Bars", type: "integer", min: 20, max: 500, description: "Bars for bandwidth percentile" },
      { key: "bbs_min_volume_spike", label: "Min Volume Spike", type: "float", min: 1, max: 5, step: 0.1, unit: "x", description: "Volume spike on breakout" },
    ],
  },
  {
    id: "strat-macd",
    title: "Strategy: MACD Divergence",
    icon: <IconChart />,
    description: "MACD divergence detection",
    fields: [
      { key: "macd_div_swing_window", label: "Swing Window", type: "integer", min: 2, max: 10, description: "Window for swing detection" },
      { key: "macd_div_recency", label: "Max Recency", type: "integer", min: 10, max: 100, description: "Max bars to look back" },
    ],
  },
  {
    id: "strat-vs",
    title: "Strategy: Volume Spike",
    icon: <IconBars />,
    description: "Volume spike reversal parameters",
    fields: [
      { key: "vs_spike_threshold", label: "Spike Threshold", type: "float", min: 1.5, max: 10, step: 0.5, unit: "x", description: "Volume vs 20-bar average" },
      { key: "vs_wick_ratio", label: "Wick/Body Ratio", type: "float", min: 1, max: 5, step: 0.5, description: "Min wick/body ratio for reversal" },
    ],
  },
  {
    id: "strat-bo",
    title: "Strategy: S/R Breakout",
    icon: <IconBreakout />,
    description: "Support/resistance breakout",
    fields: [
      { key: "bo_lookback_hours", label: "S/R Lookback", type: "integer", min: 4, max: 168, unit: "h", description: "Hours of data for S/R levels" },
      { key: "bo_min_breakout_pct", label: "Min Breakout %", type: "float", min: 0.01, max: 2, step: 0.01, unit: "%", description: "Min % past level to confirm" },
      { key: "bo_confirm_bars", label: "Confirm Bars", type: "integer", min: 1, max: 5, description: "Bars that must be past level" },
    ],
  },
  {
    id: "strat-fr",
    title: "Strategy: Funding Rate",
    icon: <IconPercent />,
    description: "Funding rate contrarian",
    fields: [
      { key: "fr_extreme_negative", label: "Extreme Negative", type: "float", min: -0.01, max: 0, step: 0.0001, description: "Extreme negative funding threshold" },
      { key: "fr_extreme_positive", label: "Extreme Positive", type: "float", min: 0, max: 0.01, step: 0.0001, description: "Extreme positive funding threshold" },
      { key: "fr_normalize_threshold", label: "Normalize Threshold", type: "float", min: 0, max: 0.001, step: 0.00001, description: "Funding considered normal below this" },
    ],
  },
  {
    id: "strat-btc",
    title: "Strategy: BTC Correlation",
    icon: <IconBitcoin />,
    description: "BTC correlation lag trading",
    fields: [
      { key: "btc_min_move_pct", label: "Min BTC Move", type: "float", min: 0.5, max: 10, step: 0.1, unit: "%", description: "Min BTC move in 1h" },
      { key: "btc_min_lag_pct", label: "Min Lag", type: "float", min: 0.1, max: 5, step: 0.1, unit: "%", description: "Min lag between BTC and altcoin" },
      { key: "btc_catch_up_pct", label: "Catch-up Threshold", type: "float", min: 0.05, max: 2, step: 0.05, unit: "%", description: "Gap considered closed below this" },
    ],
  },
  {
    id: "strat-sm",
    title: "Strategy: Session Momentum",
    icon: <IconClock />,
    description: "Session-based momentum trading",
    fields: [
      { key: "sm_min_session_change", label: "Min Session Change", type: "float", min: 0.1, max: 5, step: 0.1, unit: "%", description: "Min % change from prior session" },
      { key: "sm_entry_window_hours", label: "Entry Window", type: "integer", min: 1, max: 8, unit: "h", description: "First N hours of new session" },
      { key: "sm_exit_hours", label: "Max Hold Time", type: "float", min: 1, max: 24, step: 0.5, unit: "h", description: "Max hours to hold session trade" },
    ],
  },
  {
    id: "strat-mtf",
    title: "Strategy: Multi-Timeframe",
    icon: <IconLayers />,
    description: "Multi-timeframe confluence",
    fields: [
      { key: "mtf_rsi_oversold", label: "RSI Oversold", type: "float", min: 10, max: 45, step: 1, description: "5m RSI oversold threshold" },
      { key: "mtf_rsi_overbought", label: "RSI Overbought", type: "float", min: 55, max: 90, step: 1, description: "5m RSI overbought threshold" },
      { key: "mtf_rsi_slope_bars", label: "RSI Slope Bars", type: "integer", min: 1, max: 10, description: "Bars to measure RSI slope on 15m" },
    ],
  },
  {
    id: "telegram",
    title: "Telegram",
    icon: <IconBell />,
    description: "Notification settings",
    fields: [
      { key: "telegram_bot_token", label: "Bot Token", type: "text", description: "Telegram bot token" },
      { key: "telegram_chat_id", label: "Chat ID", type: "text", description: "Telegram chat/group ID" },
    ],
  },
];

/* ═══════════════════════════════════════════════════════════
   Inline SVG icons (keep bundle small, no icon library)
   ═══════════════════════════════════════════════════════════ */

function IconCog() {
  return <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}><path strokeLinecap="round" strokeLinejoin="round" d="M9.594 3.94c.09-.542.56-.94 1.11-.94h2.593c.55 0 1.02.398 1.11.94l.213 1.281c.063.374.313.686.645.87.074.04.147.083.22.127.325.196.72.257 1.075.124l1.217-.456a1.125 1.125 0 011.37.49l1.296 2.247a1.125 1.125 0 01-.26 1.431l-1.003.827c-.293.241-.438.613-.43.992a7.723 7.723 0 010 .255c-.008.378.137.75.43.991l1.004.827c.424.35.534.955.26 1.43l-1.298 2.247a1.125 1.125 0 01-1.369.491l-1.217-.456c-.355-.133-.75-.072-1.076.124a6.47 6.47 0 01-.22.128c-.331.183-.581.495-.644.869l-.213 1.281c-.09.543-.56.941-1.11.941h-2.594c-.55 0-1.019-.398-1.11-.94l-.213-1.281c-.062-.374-.312-.686-.644-.87a6.52 6.52 0 01-.22-.127c-.325-.196-.72-.257-1.076-.124l-1.217.456a1.125 1.125 0 01-1.369-.49l-1.297-2.247a1.125 1.125 0 01.26-1.431l1.004-.827c.292-.24.437-.613.43-.991a6.932 6.932 0 010-.255c.007-.38-.138-.751-.43-.992l-1.004-.827a1.125 1.125 0 01-.26-1.43l1.297-2.247a1.125 1.125 0 011.37-.491l1.216.456c.356.133.751.072 1.076-.124.072-.044.146-.086.22-.128.332-.183.582-.495.644-.869l.214-1.28z" /><path strokeLinecap="round" strokeLinejoin="round" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" /></svg>;
}
function IconLink() {
  return <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}><path strokeLinecap="round" strokeLinejoin="round" d="M13.19 8.688a4.5 4.5 0 011.242 7.244l-4.5 4.5a4.5 4.5 0 01-6.364-6.364l1.757-1.757m13.35-.622l1.757-1.757a4.5 4.5 0 00-6.364-6.364l-4.5 4.5a4.5 4.5 0 001.242 7.244" /></svg>;
}
function IconSparkles() {
  return <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}><path strokeLinecap="round" strokeLinejoin="round" d="M9.813 15.904L9 18.75l-.813-2.846a4.5 4.5 0 00-3.09-3.09L2.25 12l2.846-.813a4.5 4.5 0 003.09-3.09L9 5.25l.813 2.846a4.5 4.5 0 003.09 3.09L15.75 12l-2.846.813a4.5 4.5 0 00-3.09 3.09zM18.259 8.715L18 9.75l-.259-1.035a3.375 3.375 0 00-2.455-2.456L14.25 6l1.036-.259a3.375 3.375 0 002.455-2.456L18 2.25l.259 1.035a3.375 3.375 0 002.455 2.456L21.75 6l-1.036.259a3.375 3.375 0 00-2.455 2.456z" /></svg>;
}
function IconShield() {
  return <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}><path strokeLinecap="round" strokeLinejoin="round" d="M9 12.75L11.25 15 15 9.75m-3-7.036A11.959 11.959 0 013.598 6 11.99 11.99 0 003 9.749c0 5.592 3.824 10.29 9 11.623 5.176-1.332 9-6.03 9-11.622 0-1.31-.21-2.571-.598-3.751h-.152c-3.196 0-6.1-1.248-8.25-3.285z" /></svg>;
}
function IconTarget() {
  return <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}><circle cx="12" cy="12" r="9" /><circle cx="12" cy="12" r="5" /><circle cx="12" cy="12" r="1" /></svg>;
}
function IconTrend() {
  return <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}><path strokeLinecap="round" strokeLinejoin="round" d="M2.25 18L9 11.25l4.306 4.307a11.95 11.95 0 015.814-5.519l2.74-1.22m0 0l-5.94-2.28m5.94 2.28l-2.28 5.941" /></svg>;
}
function IconAlertTriangle() {
  return <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}><path strokeLinecap="round" strokeLinejoin="round" d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126zM12 15.75h.007v.008H12v-.008z" /></svg>;
}
function IconGlobe() {
  return <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}><path strokeLinecap="round" strokeLinejoin="round" d="M12 21a9.004 9.004 0 008.716-6.747M12 21a9.004 9.004 0 01-8.716-6.747M12 21c2.485 0 4.5-4.03 4.5-9S14.485 3 12 3m0 18c-2.485 0-4.5-4.03-4.5-9S9.515 3 12 3m0 0a8.997 8.997 0 017.843 4.582M12 3a8.997 8.997 0 00-7.843 4.582m15.686 0A11.953 11.953 0 0112 10.5c-2.998 0-5.74-1.1-7.843-2.918m15.686 0A8.959 8.959 0 0121 12c0 .778-.099 1.533-.284 2.253m0 0A17.919 17.919 0 0112 16.5c-3.162 0-6.133-.815-8.716-2.247m0 0A9.015 9.015 0 013 12c0-1.605.42-3.113 1.157-4.418" /></svg>;
}
function IconStrategy() {
  return <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}><path strokeLinecap="round" strokeLinejoin="round" d="M3.75 13.5l10.5-11.25L12 10.5h8.25L9.75 21.75 12 13.5H3.75z" /></svg>;
}
function IconChart() {
  return <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}><path strokeLinecap="round" strokeLinejoin="round" d="M3 13.125C3 12.504 3.504 12 4.125 12h2.25c.621 0 1.125.504 1.125 1.125v6.75C7.5 20.496 6.996 21 6.375 21h-2.25A1.125 1.125 0 013 19.875v-6.75zM9.75 8.625c0-.621.504-1.125 1.125-1.125h2.25c.621 0 1.125.504 1.125 1.125v11.25c0 .621-.504 1.125-1.125 1.125h-2.25a1.125 1.125 0 01-1.125-1.125V8.625zM16.5 4.125c0-.621.504-1.125 1.125-1.125h2.25C20.496 3 21 3.504 21 4.125v15.75c0 .621-.504 1.125-1.125 1.125h-2.25a1.125 1.125 0 01-1.125-1.125V4.125z" /></svg>;
}
function IconArrowDown() {
  return <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}><path strokeLinecap="round" strokeLinejoin="round" d="M2.25 6L9 12.75l4.286-4.286a11.948 11.948 0 014.306 6.43l.776 2.898m0 0l3.182-5.511m-3.182 5.51l-5.511-3.181" /></svg>;
}
function IconCross() {
  return <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}><path strokeLinecap="round" strokeLinejoin="round" d="M7.5 21L3 16.5m0 0L7.5 12M3 16.5h13.5m0-13.5L21 7.5m0 0L16.5 12M21 7.5H7.5" /></svg>;
}
function IconCompress() {
  return <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}><path strokeLinecap="round" strokeLinejoin="round" d="M9 9V4.5M9 9H4.5M9 9L3.75 3.75M9 15v4.5M9 15H4.5M9 15l-5.25 5.25M15 9h4.5M15 9V4.5M15 9l5.25-5.25M15 15h4.5M15 15v4.5m0-4.5l5.25 5.25" /></svg>;
}
function IconBars() {
  return <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}><path strokeLinecap="round" strokeLinejoin="round" d="M3 4.5h14.25M3 9h9.75M3 13.5h9.75m4.5-4.5v12m0 0l-3.75-3.75M17.25 21L21 17.25" /></svg>;
}
function IconBreakout() {
  return <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}><path strokeLinecap="round" strokeLinejoin="round" d="M15.59 14.37a6 6 0 01-5.84 7.38v-4.8m5.84-2.58a14.98 14.98 0 006.16-12.12A14.98 14.98 0 009.631 8.41m5.96 5.96a14.926 14.926 0 01-5.841 2.58m-.119-8.54a6 6 0 00-7.381 5.84h4.8m2.58-5.84a14.927 14.927 0 00-2.58 5.84m2.699 2.7c-.103.021-.207.041-.311.06a15.09 15.09 0 01-2.448-2.448 14.9 14.9 0 01.06-.312m-2.24 2.39a4.493 4.493 0 00-1.757 4.306 4.493 4.493 0 004.306-1.758M16.5 9a1.5 1.5 0 11-3 0 1.5 1.5 0 013 0z" /></svg>;
}
function IconPercent() {
  return <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}><path strokeLinecap="round" strokeLinejoin="round" d="M20.25 3.75L3.75 20.25M8.25 6.75a1.5 1.5 0 110-3 1.5 1.5 0 010 3zm7.5 10.5a1.5 1.5 0 110-3 1.5 1.5 0 010 3z" /></svg>;
}
function IconBitcoin() {
  return <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}><path strokeLinecap="round" strokeLinejoin="round" d="M12 6v12m-3-2.818l.879.659c1.171.879 3.07.879 4.242 0 1.172-.879 1.172-2.303 0-3.182C13.536 12.219 12.768 12 12 12c-.725 0-1.45-.22-2.003-.659-1.106-.879-1.106-2.303 0-3.182s2.9-.879 4.006 0l.415.33M21 12a9 9 0 11-18 0 9 9 0 0118 0z" /></svg>;
}
function IconClock() {
  return <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}><path strokeLinecap="round" strokeLinejoin="round" d="M12 6v6h4.5m4.5 0a9 9 0 11-18 0 9 9 0 0118 0z" /></svg>;
}
function IconLayers() {
  return <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}><path strokeLinecap="round" strokeLinejoin="round" d="M6.429 9.75L2.25 12l4.179 2.25m0-4.5l5.571 3 5.571-3m-11.142 0L2.25 7.5 12 2.25l9.75 5.25-4.179 2.25m0 0L12 12.75l-5.571-3m11.142 0l4.179 2.25L12 17.25l-9.75-5.25 4.179-2.25m11.142 0l4.179 2.25L12 21.75l-9.75-5.25 4.179-2.25" /></svg>;
}
function IconBell() {
  return <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}><path strokeLinecap="round" strokeLinejoin="round" d="M14.857 17.082a23.848 23.848 0 005.454-1.31A8.967 8.967 0 0118 9.75v-.7V9A6 6 0 006 9v.75a8.967 8.967 0 01-2.312 6.022c1.733.64 3.56 1.085 5.455 1.31m5.714 0a24.255 24.255 0 01-5.714 0m5.714 0a3 3 0 11-5.714 0" /></svg>;
}

/* ═══════════════════════════════════════════════════════════
   Field Input Components
   ═══════════════════════════════════════════════════════════ */

function FieldInput({
  meta,
  value,
  onChange,
}: {
  meta: FieldMeta;
  value: unknown;
  onChange: (key: string, val: unknown) => void;
}) {
  const baseInput =
    "w-full bg-bg-primary border border-border rounded-lg px-3 py-2 text-sm text-text-primary focus:outline-none focus:ring-2 focus:ring-accent/40 focus:border-accent transition-colors";

  if (meta.type === "boolean") {
    const checked = value === 1 || value === true;
    return (
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        onClick={() => onChange(meta.key, checked ? 0 : 1)}
        className={`relative inline-flex h-6 w-11 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors ${
          checked ? "bg-accent" : "bg-border"
        }`}
      >
        <span
          className={`pointer-events-none inline-block h-5 w-5 transform rounded-full bg-white shadow-sm transition-transform ${
            checked ? "translate-x-5" : "translate-x-0"
          }`}
        />
      </button>
    );
  }

  if (meta.type === "select" && meta.options) {
    return (
      <select
        value={String(value ?? "")}
        onChange={(e) => onChange(meta.key, e.target.value)}
        className={baseInput}
      >
        {meta.options.map((opt) => (
          <option key={opt} value={opt}>
            {opt}
          </option>
        ))}
      </select>
    );
  }

  if (meta.type === "textarea") {
    return (
      <textarea
        value={String(value ?? "")}
        onChange={(e) => onChange(meta.key, e.target.value)}
        className={`${baseInput} min-h-[60px] resize-y`}
        rows={2}
      />
    );
  }

  if (meta.type === "integer" || meta.type === "number") {
    return (
      <div className="relative">
        <input
          type="number"
          value={value === undefined || value === null ? "" : String(value)}
          onChange={(e) => onChange(meta.key, e.target.value === "" ? 0 : parseInt(e.target.value, 10))}
          min={meta.min}
          max={meta.max}
          step={1}
          className={`${baseInput} ${meta.unit ? "pr-10" : ""}`}
        />
        {meta.unit && (
          <span className="absolute right-3 top-1/2 -translate-y-1/2 text-xs text-text-muted">
            {meta.unit}
          </span>
        )}
      </div>
    );
  }

  if (meta.type === "float") {
    return (
      <div className="relative">
        <input
          type="number"
          value={value === undefined || value === null ? "" : String(value)}
          onChange={(e) => onChange(meta.key, e.target.value === "" ? 0 : parseFloat(e.target.value))}
          min={meta.min}
          max={meta.max}
          step={meta.step ?? 0.1}
          className={`${baseInput} ${meta.unit ? "pr-10" : ""}`}
        />
        {meta.unit && (
          <span className="absolute right-3 top-1/2 -translate-y-1/2 text-xs text-text-muted">
            {meta.unit}
          </span>
        )}
      </div>
    );
  }

  // text
  return (
    <input
      type="text"
      value={String(value ?? "")}
      onChange={(e) => onChange(meta.key, e.target.value)}
      className={baseInput}
    />
  );
}

/* ═══════════════════════════════════════════════════════════
   Section Component
   ═══════════════════════════════════════════════════════════ */

function SectionCard({
  section,
  config,
  localChanges,
  onChange,
}: {
  section: Section;
  config: BotConfig;
  localChanges: Record<string, unknown>;
  onChange: (key: string, val: unknown) => void;
}) {
  const [collapsed, setCollapsed] = useState(false);

  return (
    <div className="bg-bg-card border border-border rounded-xl overflow-hidden">
      <button
        type="button"
        onClick={() => setCollapsed(!collapsed)}
        className="w-full px-5 py-4 flex items-center gap-3 hover:bg-bg-card-hover transition-colors"
      >
        <span className="text-accent">{section.icon}</span>
        <div className="flex-1 text-left">
          <h3 className="text-sm font-semibold text-text-primary">{section.title}</h3>
          <p className="text-xs text-text-muted">{section.description}</p>
        </div>
        {/* Count of changed fields in this section */}
        {section.fields.some((f) => f.key in localChanges) && (
          <span className="px-2 py-0.5 rounded-full bg-warning/15 text-warning text-[11px] font-bold">
            {section.fields.filter((f) => f.key in localChanges).length} changed
          </span>
        )}
        <svg
          className={`w-4 h-4 text-text-muted transition-transform ${collapsed ? "-rotate-90" : ""}`}
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
          strokeWidth={2}
        >
          <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
        </svg>
      </button>

      {!collapsed && (
        <div className="border-t border-border">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-x-6 gap-y-4 p-5">
            {section.fields.map((field) => {
              const currentValue = field.key in localChanges ? localChanges[field.key] : config[field.key];
              const isChanged = field.key in localChanges;

              if (field.type === "boolean") {
                return (
                  <div key={field.key} className="md:col-span-2 flex items-center gap-3 py-2 border-b border-border/20 last:border-0">
                    <div className="shrink-0">
                      <FieldInput meta={field} value={currentValue} onChange={onChange} />
                    </div>
                    <div className="min-w-0">
                      <div className="flex items-center gap-2">
                        <label className="text-xs font-medium text-text-secondary">{field.label}</label>
                        {isChanged && (
                          <span className="w-1.5 h-1.5 rounded-full bg-warning" title="Modified" />
                        )}
                      </div>
                      {field.description && (
                        <p className="text-[11px] text-text-muted">{field.description}</p>
                      )}
                    </div>
                  </div>
                );
              }

              return (
                <div key={field.key} className={`space-y-1.5 ${field.type === "textarea" ? "md:col-span-2" : ""}`}>
                  <div className="flex items-center gap-2">
                    <label className="text-xs font-medium text-text-secondary">{field.label}</label>
                    {isChanged && (
                      <span className="w-1.5 h-1.5 rounded-full bg-warning" title="Modified" />
                    )}
                  </div>
                  <FieldInput meta={field} value={currentValue} onChange={onChange} />
                  {field.description && (
                    <p className="text-[11px] text-text-muted">{field.description}</p>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════
   History Panel
   ═══════════════════════════════════════════════════════════ */


/* ═══════════════════════════════════════════════════════════
   Section Sidebar Nav
   ═══════════════════════════════════════════════════════════ */

function SectionNav({
  sections,
  activeId,
  onSelect,
}: {
  sections: Section[];
  activeId: string;
  onSelect: (id: string) => void;
}) {
  return (
    <nav className="space-y-0.5">
      {sections.map((s) => (
        <button
          key={s.id}
          type="button"
          onClick={() => onSelect(s.id)}
          className={`w-full text-left px-3 py-2 rounded-lg text-xs font-medium transition-all flex items-center gap-2 ${
            activeId === s.id
              ? "bg-accent/10 text-accent"
              : "text-text-muted hover:text-text-primary hover:bg-bg-card-hover"
          }`}
        >
          <span className="shrink-0">{s.icon}</span>
          <span className="truncate">{s.title}</span>
        </button>
      ))}
    </nav>
  );
}

/* ═══════════════════════════════════════════════════════════
   Main Settings Page
   ═══════════════════════════════════════════════════════════ */

export default function SettingsPage() {
  const { config, isLoading, mutate } = useConfig();
  const [localChanges, setLocalChanges] = useState<Record<string, unknown>>({});
  const [saving, setSaving] = useState(false);
  const [saveResult, setSaveResult] = useState<{ ok: boolean; count: number; error?: string } | null>(null);
  const [activeSection, setActiveSection] = useState(SECTIONS[0].id);
  const sectionRefs = useRef<Record<string, HTMLDivElement | null>>({});

  const handleChange = useCallback((key: string, val: unknown) => {
    setLocalChanges((prev) => {
      // If we're reverting to original value, remove from changes
      if (config && String(config[key as keyof BotConfig]) === String(val)) {
        const next = { ...prev };
        delete next[key];
        return next;
      }
      return { ...prev, [key]: val };
    });
    setSaveResult(null);
  }, [config]);

  const handleSave = useCallback(async () => {
    if (Object.keys(localChanges).length === 0) return;
    setSaving(true);
    setSaveResult(null);
    try {
      const result = await saveConfig(localChanges);
      if (result.ok) {
        const changedCount = Object.keys(result.changed).length;
        setSaveResult({ ok: true, count: changedCount });
        setLocalChanges({});
        mutate();
      } else {
        console.error("[Settings] Save failed:", result.error);
        setSaveResult({ ok: false, count: 0, error: result.error });
      }
    } catch (e) {
      console.error("[Settings] Save error:", e);
      setSaveResult({ ok: false, count: 0, error: String(e) });
    } finally {
      setSaving(false);
    }
  }, [localChanges, mutate]);

  const handleDiscard = useCallback(() => {
    setLocalChanges({});
    setSaveResult(null);
  }, []);

  const scrollToSection = useCallback((id: string) => {
    setActiveSection(id);
    sectionRefs.current[id]?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, []);

  // Track scroll position to highlight active section
  useEffect(() => {
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) {
            setActiveSection(entry.target.getAttribute("data-section-id") ?? "");
          }
        }
      },
      { rootMargin: "-100px 0px -60% 0px", threshold: 0 }
    );

    for (const ref of Object.values(sectionRefs.current)) {
      if (ref) observer.observe(ref);
    }

    return () => observer.disconnect();
  }, [isLoading]);

  if (isLoading || !config) {
    return (
      <div className="space-y-4">
        <Skeleton className="w-full h-16 rounded-xl" />
        <Skeleton className="w-full h-64 rounded-xl" />
        <Skeleton className="w-full h-64 rounded-xl" />
      </div>
    );
  }

  const changeCount = Object.keys(localChanges).length;

  return (
    <div className="flex gap-6">
      {/* Left sidebar: section nav */}
      <div className="w-52 shrink-0 hidden lg:block">
        <div className="sticky top-0 space-y-4">
          <h2 className="text-lg font-bold text-text-primary px-3">Settings</h2>
          <SectionNav sections={SECTIONS} activeId={activeSection} onSelect={scrollToSection} />

        </div>
      </div>

      {/* Main content */}
      <div className="flex-1 min-w-0">
        {/* Sticky save bar */}
        {changeCount > 0 && (
          <div className="sticky top-0 z-10 mb-4 bg-bg-card border border-warning/30 rounded-xl px-5 py-3 flex items-center justify-between shadow-lg">
            <div className="flex items-center gap-3">
              <span className="w-2 h-2 rounded-full bg-warning animate-pulse" />
              <span className="text-sm font-medium text-text-primary">
                {changeCount} unsaved change{changeCount > 1 ? "s" : ""}
              </span>
            </div>
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={handleDiscard}
                className="px-4 py-1.5 rounded-lg text-xs font-medium text-text-muted hover:text-text-primary hover:bg-bg-card-hover transition-colors border border-border"
              >
                Discard
              </button>
              <button
                type="button"
                onClick={handleSave}
                disabled={saving}
                className="px-4 py-1.5 rounded-lg text-xs font-bold text-white bg-accent hover:bg-accent/90 transition-colors disabled:opacity-50"
              >
                {saving ? "Saving..." : "Save Changes"}
              </button>
            </div>
          </div>
        )}

        {/* Save result toast */}
        {saveResult && (
          <div
            className={`mb-4 px-5 py-3 rounded-xl text-sm font-medium border ${
              saveResult.ok
                ? "bg-profit/10 border-profit/20 text-profit"
                : "bg-loss/10 border-loss/20 text-loss"
            }`}
          >
            {saveResult.ok
              ? `Saved ${saveResult.count} change${saveResult.count > 1 ? "s" : ""} successfully`
              : `Save failed: ${saveResult.error || "unknown error"}`}
          </div>
        )}

        {/* Title on mobile */}
        <div className="lg:hidden mb-4">
          <h2 className="text-lg font-bold text-text-primary">Settings</h2>
        </div>

        {/* Sections */}
        <div className="space-y-4">
          {SECTIONS.map((section) => (
            <div
              key={section.id}
              data-section-id={section.id}
              ref={(el) => { sectionRefs.current[section.id] = el; }}
            >
              <SectionCard
                section={section}
                config={config}
                localChanges={localChanges}
                onChange={handleChange}
              />
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
