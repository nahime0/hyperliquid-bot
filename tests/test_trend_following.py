"""Tests for TrendFollowingStrategy scoring-based entry system."""
from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from config.settings import RiskConfig
from core.types import Decision
from strategies.cooldown import CooldownTracker
from strategies.multi_strategy import MultiStrategy
from strategies.trend_following import (
    TrendFollowingStrategy,
    TrendSignal,
    SCORE_THRESHOLD,
    W_CHANGE_4H,
    W_TREND_1H,
    W_TREND_4H,
    W_VOLUME,
    W_FUNDING,
    W_COOLDOWN,
    HARD_FUNDING_RATE,
    HARD_COOLDOWN_FRESHNESS,
    CHANGE_4H_THRESHOLD,
    MIN_VOLUME_RATIO,
)


# -- Helpers --


def _make_strategy(
    cooldown: CooldownTracker | None = None,
    risk_config: RiskConfig | None = None,
    change_threshold: float = CHANGE_4H_THRESHOLD,
) -> TrendFollowingStrategy:
    rc = risk_config or RiskConfig()
    cd = cooldown or CooldownTracker(rc)
    return TrendFollowingStrategy(
        market_data=MagicMock(),
        trend_filter=MagicMock(),
        cooldown=cd,
        position_tracker=MagicMock(),
        risk_config=rc,
        coins=[],
        change_threshold=change_threshold,
    )


def _make_signal(
    symbol: str = "ETH",
    change_4h: float | None = -4.0,
    trend_1h: str = "BEARISH",
    trend_4h: str = "BEARISH",
    volume_ratio: float | None = 1.5,
    price: float | None = 2000.0,
) -> TrendSignal:
    return TrendSignal(
        symbol=symbol,
        change_4h=change_4h,
        trend_1h=trend_1h,
        trend_4h=trend_4h,
        volume_ratio=volume_ratio,
        price=price,
        updated_at=time.time(),
    )


# -- SHORT entry scoring --


class TestShortEntryScoring:
    def test_full_score_all_conditions(self):
        """All 6 conditions met -> score=1.0."""
        s = _make_strategy()
        sig = _make_signal(change_4h=-4.0, trend_1h="BEARISH", trend_4h="BEARISH",
                           volume_ratio=1.5)
        d = s._check_short_entry("ETH", sig)
        assert d is not None
        assert d.action == "SHORT"
        assert d.confidence == 1.0

    def test_change_and_trends_only(self):
        """Change + both trends but all minors fail -> score=0.70."""
        s = _make_strategy()
        s._funding_rates["ETH"] = 0.0007  # > max but < hard
        sig = _make_signal(change_4h=-4.0, trend_1h="BEARISH", trend_4h="BEARISH",
                           volume_ratio=0.5)
        # chg(0.30) + t1h(0.20) + t4h(0.20) + vol(0) + fund(0) + cd(0.10) = 0.80
        d = s._check_short_entry("ETH", sig)
        assert d is not None
        assert d.confidence == 0.80

    def test_change_only_with_minors(self):
        """Only change_4h met + minors -> score=0.60."""
        s = _make_strategy()
        sig = _make_signal(change_4h=-4.0, trend_1h="NEUTRAL", trend_4h="NEUTRAL",
                           volume_ratio=1.5)
        # chg(0.30) + t1h(0) + t4h(0) + vol(0.10) + fund(0.10) + cd(0.10) = 0.60
        d = s._check_short_entry("ETH", sig)
        assert d is not None
        assert d.confidence == 0.60

    def test_trends_only_no_change(self):
        """Both trends BEARISH but change < threshold -> score=0.60."""
        s = _make_strategy()
        sig = _make_signal(change_4h=-2.0, trend_1h="BEARISH", trend_4h="BEARISH",
                           volume_ratio=1.5)
        # chg(0) + t1h(0.20) + t4h(0.20) + vol(0.10) + fund(0.10) + cd(0.10) = 0.70
        d = s._check_short_entry("ETH", sig)
        assert d is not None
        assert d.confidence == 0.70

    def test_below_threshold_no_signal(self):
        """Nothing strong enough -> below threshold."""
        s = _make_strategy()
        sig = _make_signal(change_4h=-1.0, trend_1h="NEUTRAL", trend_4h="NEUTRAL",
                           volume_ratio=0.5)
        # chg(0) + t1h(0) + t4h(0) + vol(0) + fund(0.10) + cd(0.10) = 0.20
        d = s._check_short_entry("ETH", sig)
        assert d is None

    def test_partial_score_just_passes(self):
        """Score exactly at threshold -> signal generated."""
        s = _make_strategy()
        sig = _make_signal(change_4h=-4.0, trend_1h="NEUTRAL", trend_4h="NEUTRAL",
                           volume_ratio=1.5)
        s._funding_rates["ETH"] = 0.0007  # no funding points
        # chg(0.30) + vol(0.10) + cd(0.10) = 0.50
        d = s._check_short_entry("ETH", sig)
        assert d is not None
        assert d.confidence == 0.50


# -- LONG entry scoring (mirrored) --


class TestLongEntryScoring:
    def test_full_score_all_conditions(self):
        """All 6 conditions met -> score=1.0."""
        s = _make_strategy()
        sig = _make_signal(change_4h=4.0, trend_1h="BULLISH", trend_4h="BULLISH",
                           volume_ratio=1.5)
        d = s._check_long_entry("ETH", sig)
        assert d is not None
        assert d.action == "BUY"
        assert d.confidence == 1.0

    def test_change_and_trends_only(self):
        """Change + both trends -> score=0.70 + minors."""
        s = _make_strategy()
        sig = _make_signal(change_4h=4.0, trend_1h="BULLISH", trend_4h="BULLISH",
                           volume_ratio=0.5)
        s._funding_rates["ETH"] = 0.0007
        # chg(0.30) + t1h(0.20) + t4h(0.20) + vol(0) + fund(0) + cd(0.10) = 0.80
        d = s._check_long_entry("ETH", sig)
        assert d is not None
        assert d.confidence == 0.80

    def test_below_threshold_no_signal(self):
        s = _make_strategy()
        sig = _make_signal(change_4h=1.0, trend_1h="NEUTRAL", trend_4h="NEUTRAL",
                           volume_ratio=0.5)
        d = s._check_long_entry("ETH", sig)
        assert d is None

    def test_change_exactly_at_threshold(self):
        """change_4h == 3.0 (>= threshold) -> gets points."""
        s = _make_strategy()
        sig = _make_signal(change_4h=3.0, trend_1h="BULLISH", trend_4h="BULLISH",
                           volume_ratio=1.5)
        d = s._check_long_entry("ETH", sig)
        assert d is not None
        assert d.confidence == 1.0

    def test_reasoning_contains_score(self):
        s = _make_strategy()
        sig = _make_signal(change_4h=5.0, trend_1h="BULLISH", trend_4h="BULLISH")
        d = s._check_long_entry("ETH", sig)
        assert d is not None
        assert "score=" in d.reasoning
        assert "TrendFollow LONG" in d.reasoning


# -- Hard blocks --


class TestHardBlocks:
    def test_extreme_funding_blocks_short(self):
        s = _make_strategy()
        s._funding_rates["ETH"] = 0.0015
        sig = _make_signal()
        assert s._check_short_entry("ETH", sig) is None

    def test_extreme_funding_blocks_long(self):
        s = _make_strategy()
        s._funding_rates["ETH"] = -0.0015
        sig = _make_signal(change_4h=5.0, trend_1h="BULLISH", trend_4h="BULLISH")
        assert s._check_long_entry("ETH", sig) is None

    def test_moderate_funding_soft_penalty(self):
        """Funding between max_funding_rate and HARD_FUNDING_RATE -> no hard block, just no points."""
        s = _make_strategy()
        s._funding_rates["ETH"] = 0.0007
        sig = _make_signal(change_4h=-4.0, trend_1h="BEARISH", trend_4h="BEARISH",
                           volume_ratio=1.5)
        d = s._check_short_entry("ETH", sig)
        assert d is not None
        # All except funding: 0.30+0.20+0.20+0.10+0+0.10 = 0.90
        assert d.confidence == 0.90

    def test_global_cooldown_blocks_short(self):
        rc = RiskConfig()
        cd = CooldownTracker(rc)
        cd._global_cooldown_until = time.time() + 300
        s = _make_strategy(cooldown=cd, risk_config=rc)
        sig = _make_signal()
        assert s._check_short_entry("ETH", sig) is None

    def test_global_cooldown_blocks_long(self):
        rc = RiskConfig()
        cd = CooldownTracker(rc)
        cd._global_cooldown_until = time.time() + 300
        s = _make_strategy(cooldown=cd, risk_config=rc)
        sig = _make_signal(change_4h=5.0, trend_1h="BULLISH", trend_4h="BULLISH")
        assert s._check_long_entry("ETH", sig) is None

    def test_fresh_cooldown_blocks(self):
        """Loss < 10 min ago -> hard block."""
        rc = RiskConfig()
        cd = CooldownTracker(rc)
        cd.record_trade_result("ETH", is_win=False)
        s = _make_strategy(cooldown=cd, risk_config=rc)
        sig = _make_signal()
        assert s._check_short_entry("ETH", sig) is None

    def test_stale_cooldown_soft_penalty(self):
        """Loss > 10 min ago -> only loses 0.10, not hard-blocked."""
        rc = RiskConfig()
        cd = CooldownTracker(rc)
        cd._symbol_cooldowns["ETH"] = time.time() + 300  # 5 min remaining
        s = _make_strategy(cooldown=cd, risk_config=rc)
        sig = _make_signal(change_4h=-4.0, trend_1h="BEARISH", trend_4h="BEARISH",
                           volume_ratio=1.5)
        d = s._check_short_entry("ETH", sig)
        assert d is not None
        # All except cooldown: 0.30+0.20+0.20+0.10+0.10 = 0.90
        assert d.confidence == 0.90

    def test_no_funding_data_gives_points(self):
        s = _make_strategy()
        sig = _make_signal()
        d = s._check_short_entry("ETH", sig)
        assert d is not None
        assert d.confidence == 1.0


# -- Exit conditions --


class TestExits:
    def test_short_exit_on_trend_1h_bullish(self):
        s = _make_strategy()
        sig = _make_signal(trend_1h="BULLISH", trend_4h="NEUTRAL")
        d = s._check_short_exit("ETH", sig)
        assert d is not None
        assert d.action == "CLOSE"
        assert "trend_1h=BULLISH" in d.reasoning

    def test_short_exit_on_trend_4h_bullish(self):
        s = _make_strategy()
        sig = _make_signal(trend_1h="NEUTRAL", trend_4h="BULLISH")
        d = s._check_short_exit("ETH", sig)
        assert d is not None
        assert d.action == "CLOSE"

    def test_short_no_exit_if_still_bearish(self):
        s = _make_strategy()
        sig = _make_signal(trend_1h="BEARISH", trend_4h="BEARISH")
        d = s._check_short_exit("ETH", sig)
        assert d is None

    def test_short_no_exit_if_neutral(self):
        s = _make_strategy()
        sig = _make_signal(trend_1h="NEUTRAL", trend_4h="NEUTRAL")
        d = s._check_short_exit("ETH", sig)
        assert d is None

    def test_long_exit_on_trend_1h_bearish(self):
        s = _make_strategy()
        sig = _make_signal(trend_1h="BEARISH", trend_4h="NEUTRAL")
        d = s._check_long_exit("ETH", sig)
        assert d is not None
        assert d.action == "CLOSE"
        assert "trend_1h=BEARISH" in d.reasoning

    def test_long_exit_on_trend_4h_bearish(self):
        s = _make_strategy()
        sig = _make_signal(trend_1h="NEUTRAL", trend_4h="BEARISH")
        d = s._check_long_exit("ETH", sig)
        assert d is not None

    def test_long_no_exit_if_still_bullish(self):
        s = _make_strategy()
        sig = _make_signal(trend_1h="BULLISH", trend_4h="BULLISH")
        d = s._check_long_exit("ETH", sig)
        assert d is None


# -- Edge cases --


class TestEdgeCases:
    def test_price_none_returns_none(self):
        s = _make_strategy()
        sig = _make_signal(price=None)
        assert s._check_short_entry("ETH", sig) is None
        assert s._check_long_entry("ETH", sig) is None

    def test_change_4h_none_returns_none(self):
        s = _make_strategy()
        sig = _make_signal(change_4h=None)
        assert s._check_short_entry("ETH", sig) is None
        assert s._check_long_entry("ETH", sig) is None

    def test_volume_none_no_points(self):
        s = _make_strategy()
        sig = _make_signal(volume_ratio=None)
        d = s._check_short_entry("ETH", sig)
        assert d is not None
        # All except volume: 0.30+0.20+0.20+0+0.10+0.10 = 0.90
        assert d.confidence == 0.90

    def test_change_exactly_at_negative_threshold(self):
        """change_4h == -3.0 (<= -threshold) -> gets points for SHORT."""
        s = _make_strategy()
        sig = _make_signal(change_4h=-3.0)
        d = s._check_short_entry("ETH", sig)
        assert d is not None

    def test_change_just_above_negative_threshold(self):
        """change_4h == -2.99 (> -threshold) -> no change points."""
        s = _make_strategy()
        sig = _make_signal(change_4h=-2.99, trend_1h="BEARISH", trend_4h="BEARISH",
                           volume_ratio=1.5)
        d = s._check_short_entry("ETH", sig)
        assert d is not None
        # No change points: 0+0.20+0.20+0.10+0.10+0.10 = 0.70
        assert d.confidence == 0.70

    def test_funding_exactly_at_hard_block(self):
        """funding_rate == 0.001 -> hard block."""
        s = _make_strategy()
        s._funding_rates["ETH"] = 0.001
        sig = _make_signal()
        assert s._check_short_entry("ETH", sig) is None

    def test_custom_change_threshold(self):
        """Strategy with custom change threshold."""
        s = _make_strategy(change_threshold=5.0)
        sig = _make_signal(change_4h=-4.0, trend_1h="BEARISH", trend_4h="BEARISH",
                           volume_ratio=1.5)
        d = s._check_short_entry("ETH", sig)
        assert d is not None
        # change_4h=-4 > -5 threshold -> no change points
        # 0+0.20+0.20+0.10+0.10+0.10 = 0.70
        assert d.confidence == 0.70

    def test_strategy_type_is_trend_following(self):
        s = _make_strategy()
        sig = _make_signal()
        d = s._check_short_entry("ETH", sig)
        assert d is not None
        assert d.strategy_type == "trend_following"


# -- MultiStrategy conflict resolution --


class TestMultiStrategyConflictResolution:
    def test_conflict_highest_score_wins(self):
        """BUY vs SHORT on same coin → highest confidence wins."""
        decisions = [
            Decision(
                action="BUY", symbol="ETH", confidence=0.55,
                reasoning="[MeanRev LONG] ETH",
                strategy_type="mean_reversion", size_pct=10.0, order_type="MARKET",
            ),
            Decision(
                action="SHORT", symbol="ETH", confidence=0.85,
                reasoning="[TrendFollow SHORT] ETH",
                strategy_type="trend_following", size_pct=10.0, order_type="MARKET",
            ),
        ]
        ms = MultiStrategy([])
        result = MultiStrategy.merge(decisions)
        assert len(result) == 1
        assert result[0].action == "SHORT"
        assert result[0].confidence == 0.85
        assert "Conflict" in result[0].reasoning

    def test_conflict_buy_wins_over_short(self):
        """When BUY has higher score, it wins."""
        decisions = [
            Decision(
                action="BUY", symbol="SOL", confidence=0.80,
                reasoning="[MeanRev LONG] SOL",
                strategy_type="mean_reversion", size_pct=10.0, order_type="MARKET",
            ),
            Decision(
                action="SHORT", symbol="SOL", confidence=0.50,
                reasoning="[TrendFollow SHORT] SOL",
                strategy_type="trend_following", size_pct=10.0, order_type="MARKET",
            ),
        ]
        ms = MultiStrategy([])
        result = MultiStrategy.merge(decisions)
        assert len(result) == 1
        assert result[0].action == "BUY"
        assert result[0].confidence == 0.80
        assert "Conflict" in result[0].reasoning

    def test_no_conflict_agreement_boosted(self):
        """Same direction -> confidence boosted."""
        decisions = [
            Decision(
                action="SHORT", symbol="ETH", confidence=0.70,
                reasoning="[MeanRev SHORT] ETH",
                strategy_type="mean_reversion", size_pct=10.0, order_type="MARKET",
            ),
            Decision(
                action="SHORT", symbol="ETH", confidence=0.85,
                reasoning="[TrendFollow SHORT] ETH",
                strategy_type="trend_following", size_pct=10.0, order_type="MARKET",
            ),
        ]
        ms = MultiStrategy([])
        result = MultiStrategy.merge(decisions)
        assert len(result) == 1
        assert result[0].action == "SHORT"
        assert result[0].confidence == 0.95  # 0.85 + 0.10 boost

    def test_single_signal_no_change(self):
        """Only one strategy signals -> no boost, no conflict."""
        decisions = [
            Decision(
                action="SHORT", symbol="ETH", confidence=0.75,
                reasoning="[TrendFollow SHORT] ETH",
                strategy_type="trend_following", size_pct=10.0, order_type="MARKET",
            ),
        ]
        ms = MultiStrategy([])
        result = MultiStrategy.merge(decisions)
        assert len(result) == 1
        assert result[0].confidence == 0.75

    def test_conflict_preserves_strategy_type(self):
        """Winning decision keeps its strategy_type."""
        decisions = [
            Decision(
                action="BUY", symbol="ETH", confidence=0.55,
                reasoning="MR", strategy_type="mean_reversion",
            ),
            Decision(
                action="SHORT", symbol="ETH", confidence=0.85,
                reasoning="TF", strategy_type="trend_following",
            ),
        ]
        ms = MultiStrategy([])
        result = MultiStrategy.merge(decisions)
        assert len(result) == 1
        assert result[0].strategy_type == "trend_following"
