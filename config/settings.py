from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# Load .env from project root
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_PROJECT_ROOT / ".env")


def _bool(val: str | None, default: bool = False) -> bool:
    if val is None:
        return default
    return val.strip().lower() in ("true", "1", "yes")


@dataclass(frozen=True)
class HyperliquidConfig:
    private_key: str = ""
    account_address: str = ""
    testnet: bool = True
    default_leverage: int = 2
    margin_mode: str = "cross"
    max_funding_rate: float = 0.0005

    @property
    def api_url(self) -> str:
        if self.testnet:
            return "https://api.hyperliquid-testnet.xyz"
        return "https://api.hyperliquid.xyz"


@dataclass(frozen=True)
class AIConfig:
    advisor: str = "claude"  # "claude" or "cursor"
    decision_interval: int = 60
    min_confidence: float = 0.6
    fallback_on_error: str = "HOLD"
    log_reasoning: bool = True
    model: str = "opus"
    timeout: int = 180


@dataclass(frozen=True)
class RiskConfig:
    max_trade_pct: float = 15.0
    stop_loss_pct: float = 1.5
    take_profit_pct: float = 1.5
    auto_take_profit: bool = False  # False → no auto-TP, rely on trailing stop
    max_daily_drawdown_pct: float = 5.0
    max_total_drawdown_pct: float = 15.0
    max_open_positions: int = 15        # hard cap (or static value if dynamic disabled)
    dynamic_positions: bool = True      # scale max positions with balance
    usdc_per_position: float = 25.0    # 1 position slot per N USDC (100 USDC → 4 slots)
    # Capital utilization
    target_utilization: float = 0.50    # target 50% balance as margin
    max_size_boost: float = 2.5         # max multiplier on base sizing
    min_balance_usdc: float = 50.0
    min_holding_minutes: int = 15  # minimum time before AI can close a position
    max_leverage: int = 3
    liquidation_buffer_pct: float = 5.0
    # Grace period: skip SL/TP check for newly opened positions
    sl_tp_grace_seconds: int = 30
    # Trailing stop
    trailing_half_pct: float = 0.5        # move SL to midpoint (entry+SL)/2 after +X%
    trailing_breakeven_pct: float = 0.5   # move SL to entry after +X%
    trailing_start_pct: float = 1.5       # start trailing after +X%
    trailing_distance_pct: float = 1.0    # trail at max_price - X%
    trailing_tight_pct: float = 2.5       # tighten trail after +X%
    trailing_tight_distance_pct: float = 0.75  # tight trail distance
    # Time stop
    time_stop_hours: float = 4.0          # close positions older than X hours
    time_stop_min_pnl_pct: float = 0.5    # only if PnL < X%
    # Cooldown
    symbol_cooldown_sec: int = 1800       # per-symbol cooldown after loss (30 min)
    global_cooldown_sec: int = 900        # global cooldown after N consecutive losses (15 min)
    global_cooldown_losses: int = 3       # trigger global cooldown after N losses
    # ATR-based stop loss
    use_atr_sl: bool = True               # use ATR to compute per-coin stop loss
    atr_sl_multiplier: float = 2.0        # SL = price +/- (multiplier * ATR)
    atr_sl_min_pct: float = 0.5           # floor: never tighter than 0.5%
    atr_sl_max_pct: float = 3.0           # ceiling: never wider than 3.0%
    # Risk-based position sizing
    risk_per_trade_pct: float = 1.0       # max % of bankroll to risk per trade
    # Partial take profit
    partial_tp_enabled: bool = False      # auto-close partial at trigger
    partial_tp_pct: float = 50.0          # % of position to close
    partial_tp_trigger_pct: float = 2.0   # trigger when gain >= +2% (after trailing starts)
    # R:R gate (only when TP is set)
    min_rr_ratio: float = 1.5            # min reward/risk ratio for entries
    # ATR-based trailing distance
    use_atr_trailing: bool = True
    atr_trailing_multiplier: float = 1.5       # normal trail = ATR * multiplier
    atr_trailing_tight_multiplier: float = 1.0 # tight trail = ATR * multiplier
    atr_trailing_min_pct: float = 0.5          # floor for ATR trailing %
    atr_trailing_max_pct: float = 2.0          # ceiling for ATR trailing %


@dataclass(frozen=True)
class MarketConfig:
    min_pair_volume: float = 50_000.0   # minimum 24h volume in USDC
    max_coins: int = 60                 # max perpetual coins to monitor
    max_spread_pct: float = 0.5         # max bid-ask spread % (blocks entry if exceeded)
    intervals: tuple[str, ...] = ("5m", "15m", "1h")  # candle intervals to subscribe


@dataclass(frozen=True)
class StrategyConfig:
    active_strategies: tuple[str, ...] = (
        "mean_reversion", "rsi_divergence", "trend_following",
        "bb_squeeze", "breakout", "btc_correlation", "buy_the_dip",
        "ema_crossover", "funding_rate", "macd_divergence",
        "mtf_confluence", "session_momentum", "volume_spike",
    )
    rsi_div_period: int = 14
    tf_change_threshold: float = 4.0  # % price change over 4h to fire trend signal
    rsi_div_swing_window: int = 5       # wider window to catch more divergences (was 4)
    rsi_div_long_exit: float = 55.0     # optimized: le=55 in 50% of top 30
    rsi_div_short_exit: float = 35.0    # optimized: se=35 in 73% of top 30
    min_candle_volume_usdc: float = 10_000.0  # skip coins with candle volume below this
    primary_interval: str = "5m"      # RSI Div uses 5m
    mr_interval: str = "15m"          # Mean Reversion uses 15m
    trend_interval: str = "1h"        # Trend filter on 1h
    # Buy the Dip
    btd_dip_min_pct: float = 0.5        # min dip % in 15 min
    btd_dip_max_pct: float = 3.0        # max dip % (above = crash, not dip)
    btd_volume_spike: float = 1.5       # volume spike multiplier for dip
    # EMA Crossover
    ema_cross_fast: int = 9             # fast EMA period
    ema_cross_slow: int = 21            # slow EMA period
    ema_cross_max_bars: int = 5         # max bars since crossover
    # BB Squeeze
    bbs_squeeze_percentile: float = 20.0  # bandwidth percentile for squeeze
    bbs_lookback_bars: int = 100          # bars to compute bandwidth percentile
    bbs_min_volume_spike: float = 1.5     # volume spike on breakout
    # MACD Divergence
    macd_div_swing_window: int = 5       # window to detect swing highs/lows
    macd_div_recency: int = 40           # max bars to look back for divergence
    # Volume Spike Reversal
    vs_spike_threshold: float = 3.0      # volume spike multiplier vs 20-bar avg
    vs_wick_ratio: float = 2.0           # wick/body ratio for reversal candle
    # S/R Breakout
    bo_lookback_hours: int = 24           # hours of 1h candles for S/R levels
    bo_min_breakout_pct: float = 0.1      # min % past level to confirm breakout
    bo_confirm_bars: int = 2              # 15m bars that must all be past level
    # Support/Resistance Breakout
    bo_lookback_hours: int = 24          # hours to compute S/R levels
    bo_min_breakout_pct: float = 0.1     # min % above/below S/R for breakout
    bo_confirm_bars: int = 2             # bars to confirm breakout
    # Funding Rate Contrarian
    fr_extreme_negative: float = -0.0005   # extreme negative funding (SHORT)
    fr_extreme_positive: float = 0.0005    # extreme positive funding (LONG)
    fr_normalize_threshold: float = 0.0001 # funding considered normal below this
    # BTC Correlation Lag
    btc_min_move_pct: float = 1.0        # min BTC move % in 1h
    btc_min_lag_pct: float = 0.5         # min lag between BTC and altcoin
    btc_catch_up_pct: float = 0.2        # gap considered closed below this
    # Session Momentum
    sm_min_session_change: float = 0.5   # min % change from prior session
    sm_entry_window_hours: int = 2       # first N hours of new session
    sm_exit_hours: float = 4.0           # max hours to hold session trade
    # Multi-Timeframe Confluence
    mtf_rsi_oversold: float = 35.0       # 5m RSI oversold threshold
    mtf_rsi_overbought: float = 65.0     # 5m RSI overbought threshold
    mtf_rsi_slope_bars: int = 3          # bars to measure RSI slope on 15m


@dataclass(frozen=True)
class TelegramConfig:
    bot_token: str = ""
    chat_id: str = ""

    @property
    def enabled(self) -> bool:
        return bool(self.bot_token and self.chat_id)


@dataclass(frozen=True)
class Settings:
    hyperliquid: HyperliquidConfig = field(default_factory=HyperliquidConfig)
    ai: AIConfig = field(default_factory=AIConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    market: MarketConfig = field(default_factory=MarketConfig)
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    log_level: str = "INFO"
    db_path: str = "data/trading_bot.db"
    project_root: Path = _PROJECT_ROOT


def load_settings() -> Settings:
    """Load settings from environment variables."""
    return Settings(
        hyperliquid=HyperliquidConfig(
            private_key=os.getenv("HL_PRIVATE_KEY", ""),
            account_address=os.getenv("HL_ACCOUNT_ADDRESS", ""),
            testnet=_bool(os.getenv("HL_TESTNET"), default=True),
            default_leverage=int(os.getenv("HL_DEFAULT_LEVERAGE", "2")),
            margin_mode=os.getenv("HL_MARGIN_MODE", "cross"),
            max_funding_rate=float(os.getenv("HL_MAX_FUNDING_RATE", "0.0005")),
        ),
        ai=AIConfig(
            advisor=os.getenv("AI_ADVISOR", "claude"),
            decision_interval=int(os.getenv("AI_DECISION_INTERVAL", "60")),
            min_confidence=float(os.getenv("AI_MIN_CONFIDENCE", "0.6")),
            fallback_on_error=os.getenv("AI_FALLBACK_ON_ERROR", "HOLD"),
            log_reasoning=_bool(os.getenv("AI_LOG_REASONING"), default=True),
            model=os.getenv("AI_MODEL", "opus"),
            timeout=int(os.getenv("AI_TIMEOUT", "180")),
        ),
        risk=RiskConfig(
            max_trade_pct=float(os.getenv("MAX_TRADE_PCT", "15")),
            stop_loss_pct=float(os.getenv("STOP_LOSS_PCT", "1.5")),
            take_profit_pct=float(os.getenv("TAKE_PROFIT_PCT", "1.5")),
            auto_take_profit=_bool(os.getenv("AUTO_TAKE_PROFIT"), default=False),
            max_daily_drawdown_pct=float(os.getenv("MAX_DAILY_DRAWDOWN_PCT", "5.0")),
            max_total_drawdown_pct=float(os.getenv("MAX_TOTAL_DRAWDOWN_PCT", "15.0")),
            max_open_positions=int(os.getenv("MAX_OPEN_POSITIONS", "15")),
            dynamic_positions=_bool(os.getenv("DYNAMIC_POSITIONS"), default=True),
            usdc_per_position=float(os.getenv("USDC_PER_POSITION", "25.0")),
            min_balance_usdc=float(os.getenv("MIN_BALANCE_USDC", "50.0")),
            min_holding_minutes=int(os.getenv("MIN_HOLDING_MINUTES", "15")),
            trailing_half_pct=float(os.getenv("TRAILING_HALF_PCT", "0.5")),
            trailing_breakeven_pct=float(os.getenv("TRAILING_BREAKEVEN_PCT", "0.5")),
            trailing_start_pct=float(os.getenv("TRAILING_START_PCT", "1.5")),
            trailing_distance_pct=float(os.getenv("TRAILING_DISTANCE_PCT", "1.0")),
            trailing_tight_pct=float(os.getenv("TRAILING_TIGHT_PCT", "2.5")),
            trailing_tight_distance_pct=float(os.getenv("TRAILING_TIGHT_DISTANCE_PCT", "0.75")),
            time_stop_hours=float(os.getenv("TIME_STOP_HOURS", "4.0")),
            time_stop_min_pnl_pct=float(os.getenv("TIME_STOP_MIN_PNL_PCT", "0.5")),
            target_utilization=float(os.getenv("TARGET_UTILIZATION", "0.50")),
            max_size_boost=float(os.getenv("MAX_SIZE_BOOST", "2.5")),
            symbol_cooldown_sec=int(os.getenv("SYMBOL_COOLDOWN_SEC", "1800")),
            global_cooldown_sec=int(os.getenv("GLOBAL_COOLDOWN_SEC", "900")),
            global_cooldown_losses=int(os.getenv("GLOBAL_COOLDOWN_LOSSES", "3")),
            use_atr_sl=_bool(os.getenv("USE_ATR_SL"), default=True),
            atr_sl_multiplier=float(os.getenv("ATR_SL_MULTIPLIER", "2.0")),
            atr_sl_min_pct=float(os.getenv("ATR_SL_MIN_PCT", "0.5")),
            atr_sl_max_pct=float(os.getenv("ATR_SL_MAX_PCT", "3.0")),
            risk_per_trade_pct=float(os.getenv("RISK_PER_TRADE_PCT", "1.0")),
            partial_tp_enabled=_bool(os.getenv("PARTIAL_TP_ENABLED"), default=False),
            partial_tp_pct=float(os.getenv("PARTIAL_TP_PCT", "50.0")),
            partial_tp_trigger_pct=float(os.getenv("PARTIAL_TP_TRIGGER_PCT", "2.0")),
            min_rr_ratio=float(os.getenv("MIN_RR_RATIO", "1.5")),
            use_atr_trailing=_bool(os.getenv("USE_ATR_TRAILING"), default=True),
            atr_trailing_multiplier=float(os.getenv("ATR_TRAILING_MULTIPLIER", "1.5")),
            atr_trailing_tight_multiplier=float(os.getenv("ATR_TRAILING_TIGHT_MULTIPLIER", "1.0")),
            atr_trailing_min_pct=float(os.getenv("ATR_TRAILING_MIN_PCT", "0.5")),
            atr_trailing_max_pct=float(os.getenv("ATR_TRAILING_MAX_PCT", "2.0")),
        ),
        market=MarketConfig(
            min_pair_volume=float(os.getenv("MIN_PAIR_VOLUME", "50000")),
            max_coins=int(os.getenv("MAX_COINS", "60")),
            max_spread_pct=float(os.getenv("MAX_SPREAD_PCT", "0.5")),
        ),
        strategy=StrategyConfig(
            active_strategies=tuple(
                os.getenv(
                    "ACTIVE_STRATEGIES",
                    "mean_reversion,rsi_divergence,trend_following,"
                    "bb_squeeze,breakout,btc_correlation,buy_the_dip,"
                    "ema_crossover,funding_rate,macd_divergence,"
                    "mtf_confluence,session_momentum,volume_spike",
                ).split(",")
            ),
            rsi_div_period=int(os.getenv("RSI_DIV_PERIOD", "14")),
            rsi_div_swing_window=int(os.getenv("RSI_DIV_SWING_WINDOW", "5")),
            rsi_div_long_exit=float(os.getenv("RSI_DIV_LONG_EXIT", "55.0")),
            rsi_div_short_exit=float(os.getenv("RSI_DIV_SHORT_EXIT", "35.0")),
            min_candle_volume_usdc=float(os.getenv("MIN_CANDLE_VOLUME_USDC", "10000")),
            primary_interval=os.getenv("PRIMARY_INTERVAL", "5m"),
            mr_interval=os.getenv("MR_INTERVAL", "15m"),
            trend_interval=os.getenv("TREND_INTERVAL", "1h"),
            tf_change_threshold=float(os.getenv("TF_CHANGE_THRESHOLD", "4.0")),
            # Buy the Dip
            btd_dip_min_pct=float(os.getenv("BTD_DIP_MIN_PCT", "0.5")),
            btd_dip_max_pct=float(os.getenv("BTD_DIP_MAX_PCT", "3.0")),
            btd_volume_spike=float(os.getenv("BTD_VOLUME_SPIKE", "1.5")),
            # EMA Crossover
            ema_cross_fast=int(os.getenv("EMA_CROSS_FAST", "9")),
            ema_cross_slow=int(os.getenv("EMA_CROSS_SLOW", "21")),
            ema_cross_max_bars=int(os.getenv("EMA_CROSS_MAX_BARS", "5")),
            # BB Squeeze
            bbs_squeeze_percentile=float(os.getenv("BBS_SQUEEZE_PERCENTILE", "20.0")),
            bbs_lookback_bars=int(os.getenv("BBS_LOOKBACK_BARS", "100")),
            bbs_min_volume_spike=float(os.getenv("BBS_MIN_VOLUME_SPIKE", "1.5")),
            # MACD Divergence
            macd_div_swing_window=int(os.getenv("MACD_DIV_SWING_WINDOW", "5")),
            macd_div_recency=int(os.getenv("MACD_DIV_RECENCY", "40")),
            # Volume Spike Reversal
            vs_spike_threshold=float(os.getenv("VS_SPIKE_THRESHOLD", "3.0")),
            vs_wick_ratio=float(os.getenv("VS_WICK_RATIO", "2.0")),
            # Support/Resistance Breakout
            bo_lookback_hours=int(os.getenv("BO_LOOKBACK_HOURS", "24")),
            bo_min_breakout_pct=float(os.getenv("BO_MIN_BREAKOUT_PCT", "0.1")),
            bo_confirm_bars=int(os.getenv("BO_CONFIRM_BARS", "2")),
            # Funding Rate Contrarian
            fr_extreme_negative=float(os.getenv("FR_EXTREME_NEGATIVE", "-0.0005")),
            fr_extreme_positive=float(os.getenv("FR_EXTREME_POSITIVE", "0.0005")),
            fr_normalize_threshold=float(os.getenv("FR_NORMALIZE_THRESHOLD", "0.0001")),
            # BTC Correlation Lag
            btc_min_move_pct=float(os.getenv("BTC_MIN_MOVE_PCT", "1.0")),
            btc_min_lag_pct=float(os.getenv("BTC_MIN_LAG_PCT", "0.5")),
            btc_catch_up_pct=float(os.getenv("BTC_CATCH_UP_PCT", "0.2")),
            # Session Momentum
            sm_min_session_change=float(os.getenv("SM_MIN_SESSION_CHANGE", "0.5")),
            sm_entry_window_hours=int(os.getenv("SM_ENTRY_WINDOW_HOURS", "2")),
            sm_exit_hours=float(os.getenv("SM_EXIT_HOURS", "4.0")),
            # Multi-Timeframe Confluence
            mtf_rsi_oversold=float(os.getenv("MTF_RSI_OVERSOLD", "35.0")),
            mtf_rsi_overbought=float(os.getenv("MTF_RSI_OVERBOUGHT", "65.0")),
            mtf_rsi_slope_bars=int(os.getenv("MTF_RSI_SLOPE_BARS", "3")),
        ),
        telegram=TelegramConfig(
            bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
            chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
        ),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
        db_path=os.getenv("DB_PATH", "data/trading_bot.db"),
    )
