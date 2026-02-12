"""Tests for risk.risk_manager.RiskManager."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

from config.settings import RiskConfig
from core.types import Decision
from data.db import Database
from risk.position_tracker import PositionTracker
from risk.risk_manager import RiskManager


# ── Mock client ─────────────────────────────────────────────


class MockClient:
    """Lightweight mock for HyperliquidClient."""

    def __init__(self, balance: float = 1000.0, positions: list | None = None):
        self._balance = balance
        self._positions = positions or []

    async def get_account_balance(self) -> float:
        return self._balance

    async def get_open_positions(self) -> list:
        return self._positions

    async def get_price(self, coin: str) -> float:
        return 2000.0

    async def update_leverage(self, coin, lev, is_cross=True):
        pass


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

        # CLOSE allowed
        d_close = Decision(action="CLOSE", confidence=0.8, reasoning="exit", symbol="ETH")
        result = await rm.validate_decision(d_close)
        # CLOSE may be blocked for other reasons (no position), but not daily pause
        assert "DAILY PAUSE" not in result.reason

    @pytest.mark.asyncio
    async def test_validate_max_positions_blocks(self, risk_env):
        rm, pt, _ = risk_env
        rm._open_position_count = 5  # max_open_positions=5
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
        assert "too low" in result.reason

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
        rc = RiskConfig(auto_take_profit=True)
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
        rc = RiskConfig(auto_take_profit=True)
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


# ── Kill switch & daily pause ───────────────────────────────


class TestKillSwitch:
    def test_kill_switch_on_drawdown(self, risk_config):
        client = MockClient(balance=800)
        db_mock = MagicMock()
        rm = RiskManager(risk_config, client, db_mock)
        rm._peak_balance = 1000.0
        rm._current_balance = 800.0  # 20% drawdown > 15% max
        rm._check_kill_switch()
        assert rm._kill_switch is True

    def test_kill_switch_on_low_balance(self, risk_config):
        client = MockClient(balance=30)
        db_mock = MagicMock()
        rm = RiskManager(risk_config, client, db_mock)
        rm._peak_balance = 1000.0
        rm._current_balance = 30.0  # below min 50
        rm._check_kill_switch()
        assert rm._kill_switch is True

    def test_no_kill_switch_within_limits(self, risk_config):
        client = MockClient(balance=900)
        db_mock = MagicMock()
        rm = RiskManager(risk_config, client, db_mock)
        rm._peak_balance = 1000.0
        rm._current_balance = 900.0  # 10% drawdown < 15%
        rm._check_kill_switch()
        assert rm._kill_switch is False

    @pytest.mark.asyncio
    async def test_consecutive_losses_kill(self, db, risk_config):
        client = MockClient(balance=1000)
        rm = RiskManager(risk_config, client, db)
        rm._peak_balance = 1000.0
        rm._current_balance = 1000.0
        # Insert 5 consecutive losing trades
        for i in range(5):
            await db.insert_trade(
                symbol="ETH", side="SELL", price=2000, quantity=0.1,
                pnl=-10.0, strategy="test",
            )
        await rm.check_consecutive_losses()
        assert rm._kill_switch is True

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
    def test_dynamic_scales_with_balance(self):
        """1000 USDC / 200 per slot = 5 slots."""
        rc = RiskConfig(dynamic_positions=True, usdc_per_position=200.0, max_open_positions=10)
        client = MockClient(balance=1000)
        rm = RiskManager(rc, client, MagicMock())
        rm._current_balance = 1000.0
        assert rm._effective_max_positions() == 5

    def test_dynamic_caps_at_max(self):
        """5000 USDC / 200 = 25, but capped at max_open_positions=10."""
        rc = RiskConfig(dynamic_positions=True, usdc_per_position=200.0, max_open_positions=10)
        client = MockClient(balance=5000)
        rm = RiskManager(rc, client, MagicMock())
        rm._current_balance = 5000.0
        assert rm._effective_max_positions() == 10

    def test_dynamic_floor_at_one(self):
        """100 USDC / 200 = 0, but floor at 1."""
        rc = RiskConfig(dynamic_positions=True, usdc_per_position=200.0)
        client = MockClient(balance=100)
        rm = RiskManager(rc, client, MagicMock())
        rm._current_balance = 100.0
        assert rm._effective_max_positions() == 1

    def test_dynamic_small_balance(self):
        """250 USDC / 200 = 1 slot."""
        rc = RiskConfig(dynamic_positions=True, usdc_per_position=200.0, max_open_positions=10)
        client = MockClient(balance=250)
        rm = RiskManager(rc, client, MagicMock())
        rm._current_balance = 250.0
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
        """Balance=400 → 2 slots, with 2 open → blocks new entry."""
        rc = RiskConfig(dynamic_positions=True, usdc_per_position=200.0, max_open_positions=10)
        client = MockClient(balance=400)
        pt = PositionTracker(db, rc)
        rm = RiskManager(rc, client, db, position_tracker=pt)
        rm._current_balance = 400.0
        rm._peak_balance = 400.0
        rm._open_position_count = 2  # 2/2 full

        d = Decision(action="BUY", confidence=0.8, reasoning="sig", symbol="SOL")
        result = await rm.validate_decision(d)
        assert result.approved is False
        assert "Max open positions" in result.reason
        assert "2/2" in result.reason


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
        }
        assert set(metrics.keys()) == expected_keys
        assert metrics["current_balance"] == 950.0
        assert metrics["open_positions"] == 2
        assert metrics["drawdown_pct"] == pytest.approx(5.0, rel=1e-2)
