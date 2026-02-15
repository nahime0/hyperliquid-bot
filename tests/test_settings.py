"""Tests for config.settings."""
import pytest

from config.settings import AIConfig, MarketConfig, RiskConfig, Settings, StrategyConfig, load_settings


class TestAIConfig:
    def test_defaults(self):
        c = AIConfig()
        assert c.advisor == "cursor"
        assert c.model == "gemini-3-flash"
        assert c.timeout == 180
        assert c.decision_interval == 60
        assert c.min_confidence == 0.7
        assert c.fallback_on_error == "HOLD"

    def test_no_anthropic_fields(self):
        c = AIConfig()
        assert not hasattr(c, "haiku_model")
        assert not hasattr(c, "opus_model")
        assert not hasattr(c, "anthropic_api_key")

    def test_frozen(self):
        c = AIConfig()
        with pytest.raises(AttributeError):
            c.model = "haiku"  # type: ignore[misc]


class TestRiskConfig:
    def test_defaults(self):
        c = RiskConfig()
        assert c.max_trade_pct == 15.0
        assert c.stop_loss_pct == 2.0
        assert c.take_profit_pct == 1.5
        assert c.max_daily_drawdown_pct == 5.0
        assert c.max_total_drawdown_pct == 15.0
        assert c.max_open_positions == 3
        assert c.min_balance_usdc == 20.0
        assert c.auto_take_profit is False
        assert c.dynamic_positions is True
        assert c.usdc_per_position == 40.0
        assert c.target_utilization == 0.50
        assert c.max_size_boost == 2.5
        assert c.trailing_breakeven_pct == 1.0
        assert c.trailing_start_pct == 1.2
        assert c.trailing_distance_pct == 0.8
        assert c.trailing_tight_pct == 2.0
        assert c.trailing_tight_distance_pct == 0.4
        assert c.time_stop_hours == 4.0
        # ATR-based stop loss
        assert c.use_atr_sl is True
        assert c.atr_sl_multiplier == 2.5
        assert c.atr_sl_min_pct == 0.8
        assert c.atr_sl_max_pct == 1.5
        # Risk-based sizing
        assert c.risk_per_trade_pct == 1.0
        # Partial TP
        assert c.partial_tp_enabled is False
        assert c.partial_tp_pct == 50.0
        assert c.partial_tp_trigger_pct == 2.0
        # R:R gate
        assert c.min_rr_ratio == 1.5
        # ATR trailing
        assert c.use_atr_trailing is True
        assert c.atr_trailing_multiplier == 1.5
        assert c.atr_trailing_tight_multiplier == 1.0
        assert c.atr_trailing_min_pct == 0.5
        assert c.atr_trailing_max_pct == 2.0

    def test_frozen(self):
        c = RiskConfig()
        with pytest.raises(AttributeError):
            c.max_trade_pct = 20.0  # type: ignore[misc]


class TestMarketConfig:
    def test_defaults(self):
        c = MarketConfig()
        assert c.min_pair_volume == 50_000.0
        assert c.max_coins == 60
        assert c.max_spread_pct == 0.5
        assert c.coin_blacklist == ("AXS", "MOODENG", "CC", "HYPE")

    def test_frozen(self):
        c = MarketConfig()
        with pytest.raises(AttributeError):
            c.max_spread_pct = 1.0  # type: ignore[misc]


class TestStrategyConfig:
    def test_defaults(self):
        c = StrategyConfig()
        assert c.tf_allow_short is False
        assert c.min_candle_volume_usdc == 10_000.0
        assert c.primary_interval == "5m"
        assert c.mr_interval == "15m"
        assert c.rsi_div_long_exit == 55.0
        assert c.rsi_div_short_exit == 35.0

    def test_frozen(self):
        c = StrategyConfig()
        with pytest.raises(AttributeError):
            c.min_candle_volume_usdc = 5000.0  # type: ignore[misc]


class TestSettings:
    def test_frozen(self):
        s = Settings()
        with pytest.raises(AttributeError):
            s.log_level = "DEBUG"  # type: ignore[misc]

    def test_load_settings(self):
        s = load_settings()
        assert isinstance(s, Settings)
        assert isinstance(s.ai, AIConfig)
        assert isinstance(s.risk, RiskConfig)
        assert isinstance(s.market, MarketConfig)
        assert isinstance(s.strategy, StrategyConfig)
        assert isinstance(s.ai.model, str) and len(s.ai.model) > 0  # from env or default
        assert s.market.max_spread_pct == 0.5
        assert s.strategy.min_candle_volume_usdc == 10_000.0

    def test_active_strategies_default_13(self):
        """Default active_strategies includes all 13 strategies."""
        s = Settings()
        expected = {
            "mean_reversion", "rsi_divergence", "trend_following",
            "bb_squeeze", "breakout", "btc_correlation", "buy_the_dip",
            "ema_crossover", "funding_rate", "macd_divergence",
            "mtf_confluence", "session_momentum", "volume_spike",
        }
        assert set(s.strategy.active_strategies) == expected
        assert len(s.strategy.active_strategies) == 13
