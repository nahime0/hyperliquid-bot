"""Integration tests — require `claude` CLI to be available.

Run with: pytest tests/test_integration.py -m integration -v
"""
from __future__ import annotations

import json

import pytest
import pytest_asyncio

from core.ai_advisor import AIAdvisor


pytestmark = pytest.mark.integration


@pytest.fixture
def advisor():
    return AIAdvisor(model="haiku", timeout=120)


class TestAdvisorIntegration:
    @pytest.mark.asyncio
    async def test_advisor_position_review(self, advisor):
        """Send 1 open position — AI should return HOLD/CLOSE/ADJUST."""
        result = await advisor.consult(
            positions=[{
                "symbol": "ETH",
                "direction": "LONG",
                "entry_price": 2000.0,
                "quantity": 0.5,
                "stop_loss": 1960.0,
                "take_profit": 2060.0,
                "leverage": 2,
                "unrealized_pnl": 15.0,
                "pnl_pct": 1.5,
            }],
            opportunities=[],
            account={"balance_usdc": 1000.0, "equity": 1015.0},
        )
        assert "positions" in result
        assert "opportunities" in result
        for pa in result["positions"]:
            assert pa["action"] in ("HOLD", "CLOSE", "ADJUST")
            assert "reasoning" in pa

    @pytest.mark.asyncio
    async def test_advisor_opportunity_review(self, advisor):
        """Send 1 opportunity — AI should return BUY/SHORT/HOLD."""
        result = await advisor.consult(
            positions=[],
            opportunities=[{
                "symbol": "SOL",
                "direction": "LONG",
                "signal_strength": 0.8,
                "strategy": "mean_reversion",
                "current_price": 150.0,
                "rsi": 28.0,
            }],
            account={"balance_usdc": 1000.0, "equity": 1000.0},
        )
        assert "opportunities" in result
        for oa in result["opportunities"]:
            assert oa["action"] in ("BUY", "SHORT", "HOLD")
            assert "reasoning" in oa

    @pytest.mark.asyncio
    async def test_advisor_mixed_payload(self, advisor):
        """Send 1 position + 1 opportunity — both should be reviewed."""
        result = await advisor.consult(
            positions=[{
                "symbol": "ETH",
                "direction": "LONG",
                "entry_price": 2000.0,
                "quantity": 0.5,
                "unrealized_pnl": -10.0,
                "pnl_pct": -1.0,
            }],
            opportunities=[{
                "symbol": "BTC",
                "direction": "SHORT",
                "signal_strength": 0.7,
                "strategy": "rsi_divergence",
                "current_price": 60000.0,
                "rsi": 72.0,
            }],
            account={"balance_usdc": 1000.0, "equity": 990.0},
        )
        assert "positions" in result
        assert "opportunities" in result

    @pytest.mark.asyncio
    async def test_advisor_schema_validation(self, advisor):
        """Response should match the JSON schema structure."""
        result = await advisor.consult(
            positions=[{
                "symbol": "ETH",
                "direction": "LONG",
                "entry_price": 2000.0,
                "quantity": 0.5,
                "unrealized_pnl": 0.0,
            }],
            opportunities=[],
            account={"balance_usdc": 1000.0},
        )
        assert isinstance(result, dict)
        assert isinstance(result.get("positions"), list)
        assert isinstance(result.get("opportunities"), list)
        for pa in result["positions"]:
            assert isinstance(pa, dict)
            assert "symbol" in pa
            assert "action" in pa
            assert "reasoning" in pa

    @pytest.mark.asyncio
    async def test_advisor_defer_roundtrip(self, advisor):
        """If AI returns HOLD with defer conditions, check_deferred should work."""
        # This test verifies the defer mechanism works end-to-end
        # We manually defer (since we can't control AI output)
        advisor.set_cycle(1)
        await advisor.defer("SOL", "BUY", {"wait_cycles": 2})
        assert "SOL" in advisor.deferred_symbols

        # Not ready yet
        advisor.set_cycle(2)
        ready = await advisor.check_deferred({"SOL": 150.0})
        assert "SOL" not in ready

        # Ready now
        advisor.set_cycle(4)
        ready = await advisor.check_deferred({"SOL": 150.0})
        assert "SOL" in ready
        assert "SOL" not in advisor.deferred_symbols
