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
    decision_interval: int = 60
    timeout: int = 120
    min_confidence: float = 0.6
    fallback_on_error: str = "HOLD"
    log_reasoning: bool = True
    # Anthropic API (direct SDK calls)
    anthropic_api_key: str = ""
    haiku_model: str = "claude-haiku-4-5-20251001"
    haiku_timeout: int = 15
    haiku_hold_confidence: float = 0.7
    opus_model: str = "claude-sonnet-4-5-20250929"
    opus_timeout: int = 60
    # Multi-backend: screening tier (Tier 2)
    screening_backend: str = "anthropic_sdk"
    screening_model: str = ""  # fallback to haiku_model
    screening_timeout: int = 0  # fallback to haiku_timeout
    # Multi-backend: analysis tier (Tier 3)
    analysis_backend: str = "anthropic_sdk"
    analysis_model: str = ""  # fallback to opus_model
    analysis_timeout: int = 0  # fallback to opus_timeout
    # Pre-screen thresholds
    max_spread_pct: float = 0.5


@dataclass(frozen=True)
class RiskConfig:
    max_trade_pct: float = 10.0
    stop_loss_pct: float = 1.0
    take_profit_pct: float = 1.5
    max_daily_drawdown_pct: float = 5.0
    max_total_drawdown_pct: float = 15.0
    max_open_positions: int = 5
    min_balance_usdc: float = 50.0
    min_holding_minutes: int = 15  # minimum time before AI can close a position
    max_leverage: int = 3
    liquidation_buffer_pct: float = 5.0
    # Trailing stop
    trailing_breakeven_pct: float = 1.0   # move SL to entry after +X%
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


@dataclass(frozen=True)
class MarketConfig:
    min_pair_volume: float = 50_000.0   # minimum 24h volume in USDC
    max_coins: int = 60                 # max perpetual coins to monitor
    intervals: tuple[str, ...] = ("5m", "15m", "1h")  # candle intervals to subscribe


@dataclass(frozen=True)
class StrategyConfig:
    active_strategies: tuple[str, ...] = ("mean_reversion", "rsi_divergence")
    rsi_div_period: int = 14
    rsi_div_swing_window: int = 5
    rsi_div_long_exit: float = 55.0   # optimized from sweep (was 60)
    rsi_div_short_exit: float = 35.0  # optimized from sweep (was 40)
    primary_interval: str = "5m"      # RSI Div uses 5m
    mr_interval: str = "15m"          # Mean Reversion uses 15m
    trend_interval: str = "1h"        # Trend filter on 1h


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
            decision_interval=int(os.getenv("AI_DECISION_INTERVAL", "60")),
            timeout=int(os.getenv("AI_TIMEOUT", "120")),
            min_confidence=float(os.getenv("AI_MIN_CONFIDENCE", "0.6")),
            fallback_on_error=os.getenv("AI_FALLBACK_ON_ERROR", "HOLD"),
            log_reasoning=_bool(os.getenv("AI_LOG_REASONING"), default=True),
            anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", ""),
            haiku_model=os.getenv("AI_HAIKU_MODEL", "claude-haiku-4-5-20251001"),
            haiku_timeout=int(os.getenv("AI_HAIKU_TIMEOUT", "15")),
            haiku_hold_confidence=float(os.getenv("AI_HAIKU_HOLD_CONFIDENCE", "0.7")),
            opus_model=os.getenv("AI_OPUS_MODEL", "claude-sonnet-4-5-20250929"),
            opus_timeout=int(os.getenv("AI_OPUS_TIMEOUT", "60")),
            screening_backend=os.getenv("AI_SCREENING_BACKEND", "anthropic_sdk"),
            screening_model=os.getenv("AI_SCREENING_MODEL", ""),
            screening_timeout=int(os.getenv("AI_SCREENING_TIMEOUT", "0")),
            analysis_backend=os.getenv("AI_ANALYSIS_BACKEND", "anthropic_sdk"),
            analysis_model=os.getenv("AI_ANALYSIS_MODEL", ""),
            analysis_timeout=int(os.getenv("AI_ANALYSIS_TIMEOUT", "0")),
            max_spread_pct=float(os.getenv("AI_MAX_SPREAD_PCT", "0.5")),
        ),
        risk=RiskConfig(
            max_trade_pct=float(os.getenv("MAX_TRADE_PCT", "10")),
            stop_loss_pct=float(os.getenv("STOP_LOSS_PCT", "1.0")),
            take_profit_pct=float(os.getenv("TAKE_PROFIT_PCT", "1.5")),
            max_daily_drawdown_pct=float(os.getenv("MAX_DAILY_DRAWDOWN_PCT", "5.0")),
            max_total_drawdown_pct=float(os.getenv("MAX_TOTAL_DRAWDOWN_PCT", "15.0")),
            max_open_positions=int(os.getenv("MAX_OPEN_POSITIONS", "5")),
            min_balance_usdc=float(os.getenv("MIN_BALANCE_USDC", "50.0")),
            min_holding_minutes=int(os.getenv("MIN_HOLDING_MINUTES", "15")),
            trailing_breakeven_pct=float(os.getenv("TRAILING_BREAKEVEN_PCT", "1.0")),
            trailing_start_pct=float(os.getenv("TRAILING_START_PCT", "1.5")),
            trailing_distance_pct=float(os.getenv("TRAILING_DISTANCE_PCT", "1.0")),
            trailing_tight_pct=float(os.getenv("TRAILING_TIGHT_PCT", "2.5")),
            trailing_tight_distance_pct=float(os.getenv("TRAILING_TIGHT_DISTANCE_PCT", "0.75")),
            time_stop_hours=float(os.getenv("TIME_STOP_HOURS", "4.0")),
            time_stop_min_pnl_pct=float(os.getenv("TIME_STOP_MIN_PNL_PCT", "0.5")),
            symbol_cooldown_sec=int(os.getenv("SYMBOL_COOLDOWN_SEC", "1800")),
            global_cooldown_sec=int(os.getenv("GLOBAL_COOLDOWN_SEC", "900")),
            global_cooldown_losses=int(os.getenv("GLOBAL_COOLDOWN_LOSSES", "3")),
        ),
        market=MarketConfig(
            min_pair_volume=float(os.getenv("MIN_PAIR_VOLUME", "50000")),
            max_coins=int(os.getenv("MAX_COINS", "60")),
        ),
        strategy=StrategyConfig(
            active_strategies=tuple(
                os.getenv("ACTIVE_STRATEGIES", "mean_reversion,rsi_divergence").split(",")
            ),
            rsi_div_period=int(os.getenv("RSI_DIV_PERIOD", "14")),
            rsi_div_swing_window=int(os.getenv("RSI_DIV_SWING_WINDOW", "5")),
            rsi_div_long_exit=float(os.getenv("RSI_DIV_LONG_EXIT", "55.0")),
            rsi_div_short_exit=float(os.getenv("RSI_DIV_SHORT_EXIT", "35.0")),
            primary_interval=os.getenv("PRIMARY_INTERVAL", "5m"),
            mr_interval=os.getenv("MR_INTERVAL", "15m"),
            trend_interval=os.getenv("TREND_INTERVAL", "1h"),
        ),
        telegram=TelegramConfig(
            bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
            chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
        ),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
        db_path=os.getenv("DB_PATH", "data/trading_bot.db"),
    )
