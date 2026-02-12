"""AI Advisor — Claude Code CLI integration for trading decisions.

Single CLI call per cycle. The AI acts as an advisor that reviews open positions
and proposed opportunities, responding with structured JSON.

Invocation:
    claude -p <payload_file> \
        --no-session-persistence \
        --model <model> \
        --output-format json \
        --json-schema <schema> \
        --system-prompt-file prompts/ai_advisor.md \
        --allowedTools "" \
        --max-turns 2

On error: logs a warning and returns empty response (bot continues autonomously).
"""
from __future__ import annotations

import asyncio
import json
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from data.db import Database
from utils.logger import get_logger

logger = get_logger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass
class DeferredOpportunity:
    """An opportunity the AI asked to re-evaluate later."""

    symbol: str
    original_action: str  # BUY or SHORT
    deferred_at_cycle: int
    conditions: dict[str, Any] = field(default_factory=dict)
    # conditions may include: wait_cycles, wait_until_price_above, wait_until_price_below


class AIAdvisor:
    """Claude Code CLI advisor for trading decisions."""

    def __init__(
        self,
        *,
        model: str = "opus",
        timeout: int = 120,
        db: Database | None = None,
    ) -> None:
        self._model = model
        self._timeout = timeout
        self._db = db
        self._schema_path = _PROJECT_ROOT / "schemas" / "ai_advisor_output.json"
        self._prompt_path = _PROJECT_ROOT / "prompts" / "ai_advisor.md"
        self._cycle_count: int = 0

        # Deferred opportunities (in-memory, synced to DB when available)
        self._deferred: dict[str, DeferredOpportunity] = {}
        # Deferred position holds (same structure, separate tracking)
        self._deferred_holds: dict[str, DeferredOpportunity] = {}

    @property
    def deferred_symbols(self) -> set[str]:
        """Symbols with deferred opportunities."""
        return set(self._deferred.keys())

    @property
    def deferred_hold_symbols(self) -> set[str]:
        """Symbols with deferred position holds."""
        return set(self._deferred_holds.keys())

    def set_cycle(self, cycle: int) -> None:
        """Update the current cycle count (called from main loop)."""
        self._cycle_count = cycle

    # ── Deferred opportunity management ───────────────────────

    async def defer(self, symbol: str, original_action: str, conditions: dict[str, Any]) -> None:
        """Defer an opportunity for later re-evaluation."""
        self._deferred[symbol] = DeferredOpportunity(
            symbol=symbol,
            original_action=original_action,
            deferred_at_cycle=self._cycle_count,
            conditions=conditions,
        )
        if self._db:
            try:
                await self._db.upsert_deferred(symbol, original_action, self._cycle_count, conditions, "opportunity")
            except Exception:
                logger.debug("Failed to persist deferred opportunity for %s", symbol, exc_info=True)
        logger.info(
            "Deferred %s %s — conditions: %s",
            original_action, symbol, conditions,
        )

    async def remove_deferred(self, symbol: str) -> None:
        """Remove a deferred opportunity (signal gone or conditions met)."""
        if symbol in self._deferred:
            del self._deferred[symbol]
            if self._db:
                try:
                    await self._db.delete_deferred(symbol, "opportunity")
                except Exception:
                    logger.debug("Failed to delete deferred opportunity for %s", symbol, exc_info=True)
            logger.debug("Removed deferred opportunity for %s", symbol)

    async def check_deferred(self, mid_prices: dict[str, float]) -> list[str]:
        """Check which deferred opportunities have met their conditions.

        Returns list of symbols ready for re-evaluation.
        """
        return await self._check_conditions(self._deferred, mid_prices, "opportunity")

    # ── Deferred position hold management ─────────────────────

    async def defer_hold(self, symbol: str, conditions: dict[str, Any]) -> None:
        """Defer re-evaluation of a position HOLD."""
        self._deferred_holds[symbol] = DeferredOpportunity(
            symbol=symbol,
            original_action="HOLD",
            deferred_at_cycle=self._cycle_count,
            conditions=conditions,
        )
        if self._db:
            try:
                await self._db.upsert_deferred(symbol, "HOLD", self._cycle_count, conditions, "hold")
            except Exception:
                logger.debug("Failed to persist deferred hold for %s", symbol, exc_info=True)
        logger.info("Deferred HOLD %s — conditions: %s", symbol, conditions)

    async def remove_deferred_hold(self, symbol: str) -> None:
        """Remove a deferred position hold (position closed or conditions met)."""
        if symbol in self._deferred_holds:
            del self._deferred_holds[symbol]
            if self._db:
                try:
                    await self._db.delete_deferred(symbol, "hold")
                except Exception:
                    logger.debug("Failed to delete deferred hold for %s", symbol, exc_info=True)

    async def check_deferred_holds(self, mid_prices: dict[str, float]) -> list[str]:
        """Check which deferred holds have met their conditions.

        Returns list of symbols ready for AI re-evaluation.
        """
        return await self._check_conditions(self._deferred_holds, mid_prices, "hold")

    # ── Load from DB (startup) ──────────────────────────────

    async def load_deferred(self) -> None:
        """Reload deferred opportunities and holds from DB at startup."""
        if not self._db:
            return
        try:
            rows = await self._db.get_all_deferred()
            for row in rows:
                opp = DeferredOpportunity(
                    symbol=row["symbol"],
                    original_action=row["original_action"],
                    deferred_at_cycle=row["deferred_at_cycle"],
                    conditions=row["conditions"],
                )
                if row["type"] == "hold":
                    self._deferred_holds[row["symbol"]] = opp
                else:
                    self._deferred[row["symbol"]] = opp
            total = len(self._deferred) + len(self._deferred_holds)
            if total:
                logger.info(
                    "Loaded %d deferred from DB (%d opportunities, %d holds)",
                    total, len(self._deferred), len(self._deferred_holds),
                )
        except Exception:
            logger.warning("Failed to load deferred from DB", exc_info=True)

    # ── Shared condition checker ────────────────────────────

    async def _check_conditions(
        self,
        store: dict[str, DeferredOpportunity],
        mid_prices: dict[str, float],
        label: str,
    ) -> list[str]:
        ready: list[str] = []
        to_remove: list[str] = []

        for symbol, opp in store.items():
            cond = opp.conditions
            met = False

            wait_cycles = cond.get("wait_cycles")
            if wait_cycles is not None:
                if self._cycle_count - opp.deferred_at_cycle >= wait_cycles:
                    met = True

            price = mid_prices.get(symbol)
            if price is not None:
                above = cond.get("wait_until_price_above")
                if above is not None and price >= above:
                    met = True
                below = cond.get("wait_until_price_below")
                if below is not None and price <= below:
                    met = True

            if not cond:
                met = True

            if met:
                ready.append(symbol)
                to_remove.append(symbol)

        for symbol in to_remove:
            del store[symbol]
            if self._db:
                try:
                    await self._db.delete_deferred(symbol, label)
                except Exception:
                    logger.debug("Failed to delete deferred %s %s from DB", label, symbol, exc_info=True)
            logger.info("Deferred %s %s conditions met — re-evaluating", label, symbol)

        return ready

    # ── Deferred summary (for AI context) ────────────────────

    def get_deferred_summary(self) -> list[dict[str, Any]]:
        """Build a structured summary of all deferred items for the AI payload.

        Returns a list of dicts with: symbol, action, deferred_since_cycles,
        and the original conditions.
        """
        items: list[dict[str, Any]] = []
        for sym, opp in self._deferred.items():
            age = self._cycle_count - opp.deferred_at_cycle
            item: dict[str, Any] = {
                "symbol": sym,
                "action": opp.original_action,
                "type": "opportunity",
                "deferred_cycles_ago": age,
            }
            if opp.conditions.get("wait_cycles") is not None:
                item["wait_cycles"] = opp.conditions["wait_cycles"]
            if opp.conditions.get("wait_until_price_above") is not None:
                item["wait_until_price_above"] = opp.conditions["wait_until_price_above"]
            if opp.conditions.get("wait_until_price_below") is not None:
                item["wait_until_price_below"] = opp.conditions["wait_until_price_below"]
            items.append(item)

        for sym, opp in self._deferred_holds.items():
            age = self._cycle_count - opp.deferred_at_cycle
            item = {
                "symbol": sym,
                "action": "HOLD",
                "type": "position_hold",
                "deferred_cycles_ago": age,
            }
            if opp.conditions.get("wait_cycles") is not None:
                item["wait_cycles"] = opp.conditions["wait_cycles"]
            if opp.conditions.get("wait_until_price_above") is not None:
                item["wait_until_price_above"] = opp.conditions["wait_until_price_above"]
            if opp.conditions.get("wait_until_price_below") is not None:
                item["wait_until_price_below"] = opp.conditions["wait_until_price_below"]
            items.append(item)

        return items

    # ── Main advisor call ─────────────────────────────────────

    async def consult(
        self,
        positions: list[dict[str, Any]],
        opportunities: list[dict[str, Any]],
        account: dict[str, Any],
        recent_trades: list[dict[str, Any]] | None = None,
        trade_stats: dict[str, Any] | None = None,
        deferred: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Call Claude Code CLI with the current state and return structured advice.

        Returns dict with "positions" and "opportunities" keys, each a list of actions.
        On error, returns empty lists.
        """
        payload = {
            "positions": positions,
            "opportunities": opportunities,
            "account": account,
        }
        if recent_trades:
            payload["recent_trades"] = recent_trades
        if trade_stats:
            payload["trade_stats"] = trade_stats
        if deferred:
            payload["deferred"] = deferred

        payload_json = json.dumps(payload, default=str)

        try:
            result = await asyncio.wait_for(
                self._invoke_cli(payload_json),
                timeout=self._timeout,
            )
            return result
        except asyncio.TimeoutError:
            logger.warning("AI advisor timed out after %ds — continuing autonomously", self._timeout)
            return {"positions": [], "opportunities": []}
        except Exception:
            logger.warning("AI advisor error — continuing autonomously", exc_info=True)
            return {"positions": [], "opportunities": []}

    async def _invoke_cli(self, payload_json: str) -> dict[str, Any]:
        """Invoke claude CLI and parse the structured output."""
        schema_content = self._schema_path.read_text()

        # Write payload to a temp file to avoid shell argument length limits
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", prefix="ai_advisor_", delete=False,
            dir="/tmp/claude",
        ) as f:
            f.write(payload_json)
            payload_file = f.name

        try:
            # Build the prompt that references the payload
            prompt = f"Review the following trading state and provide your decisions:\n\n{payload_json}"

            cmd = [
                "claude",
                "-p", prompt,
                "--no-session-persistence",
                "--model", self._model,
                "--output-format", "json",
                "--json-schema", schema_content,
                "--system-prompt-file", str(self._prompt_path),
                "--allowedTools", "",
                "--max-turns", "2",
            ]

            logger.debug("Invoking AI advisor CLI (model=%s)", self._model)

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()

            if proc.returncode != 0:
                stderr_text = stderr.decode(errors="replace").strip()
                logger.warning(
                    "AI advisor CLI exited with code %d: %s",
                    proc.returncode, stderr_text[:500],
                )
                return {"positions": [], "opportunities": []}

            output = stdout.decode(errors="replace").strip()
            if not output:
                logger.warning("AI advisor returned empty output")
                return {"positions": [], "opportunities": []}

            # Parse the JSON output
            response = json.loads(output)

            # claude --output-format json wraps result in {"result": ..., "structured_output": ...}
            # The structured_output contains our schema-validated response
            if isinstance(response, dict):
                if "structured_output" in response:
                    result = response["structured_output"]
                elif "result" in response:
                    # Try to parse result as JSON (might be a JSON string)
                    result_val = response["result"]
                    if isinstance(result_val, str):
                        try:
                            result = json.loads(result_val)
                        except json.JSONDecodeError:
                            result = response
                    elif isinstance(result_val, dict):
                        result = result_val
                    else:
                        result = response
                else:
                    result = response
            else:
                result = response

            # Validate structure
            if not isinstance(result, dict):
                logger.warning("AI advisor returned non-dict: %s", type(result))
                return {"positions": [], "opportunities": []}

            positions_resp = result.get("positions", [])
            opportunities_resp = result.get("opportunities", [])

            logger.info(
                "AI advisor responded: %d position actions, %d opportunity actions",
                len(positions_resp), len(opportunities_resp),
            )

            # Log reasoning
            for pa in positions_resp:
                logger.info(
                    "  Position %s: %s — %s",
                    pa.get("symbol"), pa.get("action"), pa.get("reasoning", ""),
                )
            for oa in opportunities_resp:
                logger.info(
                    "  Opportunity %s: %s — %s",
                    oa.get("symbol"), oa.get("action"), oa.get("reasoning", ""),
                )

            return result

        finally:
            # Clean up temp file
            try:
                Path(payload_file).unlink(missing_ok=True)
            except Exception:
                pass
