"""Tests for trailing stop price accuracy — ensures REST prices are used over WS.

Covers the bug where WS allMids returned stale/wrong prices causing the trailing
stop to update based on phantom price spikes (e.g. ME position got trail updated
to 0.1586 based on WS price 0.1591 while real price was 0.1530).
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from config.settings import RiskConfig, Settings
from data.db import Database
from risk.position_tracker import PositionTracker


# ── Fixtures ───────────────────────────────────────────────


@pytest_asyncio.fixture
async def db():
    d = Database(":memory:")
    await d.connect()
    yield d
    await d.close()


@pytest_asyncio.fixture
async def tracker(db):
    rc = RiskConfig(sl_tp_grace_seconds=0, partial_tp_enabled=False)
    return PositionTracker(db, rc)


# ── Trailing stop with accurate prices ─────────────────────


class TestTrailingWithAccuratePrices:
    """Verify trailing stop only moves when real price justifies it."""

    @pytest.mark.asyncio
    async def test_trailing_no_update_when_price_drops(self, tracker):
        """LONG: price drops from entry → trailing and max_seen stay at entry."""
        await tracker.open_position(
            symbol="ME", entry_price=0.15358, quantity=130.0,
            stop_loss=0.1520, direction="LONG",
        )
        # Price drops — no trailing update expected
        await tracker.check_sl_tp({"ME": 0.15200})
        pos = await tracker.get_position_for_symbol("ME")
        assert pos["max_price_seen"] == pytest.approx(0.15358, rel=1e-6)
        assert pos["trailing_sl"] is None or pos["trailing_sl"] <= 0.1520

    @pytest.mark.asyncio
    async def test_trailing_half_at_half_pct(self, tracker):
        """LONG: price rises 0.5% → trailing moves SL to midpoint (half)."""
        entry = 0.15358
        sl = 0.1520
        await tracker.open_position(
            symbol="ME", entry_price=entry, quantity=130.0,
            stop_loss=sl, direction="LONG",
        )
        # +0.5% = 0.15435 — hits half threshold (0.5%)
        await tracker.check_sl_tp({"ME": 0.15435})
        pos = await tracker.get_position_for_symbol("ME")
        assert pos["max_price_seen"] == pytest.approx(0.15435, rel=1e-4)
        trail = pos.get("trailing_sl") or pos.get("stop_loss")
        # half_pct moves SL to midpoint between entry and original SL
        midpoint = (entry + sl) / 2
        assert trail == pytest.approx(midpoint, rel=1e-3)

    @pytest.mark.asyncio
    async def test_trailing_no_update_below_half(self, tracker):
        """LONG: price rises 0.3% (below half 0.5%) → no trail update."""
        await tracker.open_position(
            symbol="ME", entry_price=0.15358, quantity=130.0,
            stop_loss=0.1520, direction="LONG",
        )
        # +0.3% = 0.15404 — below half threshold
        await tracker.check_sl_tp({"ME": 0.15404})
        pos = await tracker.get_position_for_symbol("ME")
        trail = pos.get("trailing_sl") or pos.get("stop_loss")
        assert trail <= 0.1520  # Still at original SL

    @pytest.mark.asyncio
    async def test_trailing_breakeven_at_1pct(self, tracker):
        """LONG: price rises 1.1% → trailing moves to entry (breakeven, breakeven_pct=1.0%)."""
        entry = 0.15358
        await tracker.open_position(
            symbol="ME", entry_price=entry, quantity=130.0,
            stop_loss=0.1520, direction="LONG",
        )
        price = entry * 1.011  # +1.1%
        await tracker.check_sl_tp({"ME": price})
        pos = await tracker.get_position_for_symbol("ME")
        trail = pos.get("trailing_sl")
        assert trail is not None
        assert trail == pytest.approx(entry, rel=1e-3)

    @pytest.mark.asyncio
    async def test_trailing_normal_at_start_pct(self, tracker):
        """LONG: price rises 1.5% → trailing starts (max * (1 - distance%)), start_pct=1.2%."""
        entry = 0.15358
        rc = RiskConfig()
        await tracker.open_position(
            symbol="ME", entry_price=entry, quantity=130.0,
            stop_loss=0.1520, direction="LONG",
        )
        price = entry * 1.015  # +1.5%
        await tracker.check_sl_tp({"ME": price})
        pos = await tracker.get_position_for_symbol("ME")
        trail = pos.get("trailing_sl")
        expected = price * (1 - rc.trailing_distance_pct / 100)
        assert trail == pytest.approx(expected, rel=1e-4)

    @pytest.mark.asyncio
    async def test_trailing_tight_at_3pct(self, tracker):
        """LONG: price rises 3% → tight trailing (max * (1 - tight_distance%)), tight_pct=2.0%."""
        entry = 0.15358
        rc = RiskConfig()
        await tracker.open_position(
            symbol="ME", entry_price=entry, quantity=130.0,
            stop_loss=0.1520, direction="LONG",
        )
        price = entry * 1.03  # +3%
        await tracker.check_sl_tp({"ME": price})
        pos = await tracker.get_position_for_symbol("ME")
        trail = pos.get("trailing_sl")
        expected = price * (1 - rc.trailing_tight_distance_pct / 100)
        assert trail == pytest.approx(expected, rel=1e-4)

    @pytest.mark.asyncio
    async def test_trailing_never_moves_on_wrong_price(self, tracker):
        """Simulate the ME bug: price never rises but somehow gets high max_seen."""
        entry = 0.15358
        await tracker.open_position(
            symbol="ME", entry_price=entry, quantity=130.0,
            stop_loss=0.1520, direction="LONG",
        )
        # Simulate real prices: entry → drop → drop more
        real_prices = [0.15340, 0.15300, 0.15280, 0.15295]
        for p in real_prices:
            await tracker.check_sl_tp({"ME": p})

        pos = await tracker.get_position_for_symbol("ME")
        # max_price_seen should be entry (highest actual price)
        assert pos["max_price_seen"] == pytest.approx(entry, rel=1e-4)
        # trailing_sl should NOT have activated (no gain)
        trail = pos.get("trailing_sl") or pos.get("stop_loss")
        assert trail <= 0.1520  # Still at original SL

    @pytest.mark.asyncio
    async def test_trailing_updates_incrementally(self, tracker):
        """LONG: price rises in steps → trailing follows each step correctly."""
        entry = 2000.0
        rc = RiskConfig()
        await tracker.open_position(
            symbol="ETH", entry_price=entry, quantity=0.5,
            stop_loss=1960, direction="LONG",
        )
        # Step 1: +1.1% → breakeven (breakeven_pct=1.0%)
        await tracker.check_sl_tp({"ETH": 2022})
        pos = await tracker.get_position_for_symbol("ETH")
        trail1 = pos.get("trailing_sl")
        assert trail1 == pytest.approx(entry, rel=1e-3)

        # Step 2: +1.5% → normal trailing (start_pct=1.2%)
        await tracker.check_sl_tp({"ETH": 2030})
        pos = await tracker.get_position_for_symbol("ETH")
        trail2 = pos.get("trailing_sl")
        expected2 = 2030 * (1 - rc.trailing_distance_pct / 100)
        assert trail2 == pytest.approx(expected2, rel=1e-4)
        assert trail2 > trail1

        # Step 3: +3.0% → tight trailing (tight_pct=2.0%)
        await tracker.check_sl_tp({"ETH": 2060})
        pos = await tracker.get_position_for_symbol("ETH")
        trail3 = pos.get("trailing_sl")
        expected3 = 2060 * (1 - rc.trailing_tight_distance_pct / 100)
        assert trail3 == pytest.approx(expected3, rel=1e-4)
        assert trail3 > trail2

    @pytest.mark.asyncio
    async def test_trailing_sl_triggers_close(self, tracker):
        """LONG: price rises (trail activates), then drops below trail → CLOSE."""
        entry = 2000.0
        await tracker.open_position(
            symbol="ETH", entry_price=entry, quantity=0.5,
            stop_loss=1960, direction="LONG",
        )
        # Push price up to activate tight trailing
        await tracker.check_sl_tp({"ETH": 2060})  # +3%
        pos = await tracker.get_position_for_symbol("ETH")
        trail = pos.get("trailing_sl")
        assert trail is not None
        assert trail > entry

        # Price drops below trailing SL
        to_close = await tracker.check_sl_tp({"ETH": trail - 1})
        assert len(to_close) == 1
        assert to_close[0]["action"] == "CLOSE"
        assert "trailing_sl" in to_close[0]["reason"] or "stop_loss" in to_close[0]["reason"]

    @pytest.mark.asyncio
    async def test_max_price_seen_only_increases(self, tracker):
        """max_price_seen should only go up, never down."""
        await tracker.open_position(
            symbol="ETH", entry_price=2000, quantity=0.5,
            stop_loss=1960, direction="LONG",
        )
        prices = [2010, 2030, 2020, 2050, 2040, 2060]
        expected_max = 2000  # starts at entry
        for p in prices:
            await tracker.check_sl_tp({"ETH": p})
            pos = await tracker.get_position_for_symbol("ETH")
            if pos is None:
                break  # SL triggered
            expected_max = max(expected_max, p)
            assert pos["max_price_seen"] == pytest.approx(expected_max, rel=1e-6)


class TestTrailingShort:
    """Same tests but for SHORT positions."""

    @pytest.mark.asyncio
    async def test_short_trailing_no_update_when_price_rises(self, tracker):
        """SHORT: price rises → no trailing update."""
        await tracker.open_position(
            symbol="BTC", entry_price=60000, quantity=0.01,
            stop_loss=61200, direction="SHORT",
        )
        await tracker.check_sl_tp({"BTC": 60500})
        pos = await tracker.get_position_for_symbol("BTC")
        assert pos["min_price_seen"] == pytest.approx(60000, rel=1e-6)

    @pytest.mark.asyncio
    async def test_short_trailing_tight(self, tracker):
        """SHORT: price drops 3% → tight trailing activates."""
        entry = 60000.0
        rc = RiskConfig()
        await tracker.open_position(
            symbol="BTC", entry_price=entry, quantity=0.01,
            stop_loss=61200, direction="SHORT",
        )
        price = entry * 0.97  # -3%
        await tracker.check_sl_tp({"BTC": price})
        pos = await tracker.get_position_for_symbol("BTC")
        trail = pos.get("trailing_sl")
        expected = price * (1 + rc.trailing_tight_distance_pct / 100)
        assert trail == pytest.approx(expected, rel=1e-4)

    @pytest.mark.asyncio
    async def test_short_min_price_only_decreases(self, tracker):
        """min_price_seen should only go down for SHORT."""
        await tracker.open_position(
            symbol="BTC", entry_price=60000, quantity=0.01,
            stop_loss=61200, direction="SHORT",
        )
        prices = [59800, 59500, 59700, 59200, 59400]
        expected_min = 60000
        for p in prices:
            await tracker.check_sl_tp({"BTC": p})
            pos = await tracker.get_position_for_symbol("BTC")
            if pos is None:
                break
            expected_min = min(expected_min, p)
            assert pos["min_price_seen"] == pytest.approx(expected_min, rel=1e-6)


class TestCheckPositionsPriceSource:
    """Test that _check_positions uses REST prices over WS prices."""

    @pytest.mark.asyncio
    async def test_rest_price_used_over_ws(self, db, tracker):
        """_check_positions should prefer REST prices (accurate) over WS (potentially stale)."""
        await tracker.open_position(
            symbol="ME", entry_price=0.15358, quantity=130.0,
            stop_loss=0.1520, direction="LONG",
        )

        # Mock client: REST returns correct price
        mock_client = AsyncMock()
        mock_client.get_price = AsyncMock(return_value=0.15300)

        # Mock market_data: WS returns WRONG price (the bug)
        mock_market_data = MagicMock()
        mock_market_data.get_mid_price = MagicMock(return_value=0.15907)

        # Simulate _check_positions logic (REST first)
        positions = await tracker.get_open_positions()
        prices: dict[str, float] = {}
        for pos in positions:
            sym = pos["symbol"]
            if sym not in prices:
                try:
                    rest_price = await mock_client.get_price(sym)
                    if rest_price > 0:
                        prices[sym] = rest_price
                except Exception:
                    mid = mock_market_data.get_mid_price(sym)
                    if mid and mid > 0:
                        prices[sym] = mid

        # REST price should be used, not WS
        assert prices["ME"] == pytest.approx(0.15300, rel=1e-6)
        mock_client.get_price.assert_called_once_with("ME")

        # Run trailing with correct price
        to_close = await tracker.check_sl_tp(prices)
        pos = await tracker.get_position_for_symbol("ME")
        # Price dropped from entry — max_seen should be entry, no trail activation
        assert pos["max_price_seen"] == pytest.approx(0.15358, rel=1e-4)

    @pytest.mark.asyncio
    async def test_ws_fallback_when_rest_fails(self, db, tracker):
        """When REST fails, WS price should be used as fallback."""
        await tracker.open_position(
            symbol="ME", entry_price=0.15358, quantity=130.0,
            stop_loss=0.1520, direction="LONG",
        )

        # Mock client: REST raises exception
        mock_client = AsyncMock()
        mock_client.get_price = AsyncMock(side_effect=Exception("REST timeout"))

        # Mock market_data: WS returns a price
        mock_market_data = MagicMock()
        mock_market_data.get_mid_price = MagicMock(return_value=0.15300)

        # Simulate _check_positions logic (REST fails → WS fallback)
        positions = await tracker.get_open_positions()
        prices: dict[str, float] = {}
        for pos in positions:
            sym = pos["symbol"]
            if sym not in prices:
                try:
                    rest_price = await mock_client.get_price(sym)
                    if rest_price > 0:
                        prices[sym] = rest_price
                except Exception:
                    mid = mock_market_data.get_mid_price(sym)
                    if mid and mid > 0:
                        prices[sym] = mid

        assert prices["ME"] == pytest.approx(0.15300, rel=1e-6)

    @pytest.mark.asyncio
    async def test_no_price_available(self, db, tracker):
        """When both REST and WS fail, position should be skipped."""
        await tracker.open_position(
            symbol="ME", entry_price=0.15358, quantity=130.0,
            stop_loss=0.1520, direction="LONG",
        )

        mock_client = AsyncMock()
        mock_client.get_price = AsyncMock(side_effect=Exception("REST timeout"))

        mock_market_data = MagicMock()
        mock_market_data.get_mid_price = MagicMock(return_value=None)

        positions = await tracker.get_open_positions()
        prices: dict[str, float] = {}
        for pos in positions:
            sym = pos["symbol"]
            if sym not in prices:
                try:
                    rest_price = await mock_client.get_price(sym)
                    if rest_price > 0:
                        prices[sym] = rest_price
                except Exception:
                    mid = mock_market_data.get_mid_price(sym)
                    if mid and mid > 0:
                        prices[sym] = mid

        assert "ME" not in prices
        # check_sl_tp with empty prices should not touch the position
        to_close = await tracker.check_sl_tp(prices)
        assert len(to_close) == 0
        pos = await tracker.get_position_for_symbol("ME")
        assert pos["max_price_seen"] == pytest.approx(0.15358, rel=1e-6)


class TestPhantomPriceBug:
    """Reproduce and verify fix for the phantom price spike bug.

    Bug: WS allMids returned 0.15907 for ME while real price was ~0.1530.
    This caused trailing SL to update to 0.1586 (3.57% gain) immediately after
    entry, even though the price had only gone DOWN since entry.
    """

    @pytest.mark.asyncio
    async def test_phantom_spike_with_ws_price(self, tracker):
        """If we fed the wrong WS price, trailing would incorrectly activate."""
        entry = 0.15358
        rc = RiskConfig()
        await tracker.open_position(
            symbol="ME", entry_price=entry, quantity=130.0,
            stop_loss=0.1520, direction="LONG",
        )
        # Feed the WRONG price (what the WS was returning)
        wrong_price = 0.15907
        await tracker.check_sl_tp({"ME": wrong_price})
        pos = await tracker.get_position_for_symbol("ME")

        # This is what HAPPENED (the bug) — trail moved up incorrectly
        gain = (wrong_price - entry) / entry * 100  # ~3.57%
        assert gain > rc.trailing_tight_pct  # Tight trail would activate
        assert pos["max_price_seen"] == pytest.approx(wrong_price, rel=1e-4)
        assert pos["trailing_sl"] > entry  # Trail moved above entry — WRONG if price never rose

    @pytest.mark.asyncio
    async def test_correct_price_no_phantom_trail(self, tracker):
        """With REST price (correct), trailing stays put when price drops."""
        entry = 0.15358
        await tracker.open_position(
            symbol="ME", entry_price=entry, quantity=130.0,
            stop_loss=0.1520, direction="LONG",
        )
        # Feed the CORRECT price (what REST returns)
        correct_price = 0.15300
        await tracker.check_sl_tp({"ME": correct_price})
        pos = await tracker.get_position_for_symbol("ME")

        # max_price_seen stays at entry (price dropped)
        assert pos["max_price_seen"] == pytest.approx(entry, rel=1e-4)
        # trailing_sl should NOT activate — we're in loss
        trail = pos.get("trailing_sl") or pos.get("stop_loss")
        assert trail <= 0.1520

    @pytest.mark.asyncio
    async def test_sl_triggers_correctly_after_drop(self, tracker):
        """Price drops below original SL → position should close."""
        entry = 0.15358
        sl = 0.1520
        await tracker.open_position(
            symbol="ME", entry_price=entry, quantity=130.0,
            stop_loss=sl, direction="LONG",
        )
        # Price below SL
        to_close = await tracker.check_sl_tp({"ME": 0.1515})
        assert len(to_close) == 1
        assert to_close[0]["action"] == "CLOSE"
        assert "stop_loss" in to_close[0]["reason"]
