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
    def test_full_score_all_conditions_extreme(self):
        """Extreme RSI (0) + far below BB → score near 1.0."""
        s = _make_strategy()
        # RSI=0 → intensity=1.0, price far below BB → intensity=1.0
        sig = _make_signal(rsi=0.0, price=1980.0, bb_lower=2010.0,
                           volume_ratio=1.5, rsi_1h=45.0)
        d = s._check_long_entry("ETH", sig)
        assert d is not None
        assert d.action == "BUY"
        assert d.confidence >= 0.90

    def test_graduated_rsi_scores_higher_for_deeper_oversold(self):
        """RSI=10 scores higher than RSI=20 (graduated)."""
        s = _make_strategy()
        sig_deep = _make_signal(rsi=10.0, price=2100.0, bb_lower=2010.0,
                                volume_ratio=1.5, rsi_1h=45.0)
        sig_mild = _make_signal(rsi=20.0, price=2100.0, bb_lower=2010.0,
                                volume_ratio=1.5, rsi_1h=45.0)
        d_deep = s._check_long_entry("ETH", sig_deep)
        d_mild = s._check_long_entry("ETH", sig_mild)
        assert d_deep is not None
        assert d_mild is not None
        assert d_deep.confidence > d_mild.confidence

    def test_rsi_only_with_all_minors(self):
        """RSI oversold but price inside BB → has RSI graduated + minors."""
        s = _make_strategy()
        # price=2100 > bb_lower*1.005=2020.05 → BB component = 0
        sig = _make_signal(rsi=20.0, price=2100.0, bb_lower=2010.0,
                           volume_ratio=1.5, rsi_1h=45.0)
        d = s._check_long_entry("ETH", sig)
        assert d is not None
        # RSI graduated: (35-20)/35 ≈ 0.429 → W_RSI*0.429 + minors ≈ 0.61
        assert d.confidence >= SCORE_THRESHOLD

    def test_bb_only_with_all_minors(self):
        """Price below BB but RSI not oversold → BB graduated + minors."""
        s = _make_strategy()
        sig = _make_signal(rsi=40.0, price=2000.0, bb_lower=2010.0,
                           volume_ratio=1.5, rsi_1h=45.0)
        d = s._check_long_entry("ETH", sig)
        assert d is not None
        assert d.confidence >= SCORE_THRESHOLD

    def test_rsi_and_bb_with_cooldown_only(self):
        """Deep RSI + BB graduated + cooldown, some minors fail → still fires."""
        s = _make_strategy()
        s._funding_rates["ETH"] = 0.0006  # > max_funding_rate(0.0005) but < HARD(0.001)
        # RSI=5 → intensity=0.857, BB: price=1970 far below 2010 → intensity~1.0
        sig = _make_signal(rsi=5.0, price=1970.0, bb_lower=2010.0,
                           volume_ratio=1.5, rsi_1h=65.0)
        d = s._check_long_entry("ETH", sig)
        assert d is not None
        assert d.confidence >= SCORE_THRESHOLD

    def test_below_threshold_no_signal(self):
        """No directional signals, only minors → below threshold."""
        s = _make_strategy()
        sig = _make_signal(rsi=50.0, price=2100.0, bb_lower=2010.0,
                           volume_ratio=0.5, rsi_1h=65.0)
        d = s._check_long_entry("ETH", sig)
        assert d is None

    def test_marginal_rsi_low_score(self):
        """RSI=34 (barely oversold) → very low graduated RSI score."""
        s = _make_strategy()
        s._funding_rates["ETH"] = 0.0006
        sig = _make_signal(rsi=34.0, price=2100.0, bb_lower=2010.0,
                           volume_ratio=0.5, rsi_1h=65.0)
        d = s._check_long_entry("ETH", sig)
        # RSI intensity: (35-34)/35 ≈ 0.029, score ≈ 0.25*0.029 + 0 + 0 + 0 + 0 + 0.10 = ~0.11
        assert d is None  # below threshold

    def test_none_volume_no_points(self):
        """Missing volume data → 0 points for volume component."""
        s = _make_strategy()
        sig = _make_signal(rsi=10.0, price=1980.0, bb_lower=2010.0,
                           volume_ratio=None, rsi_1h=45.0)
        d = s._check_long_entry("ETH", sig)
        assert d is not None
        assert d.confidence >= SCORE_THRESHOLD

    def test_none_rsi_1h_no_points(self):
        """Missing 1h RSI data → 0 points for macro component."""
        s = _make_strategy()
        sig = _make_signal(rsi=10.0, price=1980.0, bb_lower=2010.0,
                           volume_ratio=1.5, rsi_1h=None)
        d = s._check_long_entry("ETH", sig)
        assert d is not None
        assert d.confidence >= SCORE_THRESHOLD

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
        """Helper: default signal suitable for SHORT entry (extreme for graduated scoring)."""
        defaults = dict(
            rsi=85.0, rsi_1h=55.0, price=2240.0,
            bb_lower=2000.0, bb_upper=2195.0, bb_mid=2100.0,
            bb_pct=0.95, volume_ratio=1.5, signal="OVERBOUGHT",
        )
        defaults.update(kwargs)
        return _make_signal(**defaults)

    def test_full_score_extreme(self):
        """Extreme RSI (95) + far above BB → high score."""
        s = _make_strategy()
        sig = self._short_signal(rsi=95.0, price=2240.0, bb_upper=2195.0)
        d = s._check_short_entry("ETH", sig)
        assert d is not None
        assert d.action == "SHORT"
        assert d.confidence >= 0.90

    def test_graduated_rsi_scores_higher_for_deeper_overbought(self):
        """RSI=90 scores higher than RSI=80."""
        s = _make_strategy()
        sig_deep = self._short_signal(rsi=90.0, price=2100.0, bb_upper=2200.0)
        sig_mild = self._short_signal(rsi=80.0, price=2100.0, bb_upper=2200.0)
        d_deep = s._check_short_entry("ETH", sig_deep)
        d_mild = s._check_short_entry("ETH", sig_mild)
        assert d_deep is not None
        assert d_mild is not None
        assert d_deep.confidence > d_mild.confidence

    def test_rsi_only_with_all_minors(self):
        """RSI overbought but price inside BB → graduated RSI + minors."""
        s = _make_strategy()
        sig = self._short_signal(price=2100.0, bb_upper=2200.0)
        d = s._check_short_entry("ETH", sig)
        assert d is not None
        assert d.confidence >= SCORE_THRESHOLD

    def test_bb_only_with_all_minors(self):
        """Price above BB but RSI not overbought → BB graduated + minors."""
        s = _make_strategy()
        sig = self._short_signal(rsi=60.0)  # RSI <= 65 → no RSI points
        d = s._check_short_entry("ETH", sig)
        assert d is not None
        assert d.confidence >= SCORE_THRESHOLD

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
        # Still fires because RSI + BB graduated + vol + funding + cd

    def test_bb_upper_none_returns_none(self):
        s = _make_strategy()
        sig = self._short_signal(bb_upper=None)
        assert s._check_short_entry("ETH", sig) is None

    def test_bullish_trend_blocks_short(self):
        """BULLISH trend hard-blocks SHORT entry regardless of score."""
        s = _make_strategy()
        sig = self._short_signal(trend="BULLISH")
        d = s._check_short_entry("ETH", sig)
        assert d is None

    def test_neutral_trend_allows_short(self):
        """NEUTRAL trend does NOT block SHORT entry."""
        s = _make_strategy()
        sig = self._short_signal(trend="NEUTRAL")
        d = s._check_short_entry("ETH", sig)
        assert d is not None
        assert d.action == "SHORT"

    def test_bearish_trend_allows_short(self):
        """BEARISH trend does NOT block SHORT entry (aligned)."""
        s = _make_strategy()
        sig = self._short_signal(trend="BEARISH")
        d = s._check_short_entry("ETH", sig)
        assert d is not None
        assert d.action == "SHORT"


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
        """Funding between max_funding_rate and HARD_FUNDING_RATE → no hard block, just loses funding points."""
        s = _make_strategy()
        s._funding_rates["ETH"] = 0.0007  # > 0.0005 (max_funding_rate) but < 0.001 (HARD)
        # Use extreme values to ensure signal fires with graduated scoring
        sig = _make_signal(rsi=5.0, price=1970.0, bb_lower=2010.0,
                           volume_ratio=1.5, rsi_1h=45.0)
        d_no_funding = s._check_long_entry("ETH", sig)
        assert d_no_funding is not None
        # Now compare with good funding
        s2 = _make_strategy()
        d_good_funding = s2._check_long_entry("ETH", sig)
        assert d_good_funding is not None
        # Funding OK adds W_FUNDING, so good funding should score higher
        assert d_good_funding.confidence > d_no_funding.confidence

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
        """Loss > 10 min ago → only loses cooldown points, not hard-blocked."""
        rc = RiskConfig()
        cd = CooldownTracker(rc)
        # Simulate old cooldown: 5 min remaining (= 25 min since loss on 1800s cd)
        cd._symbol_cooldowns["ETH"] = time.time() + 300
        s = _make_strategy(cooldown=cd, risk_config=rc)
        # Use extreme values for graduated scoring
        sig = _make_signal(rsi=5.0, price=1970.0, bb_lower=2010.0,
                           volume_ratio=1.5, rsi_1h=45.0)
        d_no_cd = s._check_long_entry("ETH", sig)
        assert d_no_cd is not None  # fires despite cooldown
        # Compare with no cooldown
        s2 = _make_strategy()
        d_with_cd = s2._check_long_entry("ETH", sig)
        assert d_with_cd is not None
        # With cooldown should score lower (missing W_COOLDOWN)
        assert d_with_cd.confidence > d_no_cd.confidence

    def test_no_funding_data_gives_points(self):
        """No funding data → funding_rate=0.0 → passes both hard block and scoring."""
        s = _make_strategy()
        # Use extreme values for graduated scoring
        sig = _make_signal(rsi=0.0, price=1970.0, bb_lower=2010.0,
                           volume_ratio=1.5, rsi_1h=45.0)
        d = s._check_long_entry("ETH", sig)
        assert d is not None
        assert d.confidence >= 0.90  # all conditions met with extreme values


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
        """volume_ratio == 1.0 → gets volume points."""
        s = _make_strategy()
        # Extreme RSI/BB + volume at threshold
        sig = _make_signal(rsi=0.0, price=1970.0, bb_lower=2010.0,
                           volume_ratio=1.0, rsi_1h=45.0)
        d = s._check_long_entry("ETH", sig)
        assert d is not None
        assert d.confidence >= 0.90

    def test_funding_exactly_at_max(self):
        """funding_rate == max_funding_rate (0.0005) → NOT < so no funding points."""
        s = _make_strategy()
        s._funding_rates["ETH"] = 0.0005
        sig = _make_signal(rsi=0.0, price=1970.0, bb_lower=2010.0,
                           volume_ratio=1.5, rsi_1h=45.0)
        d_no_funding = s._check_long_entry("ETH", sig)
        assert d_no_funding is not None
        # Compare with good funding
        s2 = _make_strategy()
        d_good = s2._check_long_entry("ETH", sig)
        assert d_good is not None
        assert d_good.confidence > d_no_funding.confidence  # funding adds W_FUNDING

    def test_funding_exactly_at_hard_block(self):
        """funding_rate == 0.001 → hard block."""
        s = _make_strategy()
        s._funding_rates["ETH"] = 0.001
        sig = _make_signal(rsi=5.0, price=1970.0, bb_lower=2010.0)
        assert s._check_long_entry("ETH", sig) is None

    def test_score_is_float(self):
        """Verify score is a float."""
        s = _make_strategy()
        sig = _make_signal(rsi=10.0, price=2100.0, bb_lower=2010.0,
                           volume_ratio=1.5, rsi_1h=45.0)
        d = s._check_long_entry("ETH", sig)
        assert d is not None
        assert isinstance(d.confidence, float)

    def test_bearish_trend_blocks_long(self):
        """BEARISH trend hard-blocks LONG entry regardless of score."""
        s = _make_strategy()
        sig = _make_signal(rsi=30.0, price=2000.0, bb_lower=2010.0,
                           volume_ratio=1.5, rsi_1h=45.0, trend="BEARISH")
        d = s._check_long_entry("ETH", sig)
        assert d is None

    def test_neutral_trend_allows_long(self):
        """NEUTRAL trend does NOT block LONG entry."""
        s = _make_strategy()
        sig = _make_signal(rsi=30.0, price=2000.0, bb_lower=2010.0,
                           volume_ratio=1.5, rsi_1h=45.0, trend="NEUTRAL")
        d = s._check_long_entry("ETH", sig)
        assert d is not None
        assert d.action == "BUY"

    def test_bullish_trend_allows_long(self):
        """BULLISH trend does NOT block LONG entry (aligned)."""
        s = _make_strategy()
        sig = _make_signal(rsi=30.0, price=2000.0, bb_lower=2010.0,
                           volume_ratio=1.5, rsi_1h=45.0, trend="BULLISH")
        d = s._check_long_entry("ETH", sig)
        assert d is not None
        assert d.action == "BUY"
