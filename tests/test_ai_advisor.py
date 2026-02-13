"""Tests for core.ai_advisor.AIAdvisor."""
from __future__ import annotations

import asyncio
import json
import subprocess
from unittest.mock import patch

import pytest
import pytest_asyncio

from config.settings import AIConfig
from core.ai_advisor import AIAdvisor, _validate_response
from core.cli_cursor import _strip_markdown_fences


# ── Deferred opportunity tracking ───────────────────────────


class TestDeferredTracking:
    @pytest.mark.asyncio
    async def test_defer_adds_symbol(self):
        advisor = AIAdvisor(model="haiku")
        await advisor.defer("BTC", "SHORT", {"wait_cycles": 3}, "mean_reversion")
        assert "BTC" in advisor.deferred_symbols
        assert ("BTC", "mean_reversion") in advisor.deferred_keys

    @pytest.mark.asyncio
    async def test_defer_replaces_existing_same_strategy(self):
        advisor = AIAdvisor(model="haiku")
        await advisor.defer("BTC", "SHORT", {"wait_cycles": 3}, "mr")
        await advisor.defer("BTC", "LONG", {"wait_cycles": 5}, "mr")
        assert "BTC" in advisor.deferred_symbols
        # Latest conditions win — check via check_deferred behavior
        advisor.set_cycle(4)
        ready = await advisor.check_deferred({})
        ready_syms = {k[0] for k in ready}
        assert "BTC" not in ready_syms  # 4 - 0 = 4 < 5

    @pytest.mark.asyncio
    async def test_defer_different_strategies_coexist(self):
        advisor = AIAdvisor(model="haiku")
        await advisor.defer("BTC", "SHORT", {"wait_cycles": 3}, "mr")
        await advisor.defer("BTC", "LONG", {"wait_cycles": 5}, "tf")
        assert len(advisor.deferred_keys) == 2
        assert advisor.is_deferred("BTC", "mr")
        assert advisor.is_deferred("BTC", "tf")

    @pytest.mark.asyncio
    async def test_remove_deferred_specific_strategy(self):
        advisor = AIAdvisor(model="haiku")
        await advisor.defer("BTC", "SHORT", {"wait_cycles": 3}, "mr")
        await advisor.defer("BTC", "LONG", {"wait_cycles": 5}, "tf")
        await advisor.remove_deferred("BTC", "mr")
        assert not advisor.is_deferred("BTC", "mr")
        assert advisor.is_deferred("BTC", "tf")

    @pytest.mark.asyncio
    async def test_remove_deferred_all_for_symbol(self):
        advisor = AIAdvisor(model="haiku")
        await advisor.defer("BTC", "SHORT", {}, "mr")
        await advisor.defer("BTC", "LONG", {}, "tf")
        await advisor.remove_deferred("BTC")
        assert "BTC" not in advisor.deferred_symbols

    @pytest.mark.asyncio
    async def test_remove_nonexistent_no_error(self):
        advisor = AIAdvisor(model="haiku")
        await advisor.remove_deferred("XYZ", "mr")  # should not raise

    @pytest.mark.asyncio
    async def test_deferred_symbols_property(self):
        advisor = AIAdvisor(model="haiku")
        await advisor.defer("BTC", "SHORT", {}, "mr")
        await advisor.defer("ETH", "LONG", {}, "mr")
        await advisor.defer("SOL", "LONG", {}, "tf")
        assert advisor.deferred_symbols == {"BTC", "ETH", "SOL"}

    @pytest.mark.asyncio
    async def test_is_deferred(self):
        advisor = AIAdvisor(model="haiku")
        await advisor.defer("BTC", "SHORT", {}, "mr")
        assert advisor.is_deferred("BTC", "mr")
        assert not advisor.is_deferred("BTC", "tf")
        assert not advisor.is_deferred("ETH", "mr")

    @pytest.mark.asyncio
    async def test_get_deferred_action_with_strategy(self):
        advisor = AIAdvisor(model="haiku")
        await advisor.defer("BTC", "SHORT", {}, "mr")
        assert advisor.get_deferred_action("BTC", "mr") == "SHORT"
        assert advisor.get_deferred_action("BTC", "tf") is None

    @pytest.mark.asyncio
    async def test_check_deferred_wait_cycles_not_ready(self):
        advisor = AIAdvisor(model="haiku")
        advisor.set_cycle(1)
        await advisor.defer("BTC", "SHORT", {"wait_cycles": 3})
        advisor.set_cycle(2)
        ready = await advisor.check_deferred({})
        ready_syms = {k[0] for k in ready}
        assert "BTC" not in ready_syms
        assert "BTC" in advisor.deferred_symbols

    @pytest.mark.asyncio
    async def test_check_deferred_wait_cycles_ready(self):
        advisor = AIAdvisor(model="haiku")
        advisor.set_cycle(1)
        await advisor.defer("BTC", "SHORT", {"wait_cycles": 3})
        advisor.set_cycle(5)
        ready = await advisor.check_deferred({})
        ready_syms = {k[0] for k in ready}
        assert "BTC" in ready_syms
        assert "BTC" not in advisor.deferred_symbols  # removed after ready

    @pytest.mark.asyncio
    async def test_check_deferred_price_above_not_ready(self):
        advisor = AIAdvisor(model="haiku")
        await advisor.defer("BTC", "LONG", {"wait_until_price_above": 45000})
        ready = await advisor.check_deferred({"BTC": 44000})
        ready_syms = {k[0] for k in ready}
        assert "BTC" not in ready_syms

    @pytest.mark.asyncio
    async def test_check_deferred_price_above_ready(self):
        advisor = AIAdvisor(model="haiku")
        await advisor.defer("BTC", "LONG", {"wait_until_price_above": 45000})
        ready = await advisor.check_deferred({"BTC": 46000})
        ready_syms = {k[0] for k in ready}
        assert "BTC" in ready_syms

    @pytest.mark.asyncio
    async def test_check_deferred_price_below_ready(self):
        advisor = AIAdvisor(model="haiku")
        await advisor.defer("ETH", "SHORT", {"wait_until_price_below": 2000})
        ready = await advisor.check_deferred({"ETH": 1900})
        ready_syms = {k[0] for k in ready}
        assert "ETH" in ready_syms

    @pytest.mark.asyncio
    async def test_check_deferred_no_conditions_ready_immediately(self):
        advisor = AIAdvisor(model="haiku")
        await advisor.defer("BTC", "LONG", {})
        ready = await advisor.check_deferred({})
        ready_syms = {k[0] for k in ready}
        assert "BTC" in ready_syms

    @pytest.mark.asyncio
    async def test_check_deferred_multiple_symbols(self):
        advisor = AIAdvisor(model="haiku")
        advisor.set_cycle(0)
        await advisor.defer("BTC", "LONG", {"wait_cycles": 2})
        await advisor.defer("ETH", "SHORT", {"wait_until_price_below": 2000})
        await advisor.defer("SOL", "LONG", {"wait_until_price_above": 200})  # not ready
        advisor.set_cycle(5)
        ready = await advisor.check_deferred({"ETH": 1900, "SOL": 150})
        ready_syms = {k[0] for k in ready}
        assert "BTC" in ready_syms
        assert "ETH" in ready_syms
        assert "SOL" not in ready_syms

    def test_set_cycle(self):
        advisor = AIAdvisor(model="haiku")
        advisor.set_cycle(10)
        assert advisor._cycle_count == 10


class TestDeferredHolds:
    @pytest.mark.asyncio
    async def test_defer_hold_adds_symbol(self):
        advisor = AIAdvisor(model="haiku")
        await advisor.defer_hold("SOL", {"wait_cycles": 5})
        assert "SOL" in advisor.deferred_hold_symbols

    @pytest.mark.asyncio
    async def test_defer_hold_separate_from_opportunities(self):
        advisor = AIAdvisor(model="haiku")
        await advisor.defer("SOL", "BUY", {"wait_cycles": 3})
        await advisor.defer_hold("ETH", {"wait_cycles": 5})
        assert advisor.deferred_symbols == {"SOL"}
        assert advisor.deferred_hold_symbols == {"ETH"}

    @pytest.mark.asyncio
    async def test_check_deferred_holds_wait_cycles(self):
        advisor = AIAdvisor(model="haiku")
        advisor.set_cycle(1)
        await advisor.defer_hold("SOL", {"wait_cycles": 5})
        advisor.set_cycle(3)
        ready = await advisor.check_deferred_holds({})
        assert "SOL" not in ready
        advisor.set_cycle(7)
        ready = await advisor.check_deferred_holds({})
        assert "SOL" in ready
        assert "SOL" not in advisor.deferred_hold_symbols

    @pytest.mark.asyncio
    async def test_check_deferred_holds_price(self):
        advisor = AIAdvisor(model="haiku")
        await advisor.defer_hold("BTC", {"wait_until_price_below": 58000})
        ready = await advisor.check_deferred_holds({"BTC": 59000})
        assert "BTC" not in ready
        ready = await advisor.check_deferred_holds({"BTC": 57000})
        assert "BTC" in ready

    @pytest.mark.asyncio
    async def test_remove_deferred_hold(self):
        advisor = AIAdvisor(model="haiku")
        await advisor.defer_hold("SOL", {"wait_cycles": 5})
        await advisor.remove_deferred_hold("SOL")
        assert "SOL" not in advisor.deferred_hold_symbols

    @pytest.mark.asyncio
    async def test_remove_deferred_hold_nonexistent(self):
        advisor = AIAdvisor(model="haiku")
        await advisor.remove_deferred_hold("XYZ")  # no error


class TestDeferredSummary:
    @pytest.mark.asyncio
    async def test_empty_summary(self):
        advisor = AIAdvisor(model="haiku")
        assert advisor.get_deferred_summary() == []

    @pytest.mark.asyncio
    async def test_summary_includes_opportunities_and_holds(self):
        advisor = AIAdvisor(model="haiku")
        advisor.set_cycle(10)
        await advisor.defer("ETH", "SHORT", {"wait_cycles": 3})
        await advisor.defer("BTC", "SHORT", {"wait_until_price_below": 55000})
        await advisor.defer_hold("SOL", {"wait_cycles": 5, "wait_until_price_above": 200})
        advisor.set_cycle(12)

        summary = advisor.get_deferred_summary()
        assert len(summary) == 3

        eth = next(s for s in summary if s["symbol"] == "ETH")
        assert eth["action"] == "SHORT"
        assert eth["type"] == "opportunity"
        assert eth["deferred_cycles_ago"] == 2
        assert eth["wait_cycles"] == 3

        btc = next(s for s in summary if s["symbol"] == "BTC")
        assert btc["action"] == "SHORT"
        assert btc["wait_until_price_below"] == 55000
        assert "wait_cycles" not in btc

        sol = next(s for s in summary if s["symbol"] == "SOL")
        assert sol["action"] == "HOLD"
        assert sol["type"] == "position_hold"
        assert sol["wait_until_price_above"] == 200

    @pytest.mark.asyncio
    async def test_summary_after_removal(self):
        advisor = AIAdvisor(model="haiku")
        await advisor.defer("ETH", "SHORT", {"wait_cycles": 3})
        await advisor.defer("BTC", "BUY", {"wait_cycles": 5})
        await advisor.remove_deferred("ETH")
        summary = advisor.get_deferred_summary()
        assert len(summary) == 1
        assert summary[0]["symbol"] == "BTC"


# ── Deferred persistence (with DB) ──────────────────────────


class TestDeferredPersistence:
    @pytest.mark.asyncio
    async def test_defer_persists_to_db(self, db):
        advisor = AIAdvisor(model="haiku", db=db)
        advisor.set_cycle(5)
        await advisor.defer("BTC", "SHORT", {"wait_cycles": 3}, "mean_reversion")

        rows = await db.get_all_deferred("opportunity")
        assert len(rows) == 1
        assert rows[0]["symbol"] == "BTC"
        assert rows[0]["original_action"] == "SHORT"
        assert rows[0]["conditions"] == {"wait_cycles": 3}
        assert rows[0]["strategy_type"] == "mean_reversion"

    @pytest.mark.asyncio
    async def test_defer_hold_persists_to_db(self, db):
        advisor = AIAdvisor(model="haiku", db=db)
        advisor.set_cycle(1)
        await advisor.defer_hold("ETH", {"wait_until_price_below": 2000})

        rows = await db.get_all_deferred("hold")
        assert len(rows) == 1
        assert rows[0]["symbol"] == "ETH"
        assert rows[0]["original_action"] == "HOLD"

    @pytest.mark.asyncio
    async def test_remove_deferred_specific_strategy_from_db(self, db):
        advisor = AIAdvisor(model="haiku", db=db)
        await advisor.defer("BTC", "SHORT", {"wait_cycles": 3}, "mr")
        await advisor.defer("BTC", "LONG", {"wait_cycles": 5}, "tf")
        await advisor.remove_deferred("BTC", "mr")

        rows = await db.get_all_deferred("opportunity")
        assert len(rows) == 1
        assert rows[0]["strategy_type"] == "tf"

    @pytest.mark.asyncio
    async def test_remove_deferred_all_for_symbol_from_db(self, db):
        advisor = AIAdvisor(model="haiku", db=db)
        await advisor.defer("BTC", "SHORT", {"wait_cycles": 3}, "mr")
        await advisor.defer("BTC", "LONG", {"wait_cycles": 5}, "tf")
        await advisor.remove_deferred("BTC")

        rows = await db.get_all_deferred("opportunity")
        assert len(rows) == 0

    @pytest.mark.asyncio
    async def test_remove_deferred_hold_deletes_from_db(self, db):
        advisor = AIAdvisor(model="haiku", db=db)
        await advisor.defer_hold("SOL", {"wait_cycles": 5})
        await advisor.remove_deferred_hold("SOL")

        rows = await db.get_all_deferred("hold")
        assert len(rows) == 0

    @pytest.mark.asyncio
    async def test_check_deferred_cleans_db(self, db):
        advisor = AIAdvisor(model="haiku", db=db)
        advisor.set_cycle(0)
        await advisor.defer("BTC", "SHORT", {"wait_cycles": 2}, "mr")
        advisor.set_cycle(5)
        ready = await advisor.check_deferred({})
        ready_syms = {k[0] for k in ready}
        assert "BTC" in ready_syms

        rows = await db.get_all_deferred("opportunity")
        assert len(rows) == 0

    @pytest.mark.asyncio
    async def test_load_deferred_clears_stale_on_startup(self, db):
        # Manually insert into DB (simulating previous session)
        await db.upsert_deferred("BTC", "SHORT", 3, {"wait_cycles": 5}, "opportunity", "mr")
        await db.upsert_deferred("ETH", "HOLD", 2, {"wait_until_price_below": 2000}, "hold", "unknown")

        advisor = AIAdvisor(model="haiku", db=db)
        await advisor.load_deferred()

        # All deferred should be cleared on startup
        assert len(advisor.deferred_symbols) == 0
        assert len(advisor.deferred_hold_symbols) == 0

        # DB should also be empty
        rows = await db.get_all_deferred()
        assert len(rows) == 0

    @pytest.mark.asyncio
    async def test_load_deferred_empty_db(self, db):
        advisor = AIAdvisor(model="haiku", db=db)
        await advisor.load_deferred()
        assert len(advisor.deferred_symbols) == 0
        assert len(advisor.deferred_hold_symbols) == 0


# ── CLI invocation (mocked) ─────────────────────────────────


def _mock_run_factory(stdout: str, returncode: int = 0, stderr: str = ""):
    """Return a callable that returns a CompletedProcess (for side_effect)."""
    result = subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)
    def mock_run(*args, **kwargs):
        return result
    return mock_run


class TestConsultMocked:
    @pytest.mark.asyncio
    async def test_consult_returns_structured_output(self):
        """Single CLI call returns batch response with positions and opportunities."""
        batch = {
            "positions": [{"symbol": "ETH", "action": "HOLD", "reasoning": "ok"}],
            "opportunities": [],
        }
        response = {"result": "text", "structured_output": batch}

        with patch("core.cli_claude.subprocess.run", side_effect=_mock_run_factory(json.dumps(response))):
            advisor = AIAdvisor(model="haiku", timeout=10)
            result = await advisor.consult(
                positions=[{"symbol": "ETH", "direction": "LONG", "pnl_pct": 1.0}],
                opportunities=[],
                account={"balance_usdc": 1000},
            )

        assert len(result["positions"]) == 1
        assert result["positions"][0]["action"] == "HOLD"
        assert result["positions"][0]["symbol"] == "ETH"
        assert result["opportunities"] == []

    @pytest.mark.asyncio
    async def test_consult_handles_timeout(self):
        def slow_run(*args, **kwargs):
            raise subprocess.TimeoutExpired(cmd="claude", timeout=1)

        with patch("core.cli_claude.subprocess.run", side_effect=slow_run):
            advisor = AIAdvisor(model="haiku", timeout=1)
            result = await advisor.consult(
                positions=[{"symbol": "ETH", "direction": "LONG", "pnl_pct": 0}],
                opportunities=[], account={"balance_usdc": 1000}
            )

        assert result == {"positions": [], "opportunities": []}

    @pytest.mark.asyncio
    async def test_consult_handles_cli_error(self):
        with patch("core.cli_claude.subprocess.run", side_effect=_mock_run_factory("", returncode=1, stderr="CLI error")):
            advisor = AIAdvisor(model="haiku", timeout=10)
            result = await advisor.consult(
                positions=[{"symbol": "ETH", "direction": "LONG", "pnl_pct": 0}],
                opportunities=[], account={"balance_usdc": 1000}
            )

        assert result == {"positions": [], "opportunities": []}

    @pytest.mark.asyncio
    async def test_consult_batch_response(self):
        """Batch response with multiple positions and opportunities."""
        batch = {
            "positions": [],
            "opportunities": [
                {"symbol": "BTC", "action": "SHORT", "reasoning": "bearish"},
                {"symbol": "ETH", "action": "SHORT", "reasoning": "bearish"},
            ],
        }
        response = {"structured_output": batch}

        with patch("core.cli_claude.subprocess.run", side_effect=_mock_run_factory(json.dumps(response))):
            advisor = AIAdvisor(model="haiku", timeout=10)
            result = await advisor.consult(
                positions=[],
                opportunities=[
                    {"symbol": "BTC", "proposed_action": "SHORT", "confidence": 0.75},
                    {"symbol": "ETH", "proposed_action": "SHORT", "confidence": 0.65},
                ],
                account={"balance_usdc": 1000},
            )

        assert len(result["opportunities"]) == 2
        assert all(o["action"] == "SHORT" for o in result["opportunities"])

    @pytest.mark.asyncio
    async def test_consult_empty_returns_empty(self):
        advisor = AIAdvisor(model="haiku", timeout=10)
        result = await advisor.consult(
            positions=[], opportunities=[], account={"balance_usdc": 1000}
        )
        assert result == {"positions": [], "opportunities": []}

    @pytest.mark.asyncio
    async def test_consult_handles_empty_output(self):
        with patch("core.cli_claude.subprocess.run", side_effect=_mock_run_factory("")):
            advisor = AIAdvisor(model="haiku", timeout=10)
            result = await advisor.consult(
                positions=[{"symbol": "ETH", "direction": "LONG", "pnl_pct": 0}],
                opportunities=[], account={"balance_usdc": 1000}
            )

        assert result == {"positions": [], "opportunities": []}

    @pytest.mark.asyncio
    async def test_consult_handles_invalid_json(self):
        with patch("core.cli_claude.subprocess.run", side_effect=_mock_run_factory("not json at all")):
            advisor = AIAdvisor(model="haiku", timeout=10)
            result = await advisor.consult(
                positions=[{"symbol": "ETH", "direction": "LONG", "pnl_pct": 0}],
                opportunities=[], account={"balance_usdc": 1000}
            )

        assert result == {"positions": [], "opportunities": []}

    @pytest.mark.asyncio
    async def test_consult_passes_recent_trades(self):
        """Recent trades are included in the payload."""
        batch = {"positions": [], "opportunities": [{"symbol": "ETH", "action": "BUY", "reasoning": "go"}]}
        response = {"structured_output": batch}
        trades = [{"symbol": "ETH", "pnl": 5.0}]

        with patch("core.cli_claude.subprocess.run", side_effect=_mock_run_factory(json.dumps(response))) as mock_run:
            advisor = AIAdvisor(model="haiku", timeout=10)
            await advisor.consult(
                positions=[],
                opportunities=[{"symbol": "ETH", "proposed_action": "BUY", "confidence": 0.7}],
                account={"balance_usdc": 1000},
                recent_trades=trades,
            )

        call_args = mock_run.call_args
        cmd_list = call_args[0][0]
        prompt_arg = cmd_list[2]  # -p <prompt>
        assert "recent_trades" in prompt_arg

    @pytest.mark.asyncio
    async def test_consult_parses_structured_output_field(self):
        """structured_output contains the batch response."""
        batch = {
            "positions": [{"symbol": "BTC", "action": "CLOSE", "reasoning": "TP hit"}],
            "opportunities": [{"symbol": "SOL", "action": "BUY", "reasoning": "go"}],
        }
        response = {"result": "some markdown text", "structured_output": batch}

        with patch("core.cli_claude.subprocess.run", side_effect=_mock_run_factory(json.dumps(response))):
            advisor = AIAdvisor(model="haiku", timeout=10)
            result = await advisor.consult(
                positions=[{"symbol": "BTC", "direction": "LONG", "pnl_pct": 2.0}],
                opportunities=[{"symbol": "SOL", "proposed_action": "BUY", "confidence": 0.7}],
                account={"balance_usdc": 1000},
            )

        assert result["positions"][0]["symbol"] == "BTC"
        assert result["positions"][0]["action"] == "CLOSE"
        assert result["opportunities"][0]["symbol"] == "SOL"

    @pytest.mark.asyncio
    async def test_consult_parses_result_dict(self):
        batch = {
            "positions": [],
            "opportunities": [{"symbol": "ETH", "action": "SHORT", "reasoning": "overb."}],
        }
        response = {"result": batch}

        with patch("core.cli_claude.subprocess.run", side_effect=_mock_run_factory(json.dumps(response))):
            advisor = AIAdvisor(model="haiku", timeout=10)
            result = await advisor.consult(
                positions=[], opportunities=[{"symbol": "ETH", "proposed_action": "SHORT", "confidence": 0.6}],
                account={"balance_usdc": 1000},
            )

        assert result["opportunities"][0]["action"] == "SHORT"

    @pytest.mark.asyncio
    async def test_consult_parses_result_json_string(self):
        """When result is a JSON string (not dict), it should be parsed."""
        batch = {"positions": [{"symbol": "ETH", "action": "HOLD", "reasoning": "wait"}], "opportunities": []}
        response = {"result": json.dumps(batch)}

        with patch("core.cli_claude.subprocess.run", side_effect=_mock_run_factory(json.dumps(response))):
            advisor = AIAdvisor(model="haiku", timeout=10)
            result = await advisor.consult(
                positions=[{"symbol": "ETH", "direction": "LONG", "pnl_pct": 0}],
                opportunities=[],
                account={"balance_usdc": 1000},
            )

        assert result["positions"][0]["action"] == "HOLD"


# ── Markdown fence stripping (cursor backend) ─────────────


class TestStripMarkdownFences:
    def test_json_fence(self):
        text = '```json\n{"positions": [], "opportunities": []}\n```'
        assert json.loads(_strip_markdown_fences(text)) == {"positions": [], "opportunities": []}

    def test_plain_fence(self):
        text = '```\n{"positions": []}\n```'
        assert json.loads(_strip_markdown_fences(text)) == {"positions": []}

    def test_JSON_uppercase_fence(self):
        text = '```JSON\n{"positions": []}\n```'
        assert json.loads(_strip_markdown_fences(text)) == {"positions": []}

    def test_text_before_and_after_fence(self):
        text = 'Here is my response:\n\n```json\n{"positions": [], "opportunities": []}\n```\n\nHope this helps!'
        result = json.loads(_strip_markdown_fences(text))
        assert result == {"positions": [], "opportunities": []}

    def test_no_fence_raw_json(self):
        text = '{"positions": [], "opportunities": []}'
        assert json.loads(_strip_markdown_fences(text)) == {"positions": [], "opportunities": []}

    def test_no_fence_with_preamble(self):
        text = 'Here is the JSON:\n{"positions": [], "opportunities": []}'
        result = json.loads(_strip_markdown_fences(text))
        assert result == {"positions": [], "opportunities": []}

    def test_no_fence_with_postamble(self):
        text = '{"positions": [], "opportunities": []}\nDone.'
        result = json.loads(_strip_markdown_fences(text))
        assert result == {"positions": [], "opportunities": []}

    def test_nested_braces(self):
        text = 'blah {"positions": [{"symbol": "ETH", "action": "HOLD", "reasoning": "ok"}], "opportunities": []} blah'
        result = json.loads(_strip_markdown_fences(text))
        assert result["positions"][0]["symbol"] == "ETH"

    def test_empty_string(self):
        assert _strip_markdown_fences("") == ""

    def test_no_json_at_all(self):
        text = "Just some plain text without any JSON"
        # Should return stripped text (will fail json.loads but that's caller's job)
        assert _strip_markdown_fences(text) == text.strip()


# ── Cursor backend (mocked) ──────────────────────────────


def _cursor_wrapper(inner, *, is_error: bool = False, fences: bool = False, preamble: str = "") -> str:
    """Build a realistic cursor agent JSON output wrapper."""
    if isinstance(inner, dict):
        result_str = json.dumps(inner)
    else:
        result_str = inner
    if fences:
        result_str = f"```json\n{result_str}\n```"
    if preamble:
        result_str = f"{preamble}\n\n{result_str}"
    wrapper = {
        "type": "result",
        "subtype": "error_model" if is_error else "success",
        "is_error": is_error,
        "duration_ms": 3000,
        "duration_api_ms": 3000,
        "result": result_str,
        "session_id": "test-session",
        "request_id": "test-request",
    }
    return json.dumps(wrapper)


class TestConsultCursor:
    @pytest.mark.asyncio
    async def test_cursor_result_wrapper(self):
        """Cursor wraps output in full wrapper with subtype/is_error/duration."""
        inner = {"positions": [], "opportunities": [{"symbol": "BTC", "action": "SHORT", "reasoning": "trend"}]}
        stdout = _cursor_wrapper(inner)

        with patch("core.cli_cursor.subprocess.run", side_effect=_mock_run_factory(stdout)):
            advisor = AIAdvisor(config=AIConfig(advisor="cursor", model="opus-4.6-thinking", timeout=10))
            result = await advisor.consult(
                positions=[],
                opportunities=[{"symbol": "BTC", "proposed_action": "SHORT", "confidence": 0.8}],
                account={"balance_usdc": 1000},
            )

        assert len(result["opportunities"]) == 1
        assert result["opportunities"][0]["action"] == "SHORT"

    @pytest.mark.asyncio
    async def test_cursor_result_with_markdown_fences(self):
        """Cursor result string wrapped in markdown fences (thinking models)."""
        inner = {"positions": [{"symbol": "ETH", "action": "CLOSE", "reasoning": "reversal"}], "opportunities": []}
        stdout = _cursor_wrapper(inner, fences=True)

        with patch("core.cli_cursor.subprocess.run", side_effect=_mock_run_factory(stdout)):
            advisor = AIAdvisor(config=AIConfig(advisor="cursor", model="test", timeout=10))
            result = await advisor.consult(
                positions=[{"symbol": "ETH", "direction": "LONG", "pnl_pct": -1.0}],
                opportunities=[],
                account={"balance_usdc": 1000},
            )

        assert result["positions"][0]["action"] == "CLOSE"

    @pytest.mark.asyncio
    async def test_cursor_result_with_commentary_around_fences(self):
        """Cursor adds text before/after fences."""
        inner = {"positions": [], "opportunities": [{"symbol": "SOL", "action": "BUY", "reasoning": "oversold"}]}
        stdout = _cursor_wrapper(inner, fences=True, preamble="Based on analysis:")

        with patch("core.cli_cursor.subprocess.run", side_effect=_mock_run_factory(stdout)):
            advisor = AIAdvisor(config=AIConfig(advisor="cursor", model="test", timeout=10))
            result = await advisor.consult(
                positions=[],
                opportunities=[{"symbol": "SOL", "proposed_action": "BUY", "confidence": 0.7}],
                account={"balance_usdc": 1000},
            )

        assert result["opportunities"][0]["symbol"] == "SOL"
        assert result["opportunities"][0]["action"] == "BUY"

    @pytest.mark.asyncio
    async def test_cursor_result_dict_not_string(self):
        """Cursor returns result as dict directly (no string wrapping)."""
        inner = {"positions": [], "opportunities": []}
        # Build wrapper manually since _cursor_wrapper stringifies
        wrapper = json.dumps({
            "type": "result", "subtype": "success", "is_error": False,
            "result": inner, "session_id": "t", "request_id": "t",
        })

        with patch("core.cli_cursor.subprocess.run", side_effect=_mock_run_factory(wrapper)):
            advisor = AIAdvisor(config=AIConfig(advisor="cursor", model="test", timeout=10))
            result = await advisor.consult(
                positions=[],
                opportunities=[{"symbol": "X", "proposed_action": "BUY", "confidence": 0.5}],
                account={"balance_usdc": 100},
            )

        assert result == {"positions": [], "opportunities": []}

    @pytest.mark.asyncio
    async def test_cursor_is_error_true(self):
        """Cursor returns is_error: true — graceful fallback."""
        stdout = _cursor_wrapper("Model error: rate limited", is_error=True)

        with patch("core.cli_cursor.subprocess.run", side_effect=_mock_run_factory(stdout)):
            advisor = AIAdvisor(config=AIConfig(advisor="cursor", model="test", timeout=10))
            result = await advisor.consult(
                positions=[{"symbol": "ETH", "direction": "LONG", "pnl_pct": 0}],
                opportunities=[],
                account={"balance_usdc": 1000},
            )

        assert result == {"positions": [], "opportunities": []}

    @pytest.mark.asyncio
    async def test_cursor_unparseable_result(self):
        """Cursor returns garbage in result string — graceful fallback."""
        stdout = _cursor_wrapper("I'm not sure what to do here, let me think...")

        with patch("core.cli_cursor.subprocess.run", side_effect=_mock_run_factory(stdout)):
            advisor = AIAdvisor(config=AIConfig(advisor="cursor", model="test", timeout=10))
            result = await advisor.consult(
                positions=[{"symbol": "ETH", "direction": "LONG", "pnl_pct": 0}],
                opportunities=[],
                account={"balance_usdc": 1000},
            )

        assert result == {"positions": [], "opportunities": []}

    @pytest.mark.asyncio
    async def test_cursor_timeout(self):
        def slow(*args, **kwargs):
            raise subprocess.TimeoutExpired(cmd="agent", timeout=1)

        with patch("core.cli_cursor.subprocess.run", side_effect=slow):
            advisor = AIAdvisor(config=AIConfig(advisor="cursor", model="test", timeout=1))
            result = await advisor.consult(
                positions=[{"symbol": "ETH", "direction": "LONG", "pnl_pct": 0}],
                opportunities=[],
                account={"balance_usdc": 1000},
            )

        assert result == {"positions": [], "opportunities": []}

    @pytest.mark.asyncio
    async def test_cursor_raw_json_no_fences(self):
        """Cursor returns raw JSON without fences but with preamble text."""
        inner = {"positions": [], "opportunities": [{"symbol": "BTC", "action": "BUY", "reasoning": "bounce"}]}
        stdout = _cursor_wrapper(inner, preamble="Here is my analysis:")

        with patch("core.cli_cursor.subprocess.run", side_effect=_mock_run_factory(stdout)):
            advisor = AIAdvisor(config=AIConfig(advisor="cursor", model="test", timeout=10))
            result = await advisor.consult(
                positions=[],
                opportunities=[{"symbol": "BTC", "proposed_action": "BUY", "confidence": 0.6}],
                account={"balance_usdc": 1000},
            )

        assert result["opportunities"][0]["action"] == "BUY"

    @pytest.mark.asyncio
    async def test_cursor_cli_error_exit_code(self):
        """Cursor exits with non-zero — fallback."""
        with patch("core.cli_cursor.subprocess.run", side_effect=_mock_run_factory("", returncode=1, stderr="model not found")):
            advisor = AIAdvisor(config=AIConfig(advisor="cursor", model="bad-model", timeout=10))
            result = await advisor.consult(
                positions=[{"symbol": "ETH", "direction": "LONG", "pnl_pct": 0}],
                opportunities=[],
                account={"balance_usdc": 1000},
            )

        assert result == {"positions": [], "opportunities": []}


# ── Response validation ───────────────────────────────────


class TestValidateResponse:
    def test_valid_response_unchanged(self):
        resp = {
            "positions": [{"symbol": "ETH", "action": "HOLD", "reasoning": "ok"}],
            "opportunities": [{"symbol": "BTC", "action": "BUY", "reasoning": "go"}],
        }
        result = _validate_response(resp)
        assert len(result["positions"]) == 1
        assert len(result["opportunities"]) == 1

    def test_drops_invalid_position_action(self):
        resp = {
            "positions": [
                {"symbol": "ETH", "action": "HOLD", "reasoning": "ok"},
                {"symbol": "BTC", "action": "BUY", "reasoning": "wrong"},  # BUY not valid for positions
            ],
            "opportunities": [],
        }
        result = _validate_response(resp)
        assert len(result["positions"]) == 1
        assert result["positions"][0]["symbol"] == "ETH"

    def test_drops_invalid_opportunity_action(self):
        resp = {
            "positions": [],
            "opportunities": [
                {"symbol": "ETH", "action": "BUY", "reasoning": "go"},
                {"symbol": "BTC", "action": "CLOSE", "reasoning": "bad"},  # CLOSE not valid for opportunities
            ],
        }
        result = _validate_response(resp)
        assert len(result["opportunities"]) == 1
        assert result["opportunities"][0]["symbol"] == "ETH"

    def test_drops_missing_symbol(self):
        resp = {
            "positions": [{"action": "HOLD", "reasoning": "ok"}],  # no symbol
            "opportunities": [],
        }
        result = _validate_response(resp)
        assert len(result["positions"]) == 0

    def test_drops_non_dict_items(self):
        resp = {
            "positions": ["not a dict", 42, None],
            "opportunities": [True],
        }
        result = _validate_response(resp)
        assert result["positions"] == []
        assert result["opportunities"] == []

    def test_missing_reasoning_gets_default(self):
        resp = {
            "positions": [{"symbol": "ETH", "action": "CLOSE"}],  # no reasoning
            "opportunities": [{"symbol": "BTC", "action": "SHORT"}],
        }
        result = _validate_response(resp)
        assert result["positions"][0]["reasoning"] == ""
        assert result["opportunities"][0]["reasoning"] == ""

    def test_positions_not_list_becomes_empty(self):
        resp = {"positions": "garbage", "opportunities": []}
        result = _validate_response(resp)
        assert result["positions"] == []

    def test_missing_keys_become_empty(self):
        resp = {"some_random_key": True}
        result = _validate_response(resp)
        assert result["positions"] == []
        assert result["opportunities"] == []

    def test_scale_up_valid_position_action(self):
        resp = {
            "positions": [{"symbol": "ETH", "action": "SCALE_UP", "reasoning": "momentum"}],
            "opportunities": [],
        }
        result = _validate_response(resp)
        assert len(result["positions"]) == 1
        assert result["positions"][0]["action"] == "SCALE_UP"

    def test_adjust_valid_position_action(self):
        resp = {
            "positions": [{"symbol": "SOL", "action": "ADJUST", "reasoning": "tighten SL", "adjustments": {"stop_loss": 150}}],
            "opportunities": [],
        }
        result = _validate_response(resp)
        assert len(result["positions"]) == 1
        assert result["positions"][0]["adjustments"]["stop_loss"] == 150

    def test_flip_valid_position_action(self):
        resp = {
            "positions": [{"symbol": "ETH", "action": "FLIP", "reasoning": "trend reversal",
                           "adjustments": {"stop_loss": 2050, "take_profit": 1900, "size_pct": 3.0, "leverage": 2}}],
            "opportunities": [],
        }
        result = _validate_response(resp)
        assert len(result["positions"]) == 1
        assert result["positions"][0]["action"] == "FLIP"
        assert result["positions"][0]["adjustments"]["stop_loss"] == 2050
