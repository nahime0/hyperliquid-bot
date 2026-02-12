"""Tests for core.ai_advisor.AIAdvisor."""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch, MagicMock

import pytest
import pytest_asyncio

from core.ai_advisor import AIAdvisor


# ── Deferred opportunity tracking ───────────────────────────


class TestDeferredTracking:
    def test_defer_adds_symbol(self):
        advisor = AIAdvisor(model="haiku")
        advisor.defer("BTC", "SHORT", {"wait_cycles": 3})
        assert "BTC" in advisor.deferred_symbols

    def test_defer_replaces_existing(self):
        advisor = AIAdvisor(model="haiku")
        advisor.defer("BTC", "SHORT", {"wait_cycles": 3})
        advisor.defer("BTC", "LONG", {"wait_cycles": 5})
        assert "BTC" in advisor.deferred_symbols
        # Latest conditions win — check via check_deferred behavior
        advisor.set_cycle(4)
        ready = advisor.check_deferred({})
        assert "BTC" not in ready  # 4 - 0 = 4 < 5

    def test_remove_deferred(self):
        advisor = AIAdvisor(model="haiku")
        advisor.defer("BTC", "SHORT", {"wait_cycles": 3})
        advisor.remove_deferred("BTC")
        assert "BTC" not in advisor.deferred_symbols

    def test_remove_nonexistent_no_error(self):
        advisor = AIAdvisor(model="haiku")
        advisor.remove_deferred("XYZ")  # should not raise

    def test_deferred_symbols_property(self):
        advisor = AIAdvisor(model="haiku")
        advisor.defer("BTC", "SHORT", {})
        advisor.defer("ETH", "LONG", {})
        advisor.defer("SOL", "LONG", {})
        assert advisor.deferred_symbols == {"BTC", "ETH", "SOL"}

    def test_check_deferred_wait_cycles_not_ready(self):
        advisor = AIAdvisor(model="haiku")
        advisor.set_cycle(1)
        advisor.defer("BTC", "SHORT", {"wait_cycles": 3})
        advisor.set_cycle(2)
        ready = advisor.check_deferred({})
        assert "BTC" not in ready
        assert "BTC" in advisor.deferred_symbols

    def test_check_deferred_wait_cycles_ready(self):
        advisor = AIAdvisor(model="haiku")
        advisor.set_cycle(1)
        advisor.defer("BTC", "SHORT", {"wait_cycles": 3})
        advisor.set_cycle(5)
        ready = advisor.check_deferred({})
        assert "BTC" in ready
        assert "BTC" not in advisor.deferred_symbols  # removed after ready

    def test_check_deferred_price_above_not_ready(self):
        advisor = AIAdvisor(model="haiku")
        advisor.defer("BTC", "LONG", {"wait_until_price_above": 45000})
        ready = advisor.check_deferred({"BTC": 44000})
        assert "BTC" not in ready

    def test_check_deferred_price_above_ready(self):
        advisor = AIAdvisor(model="haiku")
        advisor.defer("BTC", "LONG", {"wait_until_price_above": 45000})
        ready = advisor.check_deferred({"BTC": 46000})
        assert "BTC" in ready

    def test_check_deferred_price_below_ready(self):
        advisor = AIAdvisor(model="haiku")
        advisor.defer("ETH", "SHORT", {"wait_until_price_below": 2000})
        ready = advisor.check_deferred({"ETH": 1900})
        assert "ETH" in ready

    def test_check_deferred_no_conditions_ready_immediately(self):
        advisor = AIAdvisor(model="haiku")
        advisor.defer("BTC", "LONG", {})
        ready = advisor.check_deferred({})
        assert "BTC" in ready

    def test_check_deferred_multiple_symbols(self):
        advisor = AIAdvisor(model="haiku")
        advisor.set_cycle(0)
        advisor.defer("BTC", "LONG", {"wait_cycles": 2})
        advisor.defer("ETH", "SHORT", {"wait_until_price_below": 2000})
        advisor.defer("SOL", "LONG", {"wait_until_price_above": 200})  # not ready
        advisor.set_cycle(5)
        ready = advisor.check_deferred({"ETH": 1900, "SOL": 150})
        assert "BTC" in ready
        assert "ETH" in ready
        assert "SOL" not in ready

    def test_set_cycle(self):
        advisor = AIAdvisor(model="haiku")
        advisor.set_cycle(10)
        assert advisor._cycle_count == 10


# ── CLI invocation (mocked) ─────────────────────────────────


def _make_mock_process(stdout: str, returncode: int = 0, stderr: str = ""):
    proc = AsyncMock()
    proc.communicate = AsyncMock(
        return_value=(stdout.encode(), stderr.encode())
    )
    proc.returncode = returncode
    return proc


class TestConsultMocked:
    @pytest.mark.asyncio
    async def test_consult_returns_structured_output(self):
        response = {
            "result": "some text",
            "structured_output": {
                "positions": [{"symbol": "ETH", "action": "HOLD", "reasoning": "ok"}],
                "opportunities": [],
            },
        }
        proc = _make_mock_process(json.dumps(response))

        with patch("asyncio.create_subprocess_exec", return_value=proc):
            advisor = AIAdvisor(model="haiku", timeout=10)
            result = await advisor.consult(
                positions=[{"symbol": "ETH"}],
                opportunities=[],
                account={"balance_usdc": 1000},
            )

        assert len(result["positions"]) == 1
        assert result["positions"][0]["action"] == "HOLD"
        assert result["opportunities"] == []

    @pytest.mark.asyncio
    async def test_consult_handles_timeout(self):
        async def slow_exec(*args, **kwargs):
            await asyncio.sleep(10)

        with patch("asyncio.create_subprocess_exec", side_effect=slow_exec):
            advisor = AIAdvisor(model="haiku", timeout=0.01)
            result = await advisor.consult(
                positions=[], opportunities=[], account={"balance_usdc": 1000}
            )

        assert result == {"positions": [], "opportunities": []}

    @pytest.mark.asyncio
    async def test_consult_handles_cli_error(self):
        proc = _make_mock_process("", returncode=1, stderr="CLI error")

        with patch("asyncio.create_subprocess_exec", return_value=proc):
            advisor = AIAdvisor(model="haiku", timeout=10)
            result = await advisor.consult(
                positions=[], opportunities=[], account={"balance_usdc": 1000}
            )

        assert result == {"positions": [], "opportunities": []}

    @pytest.mark.asyncio
    async def test_consult_handles_empty_output(self):
        proc = _make_mock_process("")

        with patch("asyncio.create_subprocess_exec", return_value=proc):
            advisor = AIAdvisor(model="haiku", timeout=10)
            result = await advisor.consult(
                positions=[], opportunities=[], account={"balance_usdc": 1000}
            )

        assert result == {"positions": [], "opportunities": []}

    @pytest.mark.asyncio
    async def test_consult_handles_invalid_json(self):
        proc = _make_mock_process("not json at all")

        with patch("asyncio.create_subprocess_exec", return_value=proc):
            advisor = AIAdvisor(model="haiku", timeout=10)
            result = await advisor.consult(
                positions=[], opportunities=[], account={"balance_usdc": 1000}
            )

        assert result == {"positions": [], "opportunities": []}

    @pytest.mark.asyncio
    async def test_consult_skips_if_nothing(self):
        response = {"positions": [], "opportunities": []}
        proc = _make_mock_process(json.dumps(response))

        with patch("asyncio.create_subprocess_exec", return_value=proc):
            advisor = AIAdvisor(model="haiku", timeout=10)
            result = await advisor.consult(
                positions=[], opportunities=[], account={"balance_usdc": 1000}
            )

        assert result["positions"] == []
        assert result["opportunities"] == []

    @pytest.mark.asyncio
    async def test_consult_passes_recent_trades(self):
        response = {"positions": [], "opportunities": []}
        proc = _make_mock_process(json.dumps(response))
        trades = [{"symbol": "ETH", "pnl": 5.0}]

        with patch("asyncio.create_subprocess_exec", return_value=proc) as mock_exec:
            advisor = AIAdvisor(model="haiku", timeout=10)
            result = await advisor.consult(
                positions=[],
                opportunities=[],
                account={"balance_usdc": 1000},
                recent_trades=trades,
            )

        # Verify the prompt includes recent_trades
        call_args = mock_exec.call_args
        prompt_arg = call_args[0][2]  # -p <prompt>
        assert "recent_trades" in prompt_arg

    @pytest.mark.asyncio
    async def test_consult_parses_structured_output_field(self):
        response = {
            "result": "some markdown text",
            "structured_output": {
                "positions": [{"symbol": "BTC", "action": "CLOSE", "reasoning": "TP hit"}],
                "opportunities": [{"symbol": "SOL", "action": "BUY", "reasoning": "dip"}],
            },
        }
        proc = _make_mock_process(json.dumps(response))

        with patch("asyncio.create_subprocess_exec", return_value=proc):
            advisor = AIAdvisor(model="haiku", timeout=10)
            result = await advisor.consult(
                positions=[{"symbol": "BTC"}],
                opportunities=[{"symbol": "SOL"}],
                account={"balance_usdc": 1000},
            )

        assert result["positions"][0]["symbol"] == "BTC"
        assert result["opportunities"][0]["symbol"] == "SOL"

    @pytest.mark.asyncio
    async def test_consult_parses_result_field(self):
        response = {
            "result": {
                "positions": [],
                "opportunities": [{"symbol": "ETH", "action": "SHORT", "reasoning": "overb."}],
            }
        }
        proc = _make_mock_process(json.dumps(response))

        with patch("asyncio.create_subprocess_exec", return_value=proc):
            advisor = AIAdvisor(model="haiku", timeout=10)
            result = await advisor.consult(
                positions=[], opportunities=[{"symbol": "ETH"}],
                account={"balance_usdc": 1000},
            )

        assert len(result["opportunities"]) == 1

    @pytest.mark.asyncio
    async def test_consult_parses_raw_dict(self):
        response = {
            "positions": [{"symbol": "ETH", "action": "HOLD", "reasoning": "stable"}],
            "opportunities": [],
        }
        proc = _make_mock_process(json.dumps(response))

        with patch("asyncio.create_subprocess_exec", return_value=proc):
            advisor = AIAdvisor(model="haiku", timeout=10)
            result = await advisor.consult(
                positions=[{"symbol": "ETH"}],
                opportunities=[],
                account={"balance_usdc": 1000},
            )

        assert result["positions"][0]["action"] == "HOLD"

    @pytest.mark.asyncio
    async def test_consult_parses_result_json_string(self):
        """When result is a JSON string (not dict), it should be parsed."""
        inner = {"positions": [], "opportunities": []}
        response = {"result": json.dumps(inner)}
        proc = _make_mock_process(json.dumps(response))

        with patch("asyncio.create_subprocess_exec", return_value=proc):
            advisor = AIAdvisor(model="haiku", timeout=10)
            result = await advisor.consult(
                positions=[], opportunities=[],
                account={"balance_usdc": 1000},
            )

        assert result["positions"] == []
        assert result["opportunities"] == []
