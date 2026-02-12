"""Tests for MeanReversionStrategy scoring-based entry system."""
from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from config.settings import RiskConfig
from strategies.cooldown import CooldownTracker
from strategies.mean_reversion import (
    MeanReversionStrategy,
    Signal,
    SCORE_THRESHOLD,
    W_RSI,
    W_BB,
    W_VOLUME,
    W_MACRO_RSI,
    W_FUNDING,
    W_COOLDOWN,
    HARD_FUNDING_RATE,
    HARD_COOLDOWN_FRESHNESS,
    RSI_OVERSOLD,
    RSI_OVERBOUGHT_ENTRY,
    RSI_1H_MAX,
    RSI_1H_MIN_SHORT,
    MIN_VOLUME_RATIO,
)


# ── Helpers ──────────────────────────────────────────────────


def _make_strategy(
    cooldown: CooldownTracker | None = None,
    risk_config: RiskConfig | None = None,
) -> MeanReversionStrategy:
    rc = risk_config or RiskConfig()
    cd = cooldown or CooldownTracker(rc)
    return MeanReversionStrategy(
        market_data=MagicMock(),
        trend_filter=MagicMock(),
        cooldown=cd,
        position_tracker=MagicMock(),
        risk_config=rc,
        coins=[],
    )


def _make_signal(
    symbol: str = "ETH",
    rsi: float | None = 30.0,
    rsi_1h: float | None = 45.0,
    price: float | None = 2000.0,
    bb_lower: float | None = 2010.0,
    bb_upper: float | None = 2200.0,
    bb_mid: float | None = 2100.0,
    bb_pct: float | None = 0.05,
    volume_ratio: float | None = 1.5,
    trend: str = "NEUTRAL",
    signal: str = "OVERSOLD",
    strength: float = 0.7,
) -> Signal:
    return Signal(
        symbol=symbol,
        signal=signal,
        strength=strength,
        rsi=rsi,
        rsi_1h=rsi_1h,
        price=price,
        bb_lower=bb_lower,
        bb_upper=bb_upper,
        bb_mid=bb_mid,
        bb_pct=bb_pct,
        volume_ratio=volume_ratio,
        trend=trend,
        updated_at=time.time(),
    )


# ── LONG entry scoring ──────────────────────────────────────


class TestLongEntryScoring:
    def test_full_score_all_conditions(self):
        """All 6 conditions met → score=1.0."""
        s = _make_strategy()
        sig = _make_signal(rsi=30.0, price=2000.0, bb_lower=2010.0,
                           volume_ratio=1.5, rsi_1h=45.0)
        d = s._check_long_entry("ETH", sig)
        assert d is not None
        assert d.action == "BUY"
        assert d.confidence == 1.0

    def test_rsi_only_with_all_minors(self):
        """RSI oversold but price inside BB → score=0.75."""
        s = _make_strategy()
        # price=2100 > bb_lower*1.005=2020.05 → BB component = 0
        sig = _make_signal(rsi=30.0, price=2100.0, bb_lower=2010.0,
                           volume_ratio=1.5, rsi_1h=45.0)
        d = s._check_long_entry("ETH", sig)
        assert d is not None
        assert d.confidence == W_RSI + W_VOLUME + W_MACRO_RSI + W_FUNDING + W_COOLDOWN

    def test_bb_only_with_all_minors(self):
        """Price below BB but RSI not oversold → score=0.75."""
        s = _make_strategy()
        sig = _make_signal(rsi=40.0, price=2000.0, bb_lower=2010.0,
                           volume_ratio=1.5, rsi_1h=45.0)
        d = s._check_long_entry("ETH", sig)
        assert d is not None
        assert d.confidence == W_BB + W_VOLUME + W_MACRO_RSI + W_FUNDING + W_COOLDOWN

    def test_rsi_and_bb_only(self):
        """RSI + BB but all minors fail → score=0.50, just passes."""
        s = _make_strategy()
        s._funding_rates["ETH"] = 0.0006  # > max_funding_rate(0.0005) but < HARD(0.001)
        sig = _make_signal(rsi=30.0, price=2000.0, bb_lower=2010.0,
                           volume_ratio=0.5, rsi_1h=65.0)
        # RSI(+0.25) + BB(+0.25) + vol_low(0) + rsi_1h_high(0) + funding_mid(0) + cd(+0.10) = 0.60
        d = s._check_long_entry("ETH", sig)
        assert d is not None
        assert d.confidence == 0.60

    def test_below_threshold_no_signal(self):
        """No directional signals, only minors → below threshold."""
        s = _make_strategy()
        sig = _make_signal(rsi=50.0, price=2100.0, bb_lower=2010.0,
                           volume_ratio=0.5, rsi_1h=65.0)
        # 0 + 0 + 0 + 0 + 0.10 + 0.10 = 0.20
        d = s._check_long_entry("ETH", sig)
        assert d is None

    def test_rsi_with_partial_minors(self):
        """RSI oversold + some minors but no BB → score=0.60."""
        s = _make_strategy()
        sig = _make_signal(rsi=30.0, price=2100.0, bb_lower=2010.0,
                           volume_ratio=0.5, rsi_1h=55.0)
        # RSI(+0.25) + BB(0) + vol_low(0) + RSI_1h(+0.15) + funding(+0.10) + cd(+0.10) = 0.60
        d = s._check_long_entry("ETH", sig)
        assert d is not None
        assert d.confidence == 0.60

    def test_none_volume_no_points(self):
        """Missing volume data → 0 points for volume component."""
        s = _make_strategy()
        sig = _make_signal(rsi=30.0, price=2000.0, bb_lower=2010.0,
                           volume_ratio=None, rsi_1h=45.0)
        d = s._check_long_entry("ETH", sig)
        assert d is not None
        # RSI + BB + macro + funding + cd = 0.85 (no volume)
        assert d.confidence == W_RSI + W_BB + W_MACRO_RSI + W_FUNDING + W_COOLDOWN

    def test_none_rsi_1h_no_points(self):
        """Missing 1h RSI data → 0 points for macro component."""
        s = _make_strategy()
        sig = _make_signal(rsi=30.0, price=2000.0, bb_lower=2010.0,
                           volume_ratio=1.5, rsi_1h=None)
        d = s._check_long_entry("ETH", sig)
        assert d is not None
        assert d.confidence == W_RSI + W_BB + W_VOLUME + W_FUNDING + W_COOLDOWN

    def test_confidence_equals_score(self):
        """Decision confidence equals the calculated score."""
        s = _make_strategy()
        sig = _make_signal(rsi=30.0, price=2100.0, bb_lower=2010.0,
                           volume_ratio=1.5, rsi_1h=55.0)
        d = s._check_long_entry("ETH", sig)
        assert d is not None
        expected = W_RSI + W_VOLUME + W_MACRO_RSI + W_FUNDING + W_COOLDOWN
        assert d.confidence == expected

    def test_rsi_none_returns_none(self):
        s = _make_strategy()
        sig = _make_signal(rsi=None)
        assert s._check_long_entry("ETH", sig) is None

    def test_price_none_returns_none(self):
        s = _make_strategy()
        sig = _make_signal(price=None)
        assert s._check_long_entry("ETH", sig) is None

    def test_bb_lower_none_returns_none(self):
        s = _make_strategy()
        sig = _make_signal(bb_lower=None)
        assert s._check_long_entry("ETH", sig) is None

    def test_reasoning_contains_score(self):
        """Reasoning string includes the calculated score."""
        s = _make_strategy()
        sig = _make_signal(rsi=30.0, price=2000.0, bb_lower=2010.0)
        d = s._check_long_entry("ETH", sig)
        assert d is not None
        assert "score=" in d.reasoning


# ── SHORT entry scoring ─────────────────────────────────────


class TestShortEntryScoring:
    def _short_signal(self, **kwargs) -> Signal:
        """Helper: default signal suitable for SHORT entry."""
        defaults = dict(
            rsi=70.0, rsi_1h=55.0, price=2200.0,
            bb_lower=2000.0, bb_upper=2195.0, bb_mid=2100.0,
            bb_pct=0.95, volume_ratio=1.5, signal="OVERBOUGHT",
        )
        defaults.update(kwargs)
        return _make_signal(**defaults)

    def test_full_score_all_conditions(self):
        s = _make_strategy()
        sig = self._short_signal()
        d = s._check_short_entry("ETH", sig)
        assert d is not None
        assert d.action == "SHORT"
        assert d.confidence == 1.0

    def test_rsi_only_with_all_minors(self):
        """RSI overbought but price inside BB → score=0.75."""
        s = _make_strategy()
        sig = self._short_signal(price=2100.0, bb_upper=2200.0)
        # price=2100 < bb_upper*0.995=2189 → BB = 0
        d = s._check_short_entry("ETH", sig)
        assert d is not None
        assert d.confidence == W_RSI + W_VOLUME + W_MACRO_RSI + W_FUNDING + W_COOLDOWN

    def test_bb_only_with_all_minors(self):
        """Price above BB but RSI not overbought → score=0.75."""
        s = _make_strategy()
        sig = self._short_signal(rsi=60.0)  # RSI <= 65 → no RSI points
        d = s._check_short_entry("ETH", sig)
        assert d is not None
        assert d.confidence == W_BB + W_VOLUME + W_MACRO_RSI + W_FUNDING + W_COOLDOWN

    def test_below_threshold_no_signal(self):
        s = _make_strategy()
        sig = self._short_signal(rsi=50.0, price=2100.0, volume_ratio=0.5, rsi_1h=35.0)
        d = s._check_short_entry("ETH", sig)
        assert d is None

    def test_rsi_1h_below_floor_no_macro_points(self):
        """RSI 1h <= 40 → no macro alignment points for SHORT."""
        s = _make_strategy()
        sig = self._short_signal(rsi_1h=40.0)  # <= RSI_1H_MIN_SHORT
        d = s._check_short_entry("ETH", sig)
        assert d is not None
        assert d.confidence == W_RSI + W_BB + W_VOLUME + W_FUNDING + W_COOLDOWN

    def test_bb_upper_none_returns_none(self):
        s = _make_strategy()
        sig = self._short_signal(bb_upper=None)
        assert s._check_short_entry("ETH", sig) is None


# ── Hard blocks ──────────────────────────────────────────────


class TestHardBlocks:
    def test_extreme_funding_blocks_long(self):
        s = _make_strategy()
        s._funding_rates["ETH"] = 0.0015  # > HARD_FUNDING_RATE
        sig = _make_signal(rsi=30.0, price=2000.0, bb_lower=2010.0)
        assert s._check_long_entry("ETH", sig) is None

    def test_extreme_funding_blocks_short(self):
        s = _make_strategy()
        s._funding_rates["ETH"] = -0.0015
        sig = _make_signal(rsi=70.0, price=2200.0, bb_upper=2195.0,
                           bb_pct=0.95, signal="OVERBOUGHT")
        assert s._check_short_entry("ETH", sig) is None

    def test_moderate_funding_soft_penalty(self):
        """Funding between max_funding_rate and HARD_FUNDING_RATE → no hard block, just -0.10."""
        s = _make_strategy()
        s._funding_rates["ETH"] = 0.0007  # > 0.0005 (max_funding_rate) but < 0.001 (HARD)
        sig = _make_signal(rsi=30.0, price=2000.0, bb_lower=2010.0,
                           volume_ratio=1.5, rsi_1h=45.0)
        d = s._check_long_entry("ETH", sig)
        assert d is not None
        # All conditions except funding: 0.25+0.25+0.15+0.15+0+0.10 = 0.90
        assert d.confidence == 0.90

    def test_global_cooldown_blocks_long(self):
        rc = RiskConfig()
        cd = CooldownTracker(rc)
        cd._global_cooldown_until = time.time() + 300
        s = _make_strategy(cooldown=cd, risk_config=rc)
        sig = _make_signal(rsi=30.0, price=2000.0, bb_lower=2010.0)
        assert s._check_long_entry("ETH", sig) is None

    def test_global_cooldown_blocks_short(self):
        rc = RiskConfig()
        cd = CooldownTracker(rc)
        cd._global_cooldown_until = time.time() + 300
        s = _make_strategy(cooldown=cd, risk_config=rc)
        sig = _make_signal(rsi=70.0, price=2200.0, bb_upper=2195.0,
                           bb_pct=0.95, signal="OVERBOUGHT")
        assert s._check_short_entry("ETH", sig) is None

    def test_fresh_cooldown_blocks(self):
        """Loss < 10 min ago → hard block."""
        rc = RiskConfig()
        cd = CooldownTracker(rc)
        cd.record_trade_result("ETH", is_win=False)  # just happened
        s = _make_strategy(cooldown=cd, risk_config=rc)
        sig = _make_signal(rsi=30.0, price=2000.0, bb_lower=2010.0)
        assert s._check_long_entry("ETH", sig) is None

    def test_stale_cooldown_soft_penalty(self):
        """Loss > 10 min ago → only loses 0.10, not hard-blocked."""
        rc = RiskConfig()
        cd = CooldownTracker(rc)
        # Simulate old cooldown: 5 min remaining (= 25 min since loss on 1800s cd)
        cd._symbol_cooldowns["ETH"] = time.time() + 300
        s = _make_strategy(cooldown=cd, risk_config=rc)
        sig = _make_signal(rsi=30.0, price=2000.0, bb_lower=2010.0,
                           volume_ratio=1.5, rsi_1h=45.0)
        d = s._check_long_entry("ETH", sig)
        assert d is not None
        # All except cooldown: 0.25+0.25+0.15+0.15+0.10 = 0.90
        assert d.confidence == 0.90

    def test_no_funding_data_gives_points(self):
        """No funding data → funding_rate=0.0 → passes both hard block and scoring."""
        s = _make_strategy()
        # No funding rate set for ETH → defaults to 0.0
        sig = _make_signal(rsi=30.0, price=2000.0, bb_lower=2010.0,
                           volume_ratio=1.5, rsi_1h=45.0)
        d = s._check_long_entry("ETH", sig)
        assert d is not None
        assert d.confidence == 1.0


# ── Classify (OR logic) ─────────────────────────────────────


class TestClassify:
    def test_both_rsi_and_bb_oversold(self):
        sig, strength = MeanReversionStrategy._classify(
            rsi=30.0, price=2000.0, bb_lower=2010.0, bb_upper=2200.0, bb_pct=0.05,
        )
        assert sig == "OVERSOLD"
        assert strength > 0

    def test_rsi_only_oversold(self):
        """RSI oversold but price inside BB → still OVERSOLD."""
        sig, strength = MeanReversionStrategy._classify(
            rsi=30.0, price=2100.0, bb_lower=2000.0, bb_upper=2200.0, bb_pct=0.5,
        )
        assert sig == "OVERSOLD"

    def test_bb_only_oversold(self):
        """Price below BB but RSI neutral → still OVERSOLD."""
        sig, strength = MeanReversionStrategy._classify(
            rsi=40.0, price=2000.0, bb_lower=2005.0, bb_upper=2200.0, bb_pct=0.03,
        )
        assert sig == "OVERSOLD"

    def test_both_rsi_and_bb_overbought(self):
        sig, strength = MeanReversionStrategy._classify(
            rsi=70.0, price=2200.0, bb_lower=2000.0, bb_upper=2195.0, bb_pct=0.95,
        )
        assert sig == "OVERBOUGHT"
        assert strength > 0

    def test_rsi_only_overbought(self):
        sig, strength = MeanReversionStrategy._classify(
            rsi=70.0, price=2100.0, bb_lower=2000.0, bb_upper=2200.0, bb_pct=0.5,
        )
        assert sig == "OVERBOUGHT"

    def test_bb_only_overbought(self):
        sig, strength = MeanReversionStrategy._classify(
            rsi=60.0, price=2200.0, bb_lower=2000.0, bb_upper=2195.0, bb_pct=0.98,
        )
        assert sig == "OVERBOUGHT"

    def test_neutral(self):
        sig, strength = MeanReversionStrategy._classify(
            rsi=50.0, price=2100.0, bb_lower=2000.0, bb_upper=2200.0, bb_pct=0.5,
        )
        assert sig == "NEUTRAL"
        assert strength == 0.0

    def test_rsi_none(self):
        sig, strength = MeanReversionStrategy._classify(
            rsi=None, price=2000.0, bb_lower=2010.0, bb_upper=2200.0, bb_pct=0.05,
        )
        assert sig == "NEUTRAL"


# ── Edge cases ───────────────────────────────────────────────


class TestEdgeCases:
    def test_volume_ratio_exactly_threshold(self):
        """volume_ratio == 1.0 → gets points."""
        s = _make_strategy()
        sig = _make_signal(rsi=30.0, price=2000.0, bb_lower=2010.0,
                           volume_ratio=1.0, rsi_1h=45.0)
        d = s._check_long_entry("ETH", sig)
        assert d is not None
        assert d.confidence == 1.0

    def test_funding_exactly_at_max(self):
        """funding_rate == max_funding_rate (0.0005) → NOT < so no points."""
        s = _make_strategy()
        s._funding_rates["ETH"] = 0.0005
        sig = _make_signal(rsi=30.0, price=2000.0, bb_lower=2010.0,
                           volume_ratio=1.5, rsi_1h=45.0)
        d = s._check_long_entry("ETH", sig)
        assert d is not None
        assert d.confidence == 0.90  # all except funding

    def test_funding_exactly_at_hard_block(self):
        """funding_rate == 0.001 → hard block."""
        s = _make_strategy()
        s._funding_rates["ETH"] = 0.001
        sig = _make_signal(rsi=30.0, price=2000.0, bb_lower=2010.0)
        assert s._check_long_entry("ETH", sig) is None

    def test_score_rounding(self):
        """Verify score is rounded to 2 decimals."""
        s = _make_strategy()
        sig = _make_signal(rsi=30.0, price=2100.0, bb_lower=2010.0,
                           volume_ratio=1.5, rsi_1h=None)
        d = s._check_long_entry("ETH", sig)
        assert d is not None
        # RSI(0.25) + vol(0.15) + funding(0.10) + cd(0.10) = 0.60
        assert d.confidence == 0.60
        assert isinstance(d.confidence, float)
