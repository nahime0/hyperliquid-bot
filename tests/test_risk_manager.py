"""Tests for risk.risk_manager.RiskManager."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pandas as pd
import pytest
import pytest_asyncio

from config.settings import MarketConfig, RiskConfig
from core.types import Decision
from data.db import Database
from risk.position_tracker import PositionTracker
from risk.risk_manager import RiskManager


# ── Mock client ─────────────────────────────────────────────


class MockClient:
    """Lightweight mock for HyperliquidClient."""

    def __init__(self, balance: float = 1000.0, positions: list | None = None, l2_snapshot: dict | None = None):
        self._balance = balance
        self._positions = positions or []
        self._l2_snapshot = l2_snapshot

    async def get_account_balance(self) -> float:
        return self._balance

    async def get_open_positions(self) -> list:
        return self._positions

    async def get_price(self, coin: str) -> float:
        return 2000.0

    async def update_leverage(self, coin, lev, is_cross=True):
        pass

    async def get_l2_snapshot(self, coin: str) -> dict:
        if self._l2_snapshot is not None:
            return self._l2_snapshot
        # Default: tight spread (0.05%)
        return {"levels": [
            [{"px": "1999.5", "sz": "10", "n": 5}],
            [{"px": "2000.5", "sz": "10", "n": 5}],
        ]}


# ── Fixtures ────────────────────────────────────────────────


@pytest_asyncio.fixture
async def risk_env(db, risk_config):
    """Set up RiskManager with mocked client and real DB/PositionTracker."""
    client = MockClient(balance=1000.0)
    pt = PositionTracker(db, risk_config)
    rm = RiskManager(risk_config, client, db, position_tracker=pt)
    # Manually set state (skip async start() which calls HL)
    rm._current_balance = 1000.0
    rm._peak_balance = 1000.0
    rm._open_position_count = 0
    return rm, pt, client


# ── Validation tests ────────────────────────────────────────


class TestValidation:
    @pytest.mark.asyncio
    async def test_validate_hold_always_approved(self, risk_env):
        rm, pt, _ = risk_env
        d = Decision(action="HOLD", confidence=0.5, reasoning="wait")
        result = await rm.validate_decision(d)
        assert result.approved is True

    @pytest.mark.asyncio
    async def test_validate_buy_approved(self, risk_env):
        rm, pt, _ = risk_env
        d = Decision(
            action="BUY", confidence=0.75, reasoning="signal",
            symbol="ETH", stop_loss=1960, take_profit=2060,
        )
        result = await rm.validate_decision(d)
        assert result.approved is True
        assert result.size is not None
        assert result.size.size_usdc > 0

    @pytest.mark.asyncio
    async def test_validate_kill_switch_blocks(self, risk_env):
        rm, pt, _ = risk_env
        rm._kill_switch = True
        rm._kill_reason = "test"
        d = Decision(action="BUY", confidence=0.8, reasoning="sig", symbol="ETH")
        result = await rm.validate_decision(d)
        assert result.approved is False
        assert "KILL SWITCH" in result.reason

    @pytest.mark.asyncio
    async def test_validate_daily_pause_blocks_entries(self, risk_env):
        rm, pt, _ = risk_env
        rm._daily_paused = True
        rm._daily_pause_reason = "daily dd"

        # BUY blocked
        d_buy = Decision(action="BUY", confidence=0.8, reasoning="sig", symbol="ETH")
        result = await rm.validate_decision(d_buy)
        assert result.approved is False
        assert "DAILY PAUSE" in result.reason

        # SHORT blocked
        d_short = Decision(action="SHORT", confidence=0.8, reasoning="sig", symbol="BTC")
        result = await rm.validate_decision(d_short)
        assert result.approved is False

        # CLOSE allowed (even during daily pause)
        d_close = Decision(action="CLOSE", confidence=0.8, reasoning="exit", symbol="ETH")
        result = await rm.validate_decision(d_close)
        assert result.approved is True
        assert result.reason == "approved_close"

    @pytest.mark.asyncio
    async def test_validate_max_positions_blocks(self, risk_env):
        rm, pt, _ = risk_env
        # With 1000 USDC / 25 per slot = 40, capped at max_open_positions=15
        rm._open_position_count = 15
        d = Decision(action="BUY", confidence=0.8, reasoning="sig", symbol="ETH")
        result = await rm.validate_decision(d)
        assert result.approved is False
        assert "Max open positions" in result.reason

    @pytest.mark.asyncio
    async def test_validate_min_balance_blocks(self, risk_env):
        rm, pt, _ = risk_env
        rm._current_balance = 30.0  # below 50
        d = Decision(action="BUY", confidence=0.8, reasoning="sig", symbol="ETH")
        result = await rm.validate_decision(d)
        assert result.approved is False
        assert "below minimum" in result.reason

    @pytest.mark.asyncio
    async def test_validate_low_confidence_blocks(self, risk_env):
        rm, pt, _ = risk_env
        d = Decision(action="BUY", confidence=0.3, reasoning="weak", symbol="ETH")
        result = await rm.validate_decision(d)
        assert result.approved is False
        assert "below minimum" in result.reason

    @pytest.mark.asyncio
    async def test_validate_blacklisted_coin_blocks(self, db, risk_config):
        """Blacklisted coins are blocked for BUY/SHORT/SCALE_UP/FLIP."""
        mc = MarketConfig(coin_blacklist=("AXS", "MOODENG"))
        client = MockClient(balance=1000.0)
        pt = PositionTracker(db, risk_config)
        rm = RiskManager(risk_config, client, db, position_tracker=pt, market_config=mc)
        rm._current_balance = 1000.0
        rm._peak_balance = 1000.0
        rm._open_position_count = 0

        for action in ("BUY", "SHORT"):
            d = Decision(action=action, confidence=0.8, reasoning="sig", symbol="AXS")
            result = await rm.validate_decision(d)
            assert result.approved is False
            assert "Blacklisted" in result.reason

        # Non-blacklisted coin is fine
        d = Decision(action="BUY", confidence=0.8, reasoning="sig", symbol="ETH",
                     stop_loss=1960, take_profit=2060)
        result = await rm.validate_decision(d)
        assert result.approved is True

    @pytest.mark.asyncio
    async def test_validate_custom_min_confidence(self, db, risk_config):
        """min_confidence from config is respected (not hardcoded 0.5)."""
        client = MockClient(balance=1000.0)
        pt = PositionTracker(db, risk_config)
        rm = RiskManager(risk_config, client, db, position_tracker=pt, ai_min_confidence=0.70)
        rm._current_balance = 1000.0
        rm._peak_balance = 1000.0
        rm._open_position_count = 0
        # 0.6 passes default (0.5) but fails custom (0.70)
        d = Decision(action="BUY", confidence=0.6, reasoning="sig", symbol="ETH")
        result = await rm.validate_decision(d)
        assert result.approved is False
        assert "below minimum 0.7" in result.reason

    @pytest.mark.asyncio
    async def test_validate_duplicate_position_blocks(self, risk_env):
        rm, pt, _ = risk_env
        await pt.open_position(
            symbol="ETH", entry_price=2000, quantity=0.5, direction="LONG",
        )
        rm._open_position_count = 1
        d = Decision(action="BUY", confidence=0.8, reasoning="sig", symbol="ETH")
        result = await rm.validate_decision(d)
        assert result.approved is False
        assert "Already holding" in result.reason

    @pytest.mark.asyncio
    async def test_validate_auto_sl_long(self, risk_env):
        rm, pt, _ = risk_env
        d = Decision(
            action="BUY", confidence=0.75, reasoning="signal",
            symbol="ETH",  # no SL/TP set
        )
        result = await rm.validate_decision(d)
        assert result.approved is True
        # Auto SL always applied, TP not applied (auto_take_profit=False by default)
        assert result.decision.stop_loss is not None
        assert result.decision.stop_loss < 2000  # mock price
        assert result.decision.take_profit is None  # trailing stop handles profit-taking

    @pytest.mark.asyncio
    async def test_validate_auto_sl_short(self, risk_env):
        rm, pt, _ = risk_env
        d = Decision(
            action="SHORT", confidence=0.75, reasoning="signal",
            symbol="BTC",  # no SL/TP set
        )
        result = await rm.validate_decision(d)
        assert result.approved is True
        # SHORT: SL above price, no auto-TP
        assert result.decision.stop_loss > 2000  # mock price
        assert result.decision.take_profit is None

    @pytest.mark.asyncio
    async def test_validate_auto_sl_tp_long_when_enabled(self, db):
        """With auto_take_profit=True, both SL and TP are generated."""
        rc = RiskConfig(auto_take_profit=True, min_rr_ratio=0)
        client = MockClient(balance=1000.0)
        pt = PositionTracker(db, rc)
        rm = RiskManager(rc, client, db, position_tracker=pt)
        rm._current_balance = 1000.0
        rm._peak_balance = 1000.0
        rm._open_position_count = 0

        d = Decision(action="BUY", confidence=0.75, reasoning="signal", symbol="ETH")
        result = await rm.validate_decision(d)
        assert result.approved is True
        assert result.decision.stop_loss < 2000
        assert result.decision.take_profit is not None
        assert result.decision.take_profit > 2000

    @pytest.mark.asyncio
    async def test_validate_auto_sl_tp_short_when_enabled(self, db):
        """With auto_take_profit=True, SHORT gets SL above and TP below."""
        rc = RiskConfig(auto_take_profit=True, min_rr_ratio=0)
        client = MockClient(balance=1000.0)
        pt = PositionTracker(db, rc)
        rm = RiskManager(rc, client, db, position_tracker=pt)
        rm._current_balance = 1000.0
        rm._peak_balance = 1000.0
        rm._open_position_count = 0

        d = Decision(action="SHORT", confidence=0.75, reasoning="signal", symbol="BTC")
        result = await rm.validate_decision(d)
        assert result.approved is True
        assert result.decision.stop_loss > 2000
        assert result.decision.take_profit is not None
        assert result.decision.take_profit < 2000

    @pytest.mark.asyncio
    async def test_validate_close_approved_no_position(self, risk_env):
        """CLOSE is approved even without a position (holding period skips)."""
        rm, pt, _ = risk_env
        d = Decision(action="CLOSE", confidence=0.8, reasoning="exit", symbol="ETH")
        result = await rm.validate_decision(d)
        assert result.approved is True
        assert result.reason == "approved_close"

    @pytest.mark.asyncio
    async def test_rr_gate_blocks_bad_rr(self, db):
        """R:R gate blocks entry when reward/risk < min_rr_ratio."""
        rc = RiskConfig(min_rr_ratio=1.5, auto_take_profit=True, take_profit_pct=0.5, stop_loss_pct=1.5)
        client = MockClient(balance=1000.0)
        pt = PositionTracker(db, rc)
        rm = RiskManager(rc, client, db, position_tracker=pt)
        rm._current_balance = 1000.0
        rm._peak_balance = 1000.0
        rm._open_position_count = 0

        # TP=0.5% risk=1.5% → R:R = 0.33 < 1.5
        d = Decision(action="BUY", confidence=0.75, reasoning="signal", symbol="ETH")
        result = await rm.validate_decision(d)
        assert result.approved is False
        assert "R:R" in result.reason

    @pytest.mark.asyncio
    async def test_rr_gate_passes_good_rr(self, db):
        """R:R gate allows entry when reward/risk >= min_rr_ratio."""
        rc = RiskConfig(min_rr_ratio=1.5, auto_take_profit=True, take_profit_pct=3.0, stop_loss_pct=1.5)
        client = MockClient(balance=1000.0)
        pt = PositionTracker(db, rc)
        rm = RiskManager(rc, client, db, position_tracker=pt)
        rm._current_balance = 1000.0
        rm._peak_balance = 1000.0
        rm._open_position_count = 0

        # TP=3% risk=1.5% → R:R = 2.0 >= 1.5
        d = Decision(action="BUY", confidence=0.75, reasoning="signal", symbol="ETH")
        result = await rm.validate_decision(d)
        assert result.approved is True

    @pytest.mark.asyncio
    async def test_rr_gate_skipped_without_tp(self, risk_env):
        """R:R gate is skipped when no TP is set (trailing mode)."""
        rm, pt, _ = risk_env
        d = Decision(action="BUY", confidence=0.75, reasoning="signal", symbol="ETH")
        result = await rm.validate_decision(d)
        assert result.approved is True  # no TP → gate skipped

    @pytest.mark.asyncio
    async def test_validate_flip_in_loss_approved(self, risk_env):
        """FLIP on a losing position is approved."""
        rm, pt, _ = risk_env
        await pt.open_position("ETH", 2100, 0.5, direction="LONG")  # entry 2100, mock price 2000 → loss
        rm._open_position_count = 1
        d = Decision(action="FLIP", confidence=0.9, reasoning="trend reversal", symbol="ETH",
                     stop_loss=2050, take_profit=1900, size_pct=3.0)
        result = await rm.validate_decision(d)
        assert result.approved is True
        assert result.size is not None

    @pytest.mark.asyncio
    async def test_validate_flip_in_profit_blocked(self, risk_env):
        """FLIP on a profitable position is blocked (use CLOSE instead)."""
        rm, pt, _ = risk_env
        await pt.open_position("ETH", 1900, 0.5, direction="LONG")  # entry 1900, mock price 2000 → profit
        rm._open_position_count = 1
        d = Decision(action="FLIP", confidence=0.9, reasoning="reverse", symbol="ETH")
        result = await rm.validate_decision(d)
        assert result.approved is False
        assert "in profit" in result.reason

    @pytest.mark.asyncio
    async def test_validate_flip_no_position_blocked(self, risk_env):
        """FLIP with no open position is blocked."""
        rm, pt, _ = risk_env
        d = Decision(action="FLIP", confidence=0.9, reasoning="reverse", symbol="ETH")
        result = await rm.validate_decision(d)
        assert result.approved is False
        assert "no open position" in result.reason

    @pytest.mark.asyncio
    async def test_validate_flip_daily_pause_blocked(self, risk_env):
        """FLIP is blocked during daily pause."""
        rm, pt, _ = risk_env
        await pt.open_position("ETH", 2100, 0.5, direction="LONG")
        rm._open_position_count = 1
        rm._daily_paused = True
        rm._daily_pause_reason = "daily dd"
        d = Decision(action="FLIP", confidence=0.9, reasoning="reverse", symbol="ETH")
        result = await rm.validate_decision(d)
        assert result.approved is False
        assert "DAILY PAUSE" in result.reason

    @pytest.mark.asyncio
    async def test_holding_period_blocks_early_close(self, db):
        """Holding period blocks AI CLOSE if position too young."""
        rc = RiskConfig(min_holding_minutes=15)
        client = MockClient(balance=1000.0)
        pt = PositionTracker(db, rc)
        rm = RiskManager(rc, client, db, position_tracker=pt)
        rm._current_balance = 1000.0
        rm._peak_balance = 1000.0

        # Open a fresh position
        await pt.open_position("ETH", 2000, 0.5, direction="LONG")

        d = Decision(action="CLOSE", confidence=0.8, reasoning="exit", symbol="ETH")
        result = await rm.validate_decision(d)
        assert result.approved is False
        assert "Holding period" in result.reason


# ── Kill switch & daily pause ───────────────────────────────


class TestKillSwitch:
    @pytest.mark.asyncio
    async def test_kill_switch_on_drawdown(self, db, risk_config):
        client = MockClient(balance=800)
        rm = RiskManager(risk_config, client, db)
        rm._peak_balance = 1000.0
        rm._current_balance = 800.0  # 20% drawdown > 15% max
        await rm._check_kill_switch()
        assert rm._kill_switch is True

    @pytest.mark.asyncio
    async def test_kill_switch_on_low_balance(self, db, risk_config):
        client = MockClient(balance=30)
        rm = RiskManager(risk_config, client, db)
        rm._peak_balance = 1000.0
        rm._current_balance = 30.0  # below min 50
        await rm._check_kill_switch()
        assert rm._kill_switch is True

    @pytest.mark.asyncio
    async def test_no_kill_switch_within_limits(self, db, risk_config):
        client = MockClient(balance=900)
        rm = RiskManager(risk_config, client, db)
        rm._peak_balance = 1000.0
        rm._current_balance = 900.0  # 10% drawdown < 15%
        await rm._check_kill_switch()
        assert rm._kill_switch is False

    @pytest.mark.asyncio
    async def test_consecutive_losses_no_kill_switch(self, db, risk_config):
        """Consecutive losses are handled by cooldown, not kill switch."""
        client = MockClient(balance=1000)
        rm = RiskManager(risk_config, client, db)
        rm._peak_balance = 1000.0
        rm._current_balance = 1000.0
        for i in range(5):
            await db.insert_trade(
                symbol="ETH", side="SELL", price=2000, quantity=0.1,
                pnl=-10.0, strategy="test",
            )
        await rm.check_consecutive_losses()
        assert rm._kill_switch is False

    @pytest.mark.asyncio
    async def test_reset_kill_switch(self, db, risk_config):
        client = MockClient(balance=800)
        rm = RiskManager(risk_config, client, db)
        rm._peak_balance = 1000.0
        rm._current_balance = 800.0
        await rm._check_kill_switch()
        assert rm._kill_switch is True
        await rm.reset_kill_switch()
        assert rm._kill_switch is False
        assert rm._kill_reason == ""

    def test_daily_pause_on_drawdown(self, risk_config):
        client = MockClient(balance=940)
        db_mock = MagicMock()
        rm = RiskManager(risk_config, client, db_mock)
        rm._daily.start_balance = 1000.0
        rm._daily.low_balance = 940.0
        rm._current_balance = 940.0  # 6% daily drawdown > 5%
        rm._check_daily_pause()
        assert rm._daily_paused is True


# ── Dynamic max positions ───────────────────────────────────


class TestDynamicPositions:
    def test_dynamic_100_usdc_4_slots(self):
        """100 USDC / 25 per slot = 4 slots (production scenario)."""
        rc = RiskConfig(dynamic_positions=True, usdc_per_position=25.0, max_open_positions=5)
        client = MockClient(balance=100)
        rm = RiskManager(rc, client, MagicMock())
        rm._current_balance = 100.0
        assert rm._effective_max_positions() == 4

    def test_dynamic_scales_with_balance(self):
        """500 USDC / 25 = 20, capped at max_open_positions=5."""
        rc = RiskConfig(dynamic_positions=True, usdc_per_position=25.0, max_open_positions=5)
        client = MockClient(balance=500)
        rm = RiskManager(rc, client, MagicMock())
        rm._current_balance = 500.0
        assert rm._effective_max_positions() == 5

    def test_dynamic_caps_at_max(self):
        """1000 USDC / 25 = 40, but capped at max_open_positions=10."""
        rc = RiskConfig(dynamic_positions=True, usdc_per_position=25.0, max_open_positions=10)
        client = MockClient(balance=1000)
        rm = RiskManager(rc, client, MagicMock())
        rm._current_balance = 1000.0
        assert rm._effective_max_positions() == 10

    def test_dynamic_floor_at_one(self):
        """10 USDC / 25 = 0, but floor at 1."""
        rc = RiskConfig(dynamic_positions=True, usdc_per_position=25.0)
        client = MockClient(balance=10)
        rm = RiskManager(rc, client, MagicMock())
        rm._current_balance = 10.0
        assert rm._effective_max_positions() == 1

    def test_static_ignores_balance(self):
        """dynamic_positions=False → always max_open_positions."""
        rc = RiskConfig(dynamic_positions=False, max_open_positions=5)
        client = MockClient(balance=10000)
        rm = RiskManager(rc, client, MagicMock())
        rm._current_balance = 10000.0
        assert rm._effective_max_positions() == 5

    @pytest.mark.asyncio
    async def test_dynamic_blocks_entry_when_full(self, db):
        """Balance=100 → 4 slots, with 4 open → blocks new entry."""
        rc = RiskConfig(dynamic_positions=True, usdc_per_position=25.0, max_open_positions=5)
        client = MockClient(balance=100)
        pt = PositionTracker(db, rc)
        rm = RiskManager(rc, client, db, position_tracker=pt)
        rm._current_balance = 100.0
        rm._peak_balance = 100.0
        rm._open_position_count = 4  # 4/4 full

        d = Decision(action="BUY", confidence=0.8, reasoning="sig", symbol="SOL")
        result = await rm.validate_decision(d)
        assert result.approved is False
        assert "Max open positions" in result.reason
        assert "4/4" in result.reason


# ── Risk metrics ────────────────────────────────────────────


class TestRiskMetrics:
    def test_risk_metrics_format(self, risk_config):
        client = MockClient(balance=1000)
        db_mock = MagicMock()
        rm = RiskManager(risk_config, client, db_mock)
        rm._peak_balance = 1000.0
        rm._current_balance = 950.0
        rm._open_position_count = 2

        metrics = rm.get_risk_metrics()
        expected_keys = {
            "current_balance", "peak_balance", "drawdown_pct",
            "daily_drawdown_pct", "open_positions", "max_open_positions",
            "kill_switch", "kill_reason", "daily_paused", "daily_pause_reason",
            "max_trade_pct", "stop_loss_pct", "take_profit_pct", "min_balance_usdc",
            "capital_utilization", "total_margin_used", "available_margin",
            "target_utilization",
        }
        assert set(metrics.keys()) == expected_keys
        assert metrics["current_balance"] == 950.0
        assert metrics["open_positions"] == 2
        assert metrics["drawdown_pct"] == pytest.approx(5.0, rel=1e-2)


# ── Spread check ────────────────────────────────────────────


class TestSpreadCheck:
    @pytest.mark.asyncio
    async def test_spread_blocks_wide_spread(self, db, risk_config):
        """Wide spread (2%) should block entry."""
        wide_l2 = {"levels": [
            [{"px": "1980", "sz": "10", "n": 5}],
            [{"px": "2020", "sz": "10", "n": 5}],
        ]}
        client = MockClient(balance=1000.0, l2_snapshot=wide_l2)
        mc = MarketConfig(max_spread_pct=0.5)
        pt = PositionTracker(db, risk_config)
        rm = RiskManager(risk_config, client, db, position_tracker=pt, market_config=mc)
        rm._current_balance = 1000.0
        rm._peak_balance = 1000.0

        d = Decision(action="BUY", confidence=0.8, reasoning="sig", symbol="ETH")
        result = await rm.validate_decision(d)
        assert result.approved is False
        assert "Spread" in result.reason

    @pytest.mark.asyncio
    async def test_spread_allows_tight_spread(self, db, risk_config):
        """Tight spread (0.05%) should allow entry."""
        tight_l2 = {"levels": [
            [{"px": "1999.5", "sz": "10", "n": 5}],
            [{"px": "2000.5", "sz": "10", "n": 5}],
        ]}
        client = MockClient(balance=1000.0, l2_snapshot=tight_l2)
        mc = MarketConfig(max_spread_pct=0.5)
        pt = PositionTracker(db, risk_config)
        rm = RiskManager(risk_config, client, db, position_tracker=pt, market_config=mc)
        rm._current_balance = 1000.0
        rm._peak_balance = 1000.0

        d = Decision(action="BUY", confidence=0.8, reasoning="sig", symbol="ETH",
                      stop_loss=1960, take_profit=2060)
        result = await rm.validate_decision(d)
        assert result.approved is True

    @pytest.mark.asyncio
    async def test_spread_check_disabled_when_no_market_config(self, db, risk_config):
        """Without market_config, spread check is skipped."""
        client = MockClient(balance=1000.0)
        pt = PositionTracker(db, risk_config)
        rm = RiskManager(risk_config, client, db, position_tracker=pt)
        rm._current_balance = 1000.0
        rm._peak_balance = 1000.0

        d = Decision(action="BUY", confidence=0.8, reasoning="sig", symbol="ETH",
                      stop_loss=1960, take_profit=2060)
        result = await rm.validate_decision(d)
        assert result.approved is True

    @pytest.mark.asyncio
    async def test_spread_check_graceful_on_l2_failure(self, db, risk_config):
        """If L2 snapshot fails, entry is still allowed (graceful degradation)."""
        client = MockClient(balance=1000.0)
        # Override to raise
        async def _raise_l2(coin):
            raise RuntimeError("L2 unavailable")
        client.get_l2_snapshot = _raise_l2

        mc = MarketConfig(max_spread_pct=0.5)
        pt = PositionTracker(db, risk_config)
        rm = RiskManager(risk_config, client, db, position_tracker=pt, market_config=mc)
        rm._current_balance = 1000.0
        rm._peak_balance = 1000.0

        d = Decision(action="BUY", confidence=0.8, reasoning="sig", symbol="ETH",
                      stop_loss=1960, take_profit=2060)
        result = await rm.validate_decision(d)
        assert result.approved is True

    @pytest.mark.asyncio
    async def test_spread_blocks_short_entry(self, db, risk_config):
        """Wide spread also blocks SHORT entries."""
        wide_l2 = {"levels": [
            [{"px": "1980", "sz": "10", "n": 5}],
            [{"px": "2020", "sz": "10", "n": 5}],
        ]}
        client = MockClient(balance=1000.0, l2_snapshot=wide_l2)
        mc = MarketConfig(max_spread_pct=0.5)
        pt = PositionTracker(db, risk_config)
        rm = RiskManager(risk_config, client, db, position_tracker=pt, market_config=mc)
        rm._current_balance = 1000.0
        rm._peak_balance = 1000.0

        d = Decision(action="SHORT", confidence=0.8, reasoning="sig", symbol="BTC")
        result = await rm.validate_decision(d)
        assert result.approved is False
        assert "Spread" in result.reason


# ── ATR-based stop loss ───────────────────────────────────


def _make_candle_df(n: int = 50, price: float = 2000.0, volatility: float = 20.0) -> pd.DataFrame:
    """Generate synthetic 15m candle data for ATR testing."""
    np.random.seed(42)
    closes = price + np.cumsum(np.random.randn(n) * volatility * 0.1)
    highs = closes + np.abs(np.random.randn(n) * volatility * 0.05)
    lows = closes - np.abs(np.random.randn(n) * volatility * 0.05)
    opens = closes + np.random.randn(n) * volatility * 0.02
    volumes = np.random.uniform(100, 1000, n)
    idx = pd.date_range("2024-01-01", periods=n, freq="15min", tz="UTC")
    return pd.DataFrame({
        "open": opens, "high": highs, "low": lows, "close": closes, "volume": volumes,
    }, index=idx)


class MockMarketData:
    """Minimal mock for MarketData with candle access."""
    def __init__(self, candles: dict[tuple[str, str], pd.DataFrame] | None = None):
        self._candles = candles or {}

    def get_candles(self, coin: str, interval: str) -> pd.DataFrame | None:
        return self._candles.get((coin, interval))


class TestAtrStopLoss:
    def test_atr_sl_long_below_price(self):
        """ATR-based SL for LONG should be below price."""
        df = _make_candle_df(50, price=2000.0, volatility=20.0)
        md = MockMarketData({("ETH", "15m"): df})
        rc = RiskConfig(use_atr_sl=True, atr_sl_multiplier=2.0, atr_sl_min_pct=0.5, atr_sl_max_pct=3.0)
        rm = RiskManager(rc, MockClient(), MagicMock(), market_data=md)
        sl = rm._compute_atr_sl("ETH", 2000.0, is_long=True)
        assert sl is not None
        assert sl < 2000.0

    def test_atr_sl_short_above_price(self):
        """ATR-based SL for SHORT should be above price."""
        df = _make_candle_df(50, price=2000.0, volatility=20.0)
        md = MockMarketData({("ETH", "15m"): df})
        rc = RiskConfig(use_atr_sl=True, atr_sl_multiplier=2.0)
        rm = RiskManager(rc, MockClient(), MagicMock(), market_data=md)
        sl = rm._compute_atr_sl("ETH", 2000.0, is_long=False)
        assert sl is not None
        assert sl > 2000.0

    def test_atr_sl_clamped_to_min(self):
        """Very low volatility → SL clamped to min_pct."""
        # Create near-flat candles (very low ATR)
        n = 50
        flat = np.full(n, 2000.0)
        idx = pd.date_range("2024-01-01", periods=n, freq="15min", tz="UTC")
        df = pd.DataFrame({
            "open": flat, "high": flat + 0.01, "low": flat - 0.01,
            "close": flat, "volume": np.full(n, 100.0),
        }, index=idx)
        md = MockMarketData({("ETH", "15m"): df})
        rc = RiskConfig(use_atr_sl=True, atr_sl_multiplier=2.0, atr_sl_min_pct=0.5)
        rm = RiskManager(rc, MockClient(), MagicMock(), market_data=md)
        sl = rm._compute_atr_sl("ETH", 2000.0, is_long=True)
        assert sl is not None
        # Min distance = 2000 * 0.5% = 10.0
        assert sl == pytest.approx(2000.0 - 10.0, abs=0.01)

    def test_atr_sl_clamped_to_max(self):
        """Very high volatility → SL clamped to max_pct."""
        n = 50
        np.random.seed(99)
        closes = 2000.0 + np.cumsum(np.random.randn(n) * 200)
        idx = pd.date_range("2024-01-01", periods=n, freq="15min", tz="UTC")
        df = pd.DataFrame({
            "open": closes,
            "high": closes + np.abs(np.random.randn(n) * 100),
            "low": closes - np.abs(np.random.randn(n) * 100),
            "close": closes,
            "volume": np.full(n, 100.0),
        }, index=idx)
        md = MockMarketData({("ETH", "15m"): df})
        rc = RiskConfig(use_atr_sl=True, atr_sl_multiplier=2.0, atr_sl_max_pct=3.0)
        rm = RiskManager(rc, MockClient(), MagicMock(), market_data=md)
        sl = rm._compute_atr_sl("ETH", 2000.0, is_long=True)
        assert sl is not None
        # Max distance = 2000 * 3% = 60.0 → SL >= 1940
        assert sl >= 2000.0 - 60.01

    def test_atr_sl_returns_none_without_market_data(self):
        """No market_data → returns None (fallback to fixed %)."""
        rc = RiskConfig(use_atr_sl=True)
        rm = RiskManager(rc, MockClient(), MagicMock())
        sl = rm._compute_atr_sl("ETH", 2000.0, is_long=True)
        assert sl is None

    def test_atr_sl_returns_none_when_disabled(self):
        """use_atr_sl=False → returns None."""
        df = _make_candle_df(50)
        md = MockMarketData({("ETH", "15m"): df})
        rc = RiskConfig(use_atr_sl=False)
        rm = RiskManager(rc, MockClient(), MagicMock(), market_data=md)
        sl = rm._compute_atr_sl("ETH", 2000.0, is_long=True)
        assert sl is None

    def test_atr_sl_returns_none_insufficient_candles(self):
        """Too few candles → returns None."""
        df = _make_candle_df(10)  # need >= 15
        md = MockMarketData({("ETH", "15m"): df})
        rc = RiskConfig(use_atr_sl=True)
        rm = RiskManager(rc, MockClient(), MagicMock(), market_data=md)
        sl = rm._compute_atr_sl("ETH", 2000.0, is_long=True)
        assert sl is None

    @pytest.mark.asyncio
    async def test_validate_uses_atr_sl(self, db):
        """validate_decision should use ATR-based SL when available."""
        df = _make_candle_df(50, price=2000.0, volatility=20.0)
        md = MockMarketData({("ETH", "15m"): df})
        rc = RiskConfig(use_atr_sl=True, atr_sl_multiplier=2.0, atr_sl_min_pct=0.5, atr_sl_max_pct=3.0)
        client = MockClient(balance=1000.0)
        pt = PositionTracker(db, rc)
        rm = RiskManager(rc, client, db, position_tracker=pt, market_data=md)
        rm._current_balance = 1000.0
        rm._peak_balance = 1000.0
        rm._open_position_count = 0

        d = Decision(action="BUY", confidence=0.75, reasoning="signal", symbol="ETH")
        result = await rm.validate_decision(d)
        assert result.approved is True
        # SL should be ATR-based (different from fixed 1.5% → 2000*0.985=1970)
        assert result.decision.stop_loss is not None
        assert result.decision.stop_loss < 2000.0

    @pytest.mark.asyncio
    async def test_validate_falls_back_to_fixed_sl(self, db):
        """When ATR unavailable, should use fixed stop_loss_pct."""
        md = MockMarketData()  # no candles → ATR returns None
        rc = RiskConfig(use_atr_sl=True, stop_loss_pct=1.5)
        client = MockClient(balance=1000.0)
        pt = PositionTracker(db, rc)
        rm = RiskManager(rc, client, db, position_tracker=pt, market_data=md)
        rm._current_balance = 1000.0
        rm._peak_balance = 1000.0
        rm._open_position_count = 0

        d = Decision(action="BUY", confidence=0.75, reasoning="signal", symbol="ETH")
        result = await rm.validate_decision(d)
        assert result.approved is True
        # Fixed 1.5%: 2000 * (1 - 0.015) = 1970
        assert result.decision.stop_loss == pytest.approx(1970.0, abs=0.01)


# ── Risk-based position sizing ──────────────────────────────


class TestRiskBasedSizing:
    def test_risk_cap_reduces_size_for_wide_sl(self):
        """Wide SL (10%) with small risk budget → risk cap shrinks size."""
        from risk.position_sizer import PositionSizer
        # usdc_per_position=25 on 1000 → base=2.5%, boost 2.5x → 6.25%
        # 0.5% risk on 1000 = $5 budget. SL=10% → risk_cap=5% → caps 6.25% to 5%
        rc = RiskConfig(risk_per_trade_pct=0.5, max_trade_pct=15.0, max_size_boost=2.5,
                        usdc_per_position=25.0)
        sizer = PositionSizer(rc)
        stats = {"total_trades": 0}

        # With boost, cold start = 2.5% * 2.5 = 6.25%
        uncapped = sizer.compute(1000, stats, 0.75, utilization_boost=2.5, sl_distance_pct=None)
        assert uncapped.size_pct == pytest.approx(6.25, abs=0.1)

        # With 10% SL: risk_cap = (1000*0.005) / 0.10 / 1000 * 100 = 5%
        capped = sizer.compute(1000, stats, 0.75, utilization_boost=2.5, sl_distance_pct=10.0)
        assert capped.size_pct == pytest.approx(5.0, abs=0.1)
        assert capped.size_usdc < uncapped.size_usdc

    def test_narrow_sl_no_risk_cap(self):
        """Narrow SL (0.5%) → risk cap is very high, doesn't reduce size."""
        from risk.position_sizer import PositionSizer
        rc = RiskConfig(risk_per_trade_pct=1.0, max_trade_pct=15.0)
        sizer = PositionSizer(rc)
        stats = {"total_trades": 0}

        no_sl = sizer.compute(1000, stats, 0.75, sl_distance_pct=None)
        narrow = sizer.compute(1000, stats, 0.75, sl_distance_pct=0.5)
        assert narrow.size_pct == no_sl.size_pct  # risk cap too high to matter

    def test_sl_none_skips_risk_cap(self):
        """When sl_distance_pct=None, risk cap is skipped."""
        from risk.position_sizer import PositionSizer
        rc = RiskConfig(risk_per_trade_pct=1.0)
        sizer = PositionSizer(rc)
        stats = {"total_trades": 0}

        result = sizer.compute(1000, stats, 0.75, sl_distance_pct=None)
        assert result.size_usdc > 0

    def test_risk_cap_in_kelly_mode(self):
        """Risk cap also works in Kelly mode (enough trade history)."""
        from risk.position_sizer import PositionSizer
        rc = RiskConfig(risk_per_trade_pct=1.0, max_trade_pct=15.0)
        sizer = PositionSizer(rc)
        stats = {
            "total_trades": 50,
            "win_rate": 0.6,
            "avg_win": 20.0,
            "avg_loss": 10.0,
        }
        # Kelly should give a decent size, risk cap with wide SL should reduce
        wide = sizer.compute(1000, stats, 0.8, sl_distance_pct=10.0)
        narrow = sizer.compute(1000, stats, 0.8, sl_distance_pct=1.0)
        # Wide SL gets smaller or equal size due to risk cap
        assert wide.size_usdc <= narrow.size_usdc


# ── Kelly floor: usdc_per_position ────────────────────────────


class TestKellyFloor:
    """Kelly mode must use max(MIN_ORDER_USDC, usdc_per_position) as floor."""

    def test_kelly_floor_uses_usdc_per_position(self):
        """With usdc_per_position=40, Kelly floor should be $40, not $10."""
        from risk.position_sizer import PositionSizer, MIN_ORDER_USDC
        rc = RiskConfig(usdc_per_position=40.0, max_trade_pct=15.0)
        sizer = PositionSizer(rc)
        # Stats that produce tiny Kelly size
        stats = {
            "total_trades": 50,
            "win_rate": 0.5,
            "avg_win": 5.0,
            "avg_loss": 5.0,
        }
        result = sizer.compute(1000, stats, 0.7)
        # Kelly with 50/50 win/loss and payoff=1 gives f=0 → floor kicks in
        # Floor should be 40 USDC (usdc_per_position), not 10 (MIN_ORDER_USDC)
        assert result.size_usdc == pytest.approx(40.0, abs=0.01)

    def test_kelly_floor_default_25(self):
        """Default usdc_per_position=25 → floor at $25."""
        from risk.position_sizer import PositionSizer
        rc = RiskConfig(max_trade_pct=15.0)  # default usdc_per_position=25
        sizer = PositionSizer(rc)
        stats = {
            "total_trades": 50,
            "win_rate": 0.5,
            "avg_win": 5.0,
            "avg_loss": 5.0,
        }
        result = sizer.compute(1000, stats, 0.7)
        assert result.size_usdc == pytest.approx(25.0, abs=0.01)

    def test_kelly_floor_min_order_usdc_when_per_position_low(self):
        """If usdc_per_position < MIN_ORDER_USDC, floor is MIN_ORDER_USDC."""
        from risk.position_sizer import PositionSizer, MIN_ORDER_USDC
        rc = RiskConfig(usdc_per_position=5.0, max_trade_pct=15.0)
        sizer = PositionSizer(rc)
        stats = {
            "total_trades": 50,
            "win_rate": 0.5,
            "avg_win": 5.0,
            "avg_loss": 5.0,
        }
        result = sizer.compute(1000, stats, 0.7)
        assert result.size_usdc == pytest.approx(MIN_ORDER_USDC, abs=0.01)

    def test_kelly_large_enough_skips_floor(self):
        """When Kelly produces size > usdc_per_position, no floor applied."""
        from risk.position_sizer import PositionSizer
        rc = RiskConfig(usdc_per_position=25.0, max_trade_pct=15.0)
        sizer = PositionSizer(rc)
        # Great stats → Kelly gives big size
        stats = {
            "total_trades": 50,
            "win_rate": 0.7,
            "avg_win": 30.0,
            "avg_loss": 10.0,
        }
        result = sizer.compute(1000, stats, 0.9)
        # Kelly should produce well above $25
        assert result.size_usdc > 25.0

    def test_kelly_floor_falls_back_to_max_pct(self):
        """Floor > max_trade_pct → falls back to max allowed (not rejected)."""
        from risk.position_sizer import PositionSizer
        rc = RiskConfig(usdc_per_position=40.0, max_trade_pct=2.0)
        sizer = PositionSizer(rc)
        stats = {
            "total_trades": 50,
            "win_rate": 0.5,
            "avg_win": 5.0,
            "avg_loss": 5.0,
        }
        # Bankroll=1000, max_pct=2% → max $20. Floor=$40 > $20 → use $20
        result = sizer.compute(1000, stats, 0.7)
        assert result.size_usdc == pytest.approx(20.0, abs=0.01)
        assert result.capped is True

    def test_kelly_floor_rejected_only_below_exchange_min(self):
        """Only rejected if even max_trade_pct < MIN_ORDER_USDC ($10)."""
        from risk.position_sizer import PositionSizer, MIN_ORDER_USDC
        rc = RiskConfig(usdc_per_position=40.0, max_trade_pct=1.0)
        sizer = PositionSizer(rc)
        stats = {
            "total_trades": 50,
            "win_rate": 0.5,
            "avg_win": 5.0,
            "avg_loss": 5.0,
        }
        # Bankroll=500, max_pct=1% → $5 < MIN_ORDER_USDC → reject
        result = sizer.compute(500, stats, 0.7)
        assert result.size_usdc == 0
        assert "exchange minimum" in result.reason

    def test_cold_start_reject_below_exchange_min(self):
        """Cold-start rejects only when below exchange minimum ($10)."""
        from risk.position_sizer import PositionSizer
        rc = RiskConfig(usdc_per_position=40.0, max_trade_pct=1.0)
        sizer = PositionSizer(rc)
        stats = {"total_trades": 0}
        # Bankroll=100, max_pct=1% → $1 < MIN_ORDER_USDC → rejected
        result = sizer.compute(100, stats, 0.7)
        assert result.size_usdc == 0
        assert "exchange minimum" in result.reason

    def test_small_bankroll_realistic(self):
        """120 USDC, usdc_per_position=25, max_trade_pct=15% → $18 trades."""
        from risk.position_sizer import PositionSizer
        rc = RiskConfig(usdc_per_position=25.0, max_trade_pct=15.0)
        sizer = PositionSizer(rc)
        stats = {
            "total_trades": 50,
            "win_rate": 0.5,
            "avg_win": 5.0,
            "avg_loss": 5.0,
        }
        result = sizer.compute(120, stats, 0.7)
        # Floor=$25 > 15% of 120=$18 → falls back to $18
        assert result.size_usdc == pytest.approx(18.0, abs=0.01)
        assert result.capped is True


# ── Partial take profit ──────────────────────────────────────


class TestPartialTakeProfit:
    @pytest.mark.asyncio
    async def test_partial_close_creates_two_records(self, db, risk_config):
        """partial_close closes original + creates new position at breakeven."""
        pt = PositionTracker(db, risk_config)
        pos_id = await pt.open_position("ETH", 2000, 1.0, stop_loss=1960, direction="LONG")

        pnl, closed_qty, new_id = await pt.partial_close(pos_id, 50.0, 2020.0)

        # Original closed
        orig = await pt._get_by_id(pos_id)
        assert orig["status"] == "CLOSED"
        assert orig["pnl"] is not None
        assert pnl > 0  # profitable close

        # New position
        new_pos = await pt._get_by_id(new_id)
        assert new_pos["status"] == "OPEN"
        assert new_pos["quantity"] == pytest.approx(0.5, abs=0.001)
        assert new_pos["entry_price"] == 2000  # same entry
        assert new_pos["stop_loss"] == 2000  # breakeven
        assert new_pos["partial_closed"] == 1

    @pytest.mark.asyncio
    async def test_partial_close_short(self, db, risk_config):
        """Partial close works for SHORT positions."""
        pt = PositionTracker(db, risk_config)
        pos_id = await pt.open_position("ETH", 2000, 1.0, stop_loss=2040, direction="SHORT")

        pnl, closed_qty, new_id = await pt.partial_close(pos_id, 50.0, 1980.0)
        assert pnl > 0  # SHORT profitable (entry=2000, exit=1980)
        assert closed_qty == pytest.approx(0.5, abs=0.001)

        new_pos = await pt._get_by_id(new_id)
        assert new_pos["direction"] == "SHORT"
        assert new_pos["stop_loss"] == 2000  # breakeven

    @pytest.mark.asyncio
    async def test_partial_tp_triggers_in_check_sl_tp(self, db):
        """check_sl_tp returns PARTIAL_CLOSE when gain >= trigger."""
        rc = RiskConfig(
            partial_tp_enabled=True,
            partial_tp_trigger_pct=1.0,
            sl_tp_grace_seconds=0,
        )
        pt = PositionTracker(db, rc)
        await pt.open_position("ETH", 2000, 1.0, stop_loss=1960, direction="LONG")

        # Price at +1.5% → triggers partial TP
        to_close = await pt.check_sl_tp({"ETH": 2030.0})
        assert len(to_close) == 1
        assert to_close[0]["action"] == "PARTIAL_CLOSE"

    @pytest.mark.asyncio
    async def test_partial_tp_skipped_if_already_partial(self, db):
        """Positions with partial_closed=1 skip partial TP check."""
        rc = RiskConfig(
            partial_tp_enabled=True,
            partial_tp_trigger_pct=1.0,
            sl_tp_grace_seconds=0,
            trailing_breakeven_pct=0.5,
            trailing_start_pct=1.0,
        )
        pt = PositionTracker(db, rc)
        pos_id = await pt.open_position("ETH", 2000, 1.0, stop_loss=1960, direction="LONG")
        # Simulate partial close already happened
        _, _, new_id = await pt.partial_close(pos_id, 50.0, 2020.0)

        # New position has partial_closed=1 → should not trigger partial TP again
        to_close = await pt.check_sl_tp({"ETH": 2030.0})
        # Should get trailing/SL check, not PARTIAL_CLOSE
        partial_actions = [t for t in to_close if t["action"] == "PARTIAL_CLOSE"]
        assert len(partial_actions) == 0

    @pytest.mark.asyncio
    async def test_partial_tp_disabled(self, db):
        """partial_tp_enabled=False → no partial close triggers."""
        rc = RiskConfig(
            partial_tp_enabled=False,
            sl_tp_grace_seconds=0,
        )
        pt = PositionTracker(db, rc)
        await pt.open_position("ETH", 2000, 1.0, stop_loss=1960, direction="LONG")

        to_close = await pt.check_sl_tp({"ETH": 2030.0})
        partial_actions = [t for t in to_close if t["action"] == "PARTIAL_CLOSE"]
        assert len(partial_actions) == 0


# ── ATR trailing distance ─────────────────────────────────────


class TestAtrTrailingDistance:
    def test_atr_trailing_returns_distances(self, db, risk_config):
        """ATR trailing returns (trail_pct, tight_pct) when candles available."""
        df = _make_candle_df(50, price=2000.0, volatility=20.0)
        md = MockMarketData({("ETH", "15m"): df})
        rc = RiskConfig(
            use_atr_trailing=True,
            atr_trailing_multiplier=1.5,
            atr_trailing_tight_multiplier=1.0,
            atr_trailing_min_pct=0.5,
            atr_trailing_max_pct=3.0,
        )
        pt = PositionTracker(db, rc, market_data=md)
        result = pt._get_atr_trailing_distance("ETH", 2000.0)
        assert result is not None
        trail_pct, tight_pct = result
        assert 0.5 <= trail_pct <= 3.0
        assert 0.5 <= tight_pct <= 3.0
        assert trail_pct >= tight_pct

    def test_atr_trailing_returns_none_without_market_data(self, db, risk_config):
        """No market_data → returns None (fallback to fixed)."""
        pt = PositionTracker(db, risk_config)
        result = pt._get_atr_trailing_distance("ETH", 2000.0)
        assert result is None

    def test_atr_trailing_returns_none_when_disabled(self, db):
        rc = RiskConfig(use_atr_trailing=False)
        df = _make_candle_df(50)
        md = MockMarketData({("ETH", "15m"): df})
        pt = PositionTracker(db, rc, market_data=md)
        result = pt._get_atr_trailing_distance("ETH", 2000.0)
        assert result is None

    def test_atr_trailing_caches_result(self, db):
        """Result is cached for 5 minutes."""
        df = _make_candle_df(50, price=2000.0)
        md = MockMarketData({("ETH", "15m"): df})
        rc = RiskConfig(use_atr_trailing=True)
        pt = PositionTracker(db, rc, market_data=md)

        result1 = pt._get_atr_trailing_distance("ETH", 2000.0)
        result2 = pt._get_atr_trailing_distance("ETH", 2000.0)
        assert result1 == result2
        assert "ETH" in pt._atr_cache
