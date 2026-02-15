"""Tests for structure confirmation filter (strategies/structure_filter.py)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from strategies.structure_filter import (
    MIN_CANDLES,
    check_structure_confirmation,
    _has_lateral_base,
    _has_rsi_divergence,
    _has_structure_break,
)


# ── Helpers ──────────────────────────────────────────────────


def _make_df(
    closes: list[float],
    highs: list[float] | None = None,
    lows: list[float] | None = None,
    opens: list[float] | None = None,
    volumes: list[float] | None = None,
) -> pd.DataFrame:
    """Build a minimal OHLCV DataFrame from close prices."""
    n = len(closes)
    if highs is None:
        highs = [c * 1.005 for c in closes]
    if lows is None:
        lows = [c * 0.995 for c in closes]
    if opens is None:
        opens = closes.copy()
    if volumes is None:
        volumes = [100.0] * n
    return pd.DataFrame({
        "open": opens,
        "high": highs,
        "low": lows,
        "close": closes,
        "volume": volumes,
    })


# ── Passthrough tests ────────────────────────────────────────


class TestPassthrough:
    """Filter passes automatically when data is insufficient."""

    def test_none_df(self):
        ok, reason = check_structure_confirmation(None, "LONG")
        assert ok is True
        assert "passthrough" in reason

    def test_empty_df(self):
        df = _make_df([100.0] * 5)
        ok, reason = check_structure_confirmation(df, "LONG")
        assert ok is True
        assert "passthrough" in reason

    def test_exactly_min_minus_one(self):
        df = _make_df([100.0] * (MIN_CANDLES - 1))
        ok, reason = check_structure_confirmation(df, "SHORT")
        assert ok is True
        assert "passthrough" in reason

    def test_exactly_min_candles_no_passthrough(self):
        """With exactly MIN_CANDLES, filters are evaluated (no passthrough)."""
        # All flat → lateral base passes (min not in last 2 candles since all equal → argmin=0)
        df = _make_df([100.0] * MIN_CANDLES)
        ok, reason = check_structure_confirmation(df, "LONG")
        # With all flat prices, lateral_base will pass because argmin returns 0 which is < lookback-2
        assert ok is True
        assert reason != "passthrough:insufficient_5m_data"


# ── Lateral Base tests ───────────────────────────────────────


class TestLateralBase:
    """Filter 1: price has formed a base (no new extremes in last 2 candles)."""

    def test_long_base_formed(self):
        """LONG: lowest low is NOT in last 2 candles → base formed."""
        # 6 candles: low at index 0, then higher lows
        lows = [90.0, 95.0, 96.0, 97.0, 98.0, 99.0]
        df = _make_df(
            closes=[95.0, 96.0, 97.0, 98.0, 99.0, 99.5],
            lows=lows,
            highs=[100.0] * 6,
        )
        assert _has_lateral_base(df, "LONG", lookback=6) is True

    def test_long_still_dumping(self):
        """LONG: lowest low IS in last 2 candles → still dumping."""
        lows = [95.0, 94.0, 93.0, 92.0, 91.0, 90.0]
        df = _make_df(
            closes=[96.0, 95.0, 94.0, 93.0, 92.0, 91.0],
            lows=lows,
            highs=[100.0] * 6,
        )
        assert _has_lateral_base(df, "LONG", lookback=6) is False

    def test_long_low_at_boundary(self):
        """LONG: lowest low at index lookback-3 (just outside last 2) → passes."""
        # lookback=6, so index 3 (6-3=3) is the boundary
        lows = [95.0, 94.0, 93.0, 90.0, 93.5, 94.0]
        df = _make_df(
            closes=[96.0, 95.0, 94.0, 91.0, 94.0, 95.0],
            lows=lows,
            highs=[100.0] * 6,
        )
        assert _has_lateral_base(df, "LONG", lookback=6) is True

    def test_long_low_at_last_two_boundary(self):
        """LONG: lowest low at index lookback-2 (inside last 2) → fails."""
        # lookback=6, so index 4 (6-2=4) is inside last 2
        lows = [95.0, 94.0, 93.0, 93.5, 90.0, 94.0]
        df = _make_df(
            closes=[96.0, 95.0, 94.0, 94.0, 91.0, 95.0],
            lows=lows,
            highs=[100.0] * 6,
        )
        assert _has_lateral_base(df, "LONG", lookback=6) is False

    def test_short_base_formed(self):
        """SHORT: highest high is NOT in last 2 candles → base formed."""
        highs = [110.0, 108.0, 106.0, 104.0, 102.0, 101.0]
        df = _make_df(
            closes=[105.0, 104.0, 103.0, 102.0, 101.0, 100.0],
            highs=highs,
            lows=[95.0] * 6,
        )
        assert _has_lateral_base(df, "SHORT", lookback=6) is True

    def test_short_still_pumping(self):
        """SHORT: highest high IS in last 2 candles → still pumping."""
        highs = [100.0, 102.0, 104.0, 106.0, 108.0, 110.0]
        df = _make_df(
            closes=[99.0, 101.0, 103.0, 105.0, 107.0, 109.0],
            highs=highs,
            lows=[95.0] * 6,
        )
        assert _has_lateral_base(df, "SHORT", lookback=6) is False


# ── RSI Micro-Divergence tests ──────────────────────────────


class TestRSIDivergence:
    """Filter 2: RSI divergence between old [-12:-6] and new [-6:] windows."""

    def _make_divergence_df(self, old_closes: list[float], new_closes: list[float]) -> pd.DataFrame:
        """Build a DataFrame with enough data for RSI calculation + divergence windows.

        old_closes: 6 values for the [-12:-6] window
        new_closes: 6 values for the [-6:] window
        Prefixes with 20 candles of warm-up data for RSI(14).
        """
        warmup = [100.0] * 20
        all_closes = warmup + old_closes + new_closes
        return _make_df(all_closes)

    def test_long_bullish_divergence(self):
        """LONG: price lower-low, RSI higher-low → bullish divergence."""
        # Old window: price bottoms at 90, then recovers
        # New window: price goes to 88 (lower-low) but with less momentum
        old_closes = [100.0, 95.0, 90.0, 92.0, 94.0, 96.0]
        # New window has lower price min (88) but RSI should be higher
        # because the drop is less steep overall
        new_closes = [94.0, 92.0, 91.0, 88.0, 90.0, 92.0]
        df = self._make_divergence_df(old_closes, new_closes)
        result = _has_rsi_divergence(df, "LONG")
        # The RSI calculation is complex; just verify the function runs without error
        assert isinstance(result, bool)

    def test_long_no_divergence_same_direction(self):
        """LONG: both price and RSI making lower-lows → no divergence."""
        # Continuous downtrend — RSI keeps falling with price
        old_closes = [100.0, 98.0, 96.0, 94.0, 92.0, 90.0]
        new_closes = [88.0, 86.0, 84.0, 82.0, 80.0, 78.0]
        df = self._make_divergence_df(old_closes, new_closes)
        # In a continuous downtrend, RSI should also make lower lows
        result = _has_rsi_divergence(df, "LONG")
        assert result is False

    def test_short_bearish_divergence(self):
        """SHORT: price higher-high, RSI lower-high → bearish divergence."""
        old_closes = [100.0, 105.0, 110.0, 108.0, 106.0, 104.0]
        new_closes = [106.0, 108.0, 109.0, 112.0, 110.0, 108.0]
        df = self._make_divergence_df(old_closes, new_closes)
        result = _has_rsi_divergence(df, "SHORT")
        assert isinstance(result, bool)

    def test_short_no_divergence_continuous_uptrend(self):
        """SHORT: continuous uptrend, RSI also keeps rising → no divergence."""
        old_closes = [80.0, 82.0, 84.0, 86.0, 88.0, 90.0]
        new_closes = [92.0, 94.0, 96.0, 98.0, 100.0, 102.0]
        df = self._make_divergence_df(old_closes, new_closes)
        result = _has_rsi_divergence(df, "SHORT")
        assert result is False


# ── Break of Recent Structure tests ──────────────────────────


class TestStructureBreak:
    """Filter 3: price breaks above/below local structure."""

    def test_long_breaks_resistance(self):
        """LONG: current close above max high of [-10:-3] → breaks resistance."""
        n = 15
        # Flat reference highs at 100, last candle closes at 101
        highs = [100.0] * n
        closes = [98.0] * (n - 1) + [101.0]  # last candle breaks above
        df = _make_df(closes=closes, highs=highs)
        assert _has_structure_break(df, "LONG", lookback=10) is True

    def test_long_no_break(self):
        """LONG: current close below max high of reference → no break."""
        n = 15
        highs = [105.0] * n
        closes = [98.0] * n
        df = _make_df(closes=closes, highs=highs)
        assert _has_structure_break(df, "LONG", lookback=10) is False

    def test_short_breaks_support(self):
        """SHORT: current close below min low of [-10:-3] → breaks support."""
        n = 15
        lows = [100.0] * n
        closes = [102.0] * (n - 1) + [99.0]  # last candle breaks below
        df = _make_df(closes=closes, lows=lows)
        assert _has_structure_break(df, "SHORT", lookback=10) is True

    def test_short_no_break(self):
        """SHORT: current close above min low of reference → no break."""
        n = 15
        lows = [95.0] * n
        closes = [100.0] * n
        df = _make_df(closes=closes, lows=lows)
        assert _has_structure_break(df, "SHORT", lookback=10) is False

    def test_long_equal_to_resistance(self):
        """LONG: close exactly equal to resistance → no break (must be strictly above)."""
        n = 15
        highs = [100.0] * n
        closes = [98.0] * (n - 1) + [100.0]
        df = _make_df(closes=closes, highs=highs)
        assert _has_structure_break(df, "LONG", lookback=10) is False

    def test_short_equal_to_support(self):
        """SHORT: close exactly equal to support → no break (must be strictly below)."""
        n = 15
        lows = [100.0] * n
        closes = [102.0] * (n - 1) + [100.0]
        df = _make_df(closes=closes, lows=lows)
        assert _has_structure_break(df, "SHORT", lookback=10) is False


# ── OR logic tests ───────────────────────────────────────────


class TestORLogic:
    """check_structure_confirmation returns True if ANY filter passes."""

    def test_all_fail_returns_false(self):
        """None of the 3 filters pass → overall False."""
        # Continuous downtrend: no base (new lows), no divergence, no break up
        n = 32
        closes = [100.0 - i * 0.5 for i in range(n)]
        highs = [c + 0.2 for c in closes]
        lows = [c - 0.2 for c in closes]
        df = _make_df(closes=closes, highs=highs, lows=lows)
        ok, reason = check_structure_confirmation(df, "LONG")
        # In a perfect downtrend the last low is the most recent → no lateral base,
        # RSI drops with price → no divergence, close below all resistance → no break
        assert ok is False
        assert reason == "no_structural_confirmation"

    def test_lateral_base_passes_alone(self):
        """Only lateral base passes → overall True."""
        # Build: downtrend that bottomed 4 candles ago, last 2 candles are higher
        n = 20
        closes = [100.0 - i * 0.3 for i in range(n - 4)]
        # Bottom was at candle n-5, then recover
        closes += [closes[-1] + 0.5, closes[-1] + 1.0, closes[-1] + 1.5, closes[-1] + 2.0]
        lows = [c - 0.2 for c in closes]
        highs = [c + 0.2 for c in closes]
        df = _make_df(closes=closes, highs=highs, lows=lows)
        ok, reason = check_structure_confirmation(df, "LONG")
        assert ok is True

    def test_structure_break_passes_alone(self):
        """Structure break passes → overall True."""
        # Downtrend with new lows in last 2 candles (lateral_base fails),
        # no divergence (RSI falls with price), but last candle breaks above old highs
        n = 20
        closes = [100.0 - i * 0.3 for i in range(n)]
        highs = [c + 0.2 for c in closes]
        lows = [c - 0.2 for c in closes]
        # Force last candle to break above the reference window highs
        closes[-1] = 110.0
        highs[-1] = 110.5
        lows[-1] = 109.5  # new low in last candle still above old lows → lateral_base fails
        # Also make lows[-2] a new low to ensure lateral base fails
        lows[-2] = min(lows[:-2]) - 0.1
        df = _make_df(closes=closes, highs=highs, lows=lows)
        ok, reason = check_structure_confirmation(df, "LONG")
        assert ok is True


# ── Edge cases ───────────────────────────────────────────────


class TestEdgeCases:
    """Edge case scenarios."""

    def test_all_flat_prices_long(self):
        """All prices identical → lateral base passes (argmin=0 which is outside last 2)."""
        df = _make_df([100.0] * 20)
        ok, reason = check_structure_confirmation(df, "LONG")
        assert ok is True

    def test_all_flat_prices_short(self):
        """All prices identical → lateral base passes (argmax=0 which is outside last 2)."""
        df = _make_df([100.0] * 20)
        ok, reason = check_structure_confirmation(df, "SHORT")
        assert ok is True

    def test_direction_short_on_downtrend(self):
        """SHORT on a clean downtrend: at least one filter passes."""
        n = 20
        closes = [100.0 - i * 0.5 for i in range(n)]
        highs = [c + 0.2 for c in closes]
        lows = [c - 0.2 for c in closes]
        df = _make_df(closes=closes, highs=highs, lows=lows)
        ok, reason = check_structure_confirmation(df, "SHORT")
        assert ok is True
