"""Shared fixtures for the test suite."""
from __future__ import annotations

import pytest
import pytest_asyncio

from config.settings import AIConfig, RiskConfig, Settings
from core.types import Decision
from data.db import Database
from risk.position_tracker import PositionTracker


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "integration: tests that call external services (claude CLI)"
    )


# ── Config fixtures ─────────────────────────────────────────


@pytest.fixture
def risk_config() -> RiskConfig:
    return RiskConfig(sl_tp_grace_seconds=0, partial_tp_enabled=False)


@pytest.fixture
def ai_config() -> AIConfig:
    return AIConfig()


@pytest.fixture
def settings() -> Settings:
    return Settings()


# ── Database (in-memory) ────────────────────────────────────


@pytest_asyncio.fixture
async def db():
    d = Database(":memory:")
    await d.connect()
    yield d
    await d.close()


# ── Position tracker ────────────────────────────────────────


@pytest_asyncio.fixture
async def position_tracker(db, risk_config):
    return PositionTracker(db, risk_config)


# ── Sample data ─────────────────────────────────────────────


@pytest.fixture
def sample_positions():
    return [
        {
            "symbol": "ETH",
            "direction": "LONG",
            "entry_price": 2000.0,
            "quantity": 0.5,
            "stop_loss": 1960.0,
            "take_profit": 2060.0,
            "leverage": 2,
            "unrealized_pnl": 15.0,
            "pnl_pct": 1.5,
        },
        {
            "symbol": "BTC",
            "direction": "SHORT",
            "entry_price": 60000.0,
            "quantity": 0.01,
            "stop_loss": 61200.0,
            "take_profit": 58800.0,
            "leverage": 2,
            "unrealized_pnl": -5.0,
            "pnl_pct": -0.83,
        },
    ]


@pytest.fixture
def sample_opportunities():
    return [
        {
            "symbol": "SOL",
            "direction": "LONG",
            "signal_strength": 0.8,
            "strategy": "mean_reversion",
            "current_price": 150.0,
            "rsi": 28.0,
        },
        {
            "symbol": "DOGE",
            "direction": "SHORT",
            "signal_strength": 0.7,
            "strategy": "rsi_divergence",
            "current_price": 0.15,
            "rsi": 75.0,
        },
    ]


@pytest.fixture
def sample_account():
    return {
        "balance_usdc": 1000.0,
        "equity": 1010.0,
        "open_positions": 2,
        "max_positions": 5,
    }


@pytest.fixture
def sample_decision_buy():
    return Decision(
        action="BUY",
        confidence=0.75,
        reasoning="RSI oversold with bullish divergence",
        symbol="SOL",
        size_pct=5.0,
        order_type="MARKET",
        stop_loss=145.0,
        take_profit=157.5,
        strategy_type="rsi_divergence",
    )


@pytest.fixture
def sample_decision_short():
    return Decision(
        action="SHORT",
        confidence=0.7,
        reasoning="RSI overbought with bearish divergence",
        symbol="DOGE",
        size_pct=4.0,
        order_type="MARKET",
        stop_loss=0.155,
        take_profit=0.143,
        strategy_type="rsi_divergence",
    )


@pytest.fixture
def sample_decision_close():
    return Decision(
        action="CLOSE",
        confidence=0.8,
        reasoning="Take profit target reached",
        symbol="ETH",
    )
