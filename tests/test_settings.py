"""Tests for config.settings."""
import pytest

from config.settings import AIConfig, MarketConfig, RiskConfig, Settings, StrategyConfig, load_settings


class TestAIConfig:
    def test_defaults(self):
        c = AIConfig()
        assert c.model == "opus"
        assert c.timeout == 120
        assert c.decision_interval == 60
        assert c.min_confidence == 0.6
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
        assert c.stop_loss_pct == 1.0
        assert c.take_profit_pct == 1.5
        assert c.max_daily_drawdown_pct == 5.0
        assert c.max_total_drawdown_pct == 15.0
        assert c.max_open_positions == 15
        assert c.min_balance_usdc == 50.0
        assert c.auto_take_profit is False
        assert c.dynamic_positions is True
        assert c.usdc_per_position == 25.0
        assert c.target_utilization == 0.50
        assert c.max_size_boost == 2.5
        assert c.trailing_breakeven_pct == 1.0
        assert c.trailing_start_pct == 1.5
        assert c.trailing_distance_pct == 1.0
        assert c.trailing_tight_pct == 2.5
        assert c.trailing_tight_distance_pct == 0.75
        assert c.time_stop_hours == 4.0

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

    def test_frozen(self):
        c = MarketConfig()
        with pytest.raises(AttributeError):
            c.max_spread_pct = 1.0  # type: ignore[misc]


class TestStrategyConfig:
    def test_defaults(self):
        c = StrategyConfig()
        assert c.min_candle_volume_usdc == 10_000.0
        assert c.primary_interval == "5m"
        assert c.mr_interval == "15m"

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
        assert s.ai.model in ("opus", "haiku", "sonnet")  # from env or default
        assert s.market.max_spread_pct == 0.5
        assert s.strategy.min_candle_volume_usdc == 10_000.0
