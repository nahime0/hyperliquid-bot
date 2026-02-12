"""Tests for risk.position_tracker.PositionTracker."""
from __future__ import annotations

import pytest
import pytest_asyncio

from risk.position_tracker import FEE_PER_LEG, PositionTracker


# ── Position lifecycle ──────────────────────────────────────


class TestOpenClose:
    @pytest.mark.asyncio
    async def test_open_position_long(self, position_tracker):
        pid = await position_tracker.open_position(
            symbol="ETH", entry_price=2000, quantity=0.5,
            stop_loss=1960, take_profit=2060, direction="LONG",
        )
        assert isinstance(pid, int)
        assert pid > 0
        pos = await position_tracker.get_position_for_symbol("ETH")
        assert pos is not None
        assert pos["direction"] == "LONG"
        assert pos["entry_price"] == 2000

    @pytest.mark.asyncio
    async def test_open_position_short(self, position_tracker):
        pid = await position_tracker.open_position(
            symbol="BTC", entry_price=60000, quantity=0.01,
            stop_loss=61200, take_profit=58800, direction="SHORT",
        )
        assert pid > 0
        pos = await position_tracker.get_position_for_symbol("BTC")
        assert pos["direction"] == "SHORT"

    @pytest.mark.asyncio
    async def test_open_position_duplicate_raises(self, position_tracker):
        await position_tracker.open_position(
            symbol="ETH", entry_price=2000, quantity=0.5, direction="LONG",
        )
        with pytest.raises(ValueError, match="already have open position"):
            await position_tracker.open_position(
                symbol="ETH", entry_price=2050, quantity=0.3, direction="LONG",
            )

    @pytest.mark.asyncio
    async def test_close_position_long_profit(self, position_tracker):
        pid = await position_tracker.open_position(
            symbol="ETH", entry_price=2000, quantity=1.0, direction="LONG",
        )
        pnl = await position_tracker.close_position(pid, exit_price=2100, reason="TP")
        # Gross: (2100-2000)*1 = 100, fee: (2000+2100)*1*0.00045 = 1.845
        expected_gross = 100.0
        expected_fee = (2000 + 2100) * 1.0 * FEE_PER_LEG
        assert pnl == pytest.approx(expected_gross - expected_fee, rel=1e-6)
        assert pnl > 0

    @pytest.mark.asyncio
    async def test_close_position_long_loss(self, position_tracker):
        pid = await position_tracker.open_position(
            symbol="ETH", entry_price=2000, quantity=1.0, direction="LONG",
        )
        pnl = await position_tracker.close_position(pid, exit_price=1900, reason="SL")
        assert pnl < 0

    @pytest.mark.asyncio
    async def test_close_position_short_profit(self, position_tracker):
        pid = await position_tracker.open_position(
            symbol="BTC", entry_price=60000, quantity=0.01, direction="SHORT",
        )
        pnl = await position_tracker.close_position(pid, exit_price=58000, reason="TP")
        # Gross: (60000-58000)*0.01 = 20
        assert pnl > 0

    @pytest.mark.asyncio
    async def test_close_position_short_loss(self, position_tracker):
        pid = await position_tracker.open_position(
            symbol="BTC", entry_price=60000, quantity=0.01, direction="SHORT",
        )
        pnl = await position_tracker.close_position(pid, exit_price=62000, reason="SL")
        assert pnl < 0

    @pytest.mark.asyncio
    async def test_close_position_includes_fees(self, position_tracker):
        pid = await position_tracker.open_position(
            symbol="ETH", entry_price=2000, quantity=1.0, direction="LONG",
        )
        # Close at exactly entry — gross PnL=0, but fees make net negative
        pnl = await position_tracker.close_position(pid, exit_price=2000, reason="flat")
        assert pnl < 0  # fees always eat into PnL

    @pytest.mark.asyncio
    async def test_close_nonexistent_returns_zero(self, position_tracker):
        pnl = await position_tracker.close_position(999, exit_price=2000)
        assert pnl == 0.0


# ── Queries ─────────────────────────────────────────────────


class TestQueries:
    @pytest.mark.asyncio
    async def test_get_open_positions(self, position_tracker):
        await position_tracker.open_position(
            symbol="ETH", entry_price=2000, quantity=0.5, direction="LONG",
        )
        await position_tracker.open_position(
            symbol="BTC", entry_price=60000, quantity=0.01, direction="SHORT",
        )
        positions = await position_tracker.get_open_positions()
        assert len(positions) == 2

    @pytest.mark.asyncio
    async def test_get_position_for_symbol(self, position_tracker):
        await position_tracker.open_position(
            symbol="ETH", entry_price=2000, quantity=0.5, direction="LONG",
        )
        pos = await position_tracker.get_position_for_symbol("ETH")
        assert pos is not None
        assert pos["symbol"] == "ETH"

    @pytest.mark.asyncio
    async def test_get_position_for_symbol_none(self, position_tracker):
        pos = await position_tracker.get_position_for_symbol("XYZ")
        assert pos is None

    @pytest.mark.asyncio
    async def test_get_open_symbols(self, position_tracker):
        await position_tracker.open_position(
            symbol="ETH", entry_price=2000, quantity=0.5, direction="LONG",
        )
        await position_tracker.open_position(
            symbol="SOL", entry_price=150, quantity=5.0, direction="SHORT",
        )
        symbols = await position_tracker.get_open_symbols()
        assert symbols == {"ETH", "SOL"}

    @pytest.mark.asyncio
    async def test_closed_position_not_in_open(self, position_tracker):
        pid = await position_tracker.open_position(
            symbol="ETH", entry_price=2000, quantity=0.5, direction="LONG",
        )
        await position_tracker.close_position(pid, exit_price=2100)
        positions = await position_tracker.get_open_positions()
        assert len(positions) == 0


# ── SL/TP checking ──────────────────────────────────────────


class TestSlTp:
    @pytest.mark.asyncio
    async def test_check_sl_tp_long_sl_hit(self, position_tracker):
        await position_tracker.open_position(
            symbol="ETH", entry_price=2000, quantity=0.5,
            stop_loss=1960, take_profit=2060, direction="LONG",
        )
        to_close = await position_tracker.check_sl_tp({"ETH": 1950})
        assert len(to_close) == 1
        assert to_close[0]["action"] == "CLOSE"
        assert "stop_loss" in to_close[0]["reason"]

    @pytest.mark.asyncio
    async def test_check_sl_tp_long_tp_hit(self, position_tracker):
        await position_tracker.open_position(
            symbol="ETH", entry_price=2000, quantity=0.5,
            stop_loss=1960, take_profit=2060, direction="LONG",
        )
        to_close = await position_tracker.check_sl_tp({"ETH": 2070})
        assert len(to_close) == 1
        assert "take_profit" in to_close[0]["reason"]

    @pytest.mark.asyncio
    async def test_check_sl_tp_short_sl_hit(self, position_tracker):
        await position_tracker.open_position(
            symbol="BTC", entry_price=60000, quantity=0.01,
            stop_loss=61200, take_profit=58800, direction="SHORT",
        )
        to_close = await position_tracker.check_sl_tp({"BTC": 61500})
        assert len(to_close) == 1
        assert "stop_loss" in to_close[0]["reason"]

    @pytest.mark.asyncio
    async def test_check_sl_tp_short_tp_hit(self, position_tracker):
        await position_tracker.open_position(
            symbol="BTC", entry_price=60000, quantity=0.01,
            stop_loss=61200, take_profit=58800, direction="SHORT",
        )
        to_close = await position_tracker.check_sl_tp({"BTC": 58000})
        assert len(to_close) == 1
        assert "take_profit" in to_close[0]["reason"]

    @pytest.mark.asyncio
    async def test_check_sl_tp_no_trigger(self, position_tracker):
        await position_tracker.open_position(
            symbol="ETH", entry_price=2000, quantity=0.5,
            stop_loss=1960, take_profit=2060, direction="LONG",
        )
        to_close = await position_tracker.check_sl_tp({"ETH": 2020})
        assert len(to_close) == 0

    @pytest.mark.asyncio
    async def test_check_sl_tp_missing_price_skipped(self, position_tracker):
        await position_tracker.open_position(
            symbol="ETH", entry_price=2000, quantity=0.5,
            stop_loss=1960, take_profit=2060, direction="LONG",
        )
        to_close = await position_tracker.check_sl_tp({"BTC": 60000})  # no ETH price
        assert len(to_close) == 0


# ── Trailing stop ───────────────────────────────────────────


class TestTrailingStop:
    @pytest.mark.asyncio
    async def test_trailing_long_breakeven(self, position_tracker, risk_config):
        """LONG gain >= breakeven_pct (1.0%) → SL moves to entry."""
        await position_tracker.open_position(
            symbol="ETH", entry_price=2000, quantity=0.5,
            stop_loss=1960, take_profit=2200, direction="LONG",
        )
        # Price up 1.2% → 2024
        to_close = await position_tracker.check_sl_tp({"ETH": 2024})
        assert len(to_close) == 0

        pos = await position_tracker.get_position_for_symbol("ETH")
        # Trailing SL should have moved to at least entry (2000)
        trailing = pos.get("trailing_sl") or pos.get("stop_loss")
        assert trailing >= 2000

    @pytest.mark.asyncio
    async def test_trailing_long_full(self, position_tracker, risk_config):
        """LONG gain >= trailing_start_pct (1.5%) → SL trails at max - distance%."""
        await position_tracker.open_position(
            symbol="ETH", entry_price=2000, quantity=0.5,
            stop_loss=1960, take_profit=2200, direction="LONG",
        )
        # Price up 1.8% → 2036
        await position_tracker.check_sl_tp({"ETH": 2036})
        pos = await position_tracker.get_position_for_symbol("ETH")
        trailing = pos.get("trailing_sl")
        # Expected: 2036 * (1 - 0.01) = 2015.64
        expected = 2036 * (1 - risk_config.trailing_distance_pct / 100)
        assert trailing == pytest.approx(expected, rel=1e-4)

    @pytest.mark.asyncio
    async def test_trailing_long_tight(self, position_tracker, risk_config):
        """LONG gain >= trailing_tight_pct (2.5%) → SL tightens."""
        await position_tracker.open_position(
            symbol="ETH", entry_price=2000, quantity=0.5,
            stop_loss=1960, take_profit=2200, direction="LONG",
        )
        # Price up 3% → 2060
        await position_tracker.check_sl_tp({"ETH": 2060})
        pos = await position_tracker.get_position_for_symbol("ETH")
        trailing = pos.get("trailing_sl")
        # Expected: 2060 * (1 - 0.0075) = 2044.55
        expected = 2060 * (1 - risk_config.trailing_tight_distance_pct / 100)
        assert trailing == pytest.approx(expected, rel=1e-4)

    @pytest.mark.asyncio
    async def test_trailing_short_breakeven(self, position_tracker, risk_config):
        """SHORT gain >= breakeven_pct (1.0%) → SL moves to entry."""
        await position_tracker.open_position(
            symbol="BTC", entry_price=60000, quantity=0.01,
            stop_loss=61200, take_profit=57000, direction="SHORT",
        )
        # Price drops 1.2% → 59280
        await position_tracker.check_sl_tp({"BTC": 59280})
        pos = await position_tracker.get_position_for_symbol("BTC")
        trailing = pos.get("trailing_sl") or pos.get("stop_loss")
        assert trailing <= 60000  # Should move to entry or below

    @pytest.mark.asyncio
    async def test_trailing_sl_only_moves_favorably_long(self, position_tracker):
        """LONG trailing SL never decreases."""
        await position_tracker.open_position(
            symbol="ETH", entry_price=2000, quantity=0.5,
            stop_loss=1960, take_profit=2200, direction="LONG",
        )
        # Push price up to trigger trailing
        await position_tracker.check_sl_tp({"ETH": 2040})
        pos1 = await position_tracker.get_position_for_symbol("ETH")
        sl1 = pos1.get("trailing_sl") or pos1.get("stop_loss")

        # Price drops back
        await position_tracker.check_sl_tp({"ETH": 2010})
        pos2 = await position_tracker.get_position_for_symbol("ETH")
        sl2 = pos2.get("trailing_sl") or pos2.get("stop_loss")

        assert sl2 >= sl1  # SL should never decrease for LONG

    @pytest.mark.asyncio
    async def test_trailing_sl_only_moves_favorably_short(self, position_tracker):
        """SHORT trailing SL never increases."""
        await position_tracker.open_position(
            symbol="BTC", entry_price=60000, quantity=0.01,
            stop_loss=61200, take_profit=57000, direction="SHORT",
        )
        # Push price down to trigger trailing
        await position_tracker.check_sl_tp({"BTC": 59000})
        pos1 = await position_tracker.get_position_for_symbol("BTC")
        sl1 = pos1.get("trailing_sl") or pos1.get("stop_loss")

        # Price bounces back up
        await position_tracker.check_sl_tp({"BTC": 59500})
        pos2 = await position_tracker.get_position_for_symbol("BTC")
        sl2 = pos2.get("trailing_sl") or pos2.get("stop_loss")

        assert sl2 <= sl1  # SL should never increase for SHORT


# ── AI adjustment methods ───────────────────────────────────


class TestAIAdjustments:
    @pytest.mark.asyncio
    async def test_update_sl_tp_both(self, position_tracker):
        pid = await position_tracker.open_position(
            symbol="ETH", entry_price=2000, quantity=0.5,
            stop_loss=1960, take_profit=2060, direction="LONG",
        )
        await position_tracker.update_sl_tp(pid, stop_loss=1950, take_profit=2100)
        pos = await position_tracker.get_position_for_symbol("ETH")
        assert pos["stop_loss"] == 1950
        assert pos["take_profit"] == 2100

    @pytest.mark.asyncio
    async def test_update_sl_tp_only_sl(self, position_tracker):
        pid = await position_tracker.open_position(
            symbol="ETH", entry_price=2000, quantity=0.5,
            stop_loss=1960, take_profit=2060, direction="LONG",
        )
        await position_tracker.update_sl_tp(pid, stop_loss=1940)
        pos = await position_tracker.get_position_for_symbol("ETH")
        assert pos["stop_loss"] == 1940
        assert pos["take_profit"] == 2060  # unchanged

    @pytest.mark.asyncio
    async def test_update_sl_tp_nothing(self, position_tracker):
        pid = await position_tracker.open_position(
            symbol="ETH", entry_price=2000, quantity=0.5,
            stop_loss=1960, take_profit=2060, direction="LONG",
        )
        await position_tracker.update_sl_tp(pid)  # no-op
        pos = await position_tracker.get_position_for_symbol("ETH")
        assert pos["stop_loss"] == 1960
        assert pos["take_profit"] == 2060

    @pytest.mark.asyncio
    async def test_update_leverage(self, position_tracker):
        pid = await position_tracker.open_position(
            symbol="ETH", entry_price=2000, quantity=0.5,
            direction="LONG", leverage=2,
        )
        await position_tracker.update_leverage(pid, leverage=3)
        pos = await position_tracker.get_position_for_symbol("ETH")
        assert pos["leverage"] == 3
