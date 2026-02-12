"""Tests for core.types.Decision dataclass."""
from core.types import Decision


class TestDecision:
    def test_decision_required_fields(self):
        d = Decision(action="BUY", confidence=0.8, reasoning="test")
        assert d.action == "BUY"
        assert d.confidence == 0.8
        assert d.reasoning == "test"

    def test_decision_optional_fields_default_none(self):
        d = Decision(action="HOLD", confidence=0.5, reasoning="no signal")
        assert d.symbol is None
        assert d.size_pct is None
        assert d.order_type is None
        assert d.limit_price is None
        assert d.stop_loss is None
        assert d.take_profit is None
        assert d.strategy_type is None
        assert d.raw_response is None

    def test_decision_mutable(self):
        d = Decision(action="BUY", confidence=0.7, reasoning="test")
        d.stop_loss = 1900.0
        d.take_profit = 2100.0
        assert d.stop_loss == 1900.0
        assert d.take_profit == 2100.0

    def test_decision_all_actions(self):
        for action in ("BUY", "SHORT", "SELL", "HOLD", "CLOSE"):
            d = Decision(action=action, confidence=0.5, reasoning="test")
            assert d.action == action

    def test_decision_with_all_fields(self):
        d = Decision(
            action="BUY",
            confidence=0.9,
            reasoning="strong signal",
            symbol="ETH",
            size_pct=5.0,
            order_type="MARKET",
            limit_price=2000.0,
            stop_loss=1960.0,
            take_profit=2060.0,
            strategy_type="mean_reversion",
            raw_response='{"action": "BUY"}',
        )
        assert d.symbol == "ETH"
        assert d.size_pct == 5.0
        assert d.order_type == "MARKET"
        assert d.limit_price == 2000.0
        assert d.strategy_type == "mean_reversion"
        assert d.raw_response == '{"action": "BUY"}'

    def test_decision_confidence_bounds(self):
        low = Decision(action="BUY", confidence=0.0, reasoning="test")
        high = Decision(action="BUY", confidence=1.0, reasoning="test")
        assert low.confidence == 0.0
        assert high.confidence == 1.0

    def test_decision_size_pct_can_be_set(self):
        d = Decision(action="BUY", confidence=0.7, reasoning="test", size_pct=3.0)
        d.size_pct = 5.0
        assert d.size_pct == 5.0
