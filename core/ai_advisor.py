"""AI Advisor — CLI integration for trading decisions.

Supports two backends (selected via AIConfig.advisor):
  - "claude": Claude Code CLI with --json-schema for structured output
  - "cursor": Cursor Agent CLI with schema embedded in prompt

Single CLI call per cycle. The AI reviews all positions and opportunities
together, responding with structured JSON.

On error: logs a warning and returns empty response (bot continues autonomously).
"""
from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from config.settings import AIConfig
from core.cli_claude import invoke_claude_cli
from core.cli_cursor import invoke_cursor_cli
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
    strategy_type: str = "unknown"
    confidence: float = 0.0


class AIAdvisor:
    """CLI advisor for trading decisions (Claude or Cursor backend)."""

    def __init__(
        self,
        *,
        config: AIConfig | None = None,
        model: str = "sonnet",
        timeout: int = 180,
        db: Database | None = None,
    ) -> None:
        if config is not None:
            self._advisor = config.advisor
            self._model = config.model
            self._timeout = config.timeout
        else:
            self._advisor = "claude"
            self._model = model
            self._timeout = timeout
        self._db = db
        self._schema_path = _PROJECT_ROOT / "schemas" / "ai_advisor_output.json"
        self._prompt_path = _PROJECT_ROOT / "prompts" / "ai_advisor.md"
        self._cycle_count: int = 0

        # Build clean env for subprocess
        self._env = os.environ.copy()

        # Deferred opportunities keyed by (symbol, strategy_type)
        self._deferred: dict[tuple[str, str], DeferredOpportunity] = {}
        # Deferred position holds keyed by symbol (positions don't have strategy_type)
        self._deferred_holds: dict[str, DeferredOpportunity] = {}

    @property
    def deferred_keys(self) -> set[tuple[str, str]]:
        """Keys (symbol, strategy_type) with deferred opportunities."""
        return set(self._deferred.keys())

    @property
    def deferred_symbols(self) -> set[str]:
        """Symbols with deferred opportunities (for quick checks)."""
        return {k[0] for k in self._deferred.keys()}

    @property
    def deferred_hold_symbols(self) -> set[str]:
        """Symbols with deferred position holds."""
        return set(self._deferred_holds.keys())

    def is_deferred(self, symbol: str, strategy_type: str) -> bool:
        """Check if a specific (symbol, strategy_type) pair is deferred."""
        return (symbol, strategy_type) in self._deferred

    def get_deferred_action(self, symbol: str, strategy_type: str | None = None) -> str | None:
        """Get the original action (BUY/SHORT) of a deferred opportunity."""
        if strategy_type is not None:
            opp = self._deferred.get((symbol, strategy_type))
            return opp.original_action if opp else None
        # Fallback: find any deferred for this symbol
        for (sym, _st), opp in self._deferred.items():
            if sym == symbol:
                return opp.original_action
        return None

    def get_deferred_confidence(self, symbol: str, strategy_type: str) -> float:
        """Get the confidence score stored when this opportunity was deferred."""
        opp = self._deferred.get((symbol, strategy_type))
        return opp.confidence if opp else 0.0

    def set_cycle(self, cycle: int) -> None:
        """Update the current cycle count (called from main loop)."""
        self._cycle_count = cycle

    # ── Deferred opportunity management ───────────────────────

    async def defer(
        self, symbol: str, original_action: str, conditions: dict[str, Any],
        strategy_type: str = "unknown", confidence: float = 0.0,
    ) -> None:
        """Defer an opportunity for later re-evaluation."""
        key = (symbol, strategy_type)
        self._deferred[key] = DeferredOpportunity(
            symbol=symbol,
            original_action=original_action,
            deferred_at_cycle=self._cycle_count,
            conditions=conditions,
            strategy_type=strategy_type,
            confidence=confidence,
        )
        if self._db:
            try:
                await self._db.upsert_deferred(
                    symbol, original_action, self._cycle_count, conditions,
                    "opportunity", strategy_type, confidence,
                )
            except Exception:
                logger.debug("Failed to persist deferred opportunity for %s/%s", symbol, strategy_type, exc_info=True)
        logger.info(
            "Deferred %s %s [%s] score=%.2f — conditions: %s",
            original_action, symbol, strategy_type, confidence, conditions,
        )

    async def remove_deferred(self, symbol: str, strategy_type: str | None = None) -> None:
        """Remove deferred opportunity(ies). If strategy_type is None, remove all for symbol."""
        if strategy_type is not None:
            key = (symbol, strategy_type)
            if key in self._deferred:
                del self._deferred[key]
                if self._db:
                    try:
                        await self._db.delete_deferred(symbol, "opportunity", strategy_type)
                    except Exception:
                        logger.debug("Failed to delete deferred opportunity for %s/%s", symbol, strategy_type, exc_info=True)
                logger.debug("Removed deferred opportunity for %s/%s", symbol, strategy_type)
        else:
            # Remove all deferred for this symbol
            to_remove = [k for k in self._deferred if k[0] == symbol]
            for k in to_remove:
                del self._deferred[k]
            if self._db:
                try:
                    await self._db.delete_deferred(symbol, "opportunity")
                except Exception:
                    logger.debug("Failed to delete deferred opportunities for %s", symbol, exc_info=True)
            if to_remove:
                logger.debug("Removed %d deferred opportunity(ies) for %s", len(to_remove), symbol)

    async def check_deferred(self, mid_prices: dict[str, float]) -> list[tuple[str, str]]:
        """Check which deferred opportunities have met their conditions.

        Returns list of (symbol, strategy_type) tuples ready for re-evaluation.
        """
        return await self._check_deferred_conditions(mid_prices)

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
        return await self._check_hold_conditions(mid_prices)

    # ── Load from DB (startup) ──────────────────────────────

    async def load_deferred(self) -> None:
        """Clear all deferred on startup — stale after restart."""
        if not self._db:
            return
        try:
            rows = await self._db.get_all_deferred()
            count = len(rows)
            if count:
                for row in rows:
                    await self._db.delete_deferred(row["symbol"], row["type"], row.get("strategy_type"))
                logger.info("Cleared %d stale deferred entries on startup", count)
        except Exception:
            logger.warning("Failed to clear deferred from DB", exc_info=True)
        self._deferred.clear()
        self._deferred_holds.clear()

    # ── Condition checkers ────────────────────────────────

    async def _check_deferred_conditions(self, mid_prices: dict[str, float]) -> list[tuple[str, str]]:
        """Check deferred opportunities keyed by (symbol, strategy_type)."""
        ready: list[tuple[str, str]] = []
        to_remove: list[tuple[str, str]] = []

        for key, opp in self._deferred.items():
            symbol = opp.symbol
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
                ready.append(key)
                to_remove.append(key)

        for key in to_remove:
            opp = self._deferred.pop(key)
            if self._db:
                try:
                    await self._db.delete_deferred(key[0], "opportunity", key[1])
                except Exception:
                    logger.debug("Failed to delete deferred opportunity %s from DB", key, exc_info=True)
            logger.info("Deferred opportunity %s/%s conditions met — re-evaluating", key[0], key[1])

        return ready

    async def _check_hold_conditions(self, mid_prices: dict[str, float]) -> list[str]:
        """Check deferred holds keyed by symbol."""
        ready: list[str] = []
        to_remove: list[str] = []

        for symbol, opp in self._deferred_holds.items():
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
            del self._deferred_holds[symbol]
            if self._db:
                try:
                    await self._db.delete_deferred(symbol, "hold")
                except Exception:
                    logger.debug("Failed to delete deferred hold %s from DB", symbol, exc_info=True)
            logger.info("Deferred hold %s conditions met — re-evaluating", symbol)

        return ready

    # ── Deferred summary (for AI context) ────────────────────

    def get_deferred_summary(self) -> list[dict[str, Any]]:
        """Build a structured summary of all deferred items for the AI payload."""
        items: list[dict[str, Any]] = []
        for (sym, strat), opp in self._deferred.items():
            age = self._cycle_count - opp.deferred_at_cycle
            item: dict[str, Any] = {
                "symbol": sym,
                "action": opp.original_action,
                "type": "opportunity",
                "strategy_type": strat,
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
        market_data: dict[str, dict[str, Any]] | None = None,
        recent_trades: list[dict[str, Any]] | None = None,
        trade_stats: dict[str, Any] | None = None,
        deferred: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Call Claude Code CLI with the current state and return structured advice.

        Returns dict with "positions" and "opportunities" keys, each a list of actions.
        On error, returns empty lists.
        """
        # Early return if nothing to review
        if not positions and not opportunities:
            return {"positions": [], "opportunities": []}

        payload: dict[str, Any] = {
            "positions": positions,
            "opportunities": opportunities,
            "account": account,
        }
        if market_data:
            payload["market_data"] = market_data
        if recent_trades:
            payload["recent_trades"] = recent_trades
        if trade_stats:
            payload["trade_stats"] = trade_stats
        if deferred:
            payload["deferred"] = deferred

        payload_json = json.dumps(payload, default=str)
        logger.info("AI payload: %.1f KB (%d pos, %d opp) — %s/%s",
                    len(payload_json) / 1024, len(positions), len(opportunities),
                    self._advisor, self._model)

        # Safety net: outer timeout slightly beyond CLI timeout to catch any edge case
        # (CLI backends handle their own timeout + process cleanup internally)
        safety_timeout = self._timeout + 15
        try:
            return await asyncio.wait_for(
                self._invoke_cli(payload_json),
                timeout=safety_timeout,
            )
        except asyncio.TimeoutError:
            logger.error(
                "AI advisor SAFETY timeout after %ds (CLI should have timed out at %ds) — killing stuck call",
                safety_timeout, self._timeout,
            )
            return {"positions": [], "opportunities": []}
        except Exception:
            logger.warning("AI advisor error — continuing autonomously", exc_info=True)
            return {"positions": [], "opportunities": []}

    async def _invoke_cli(self, payload_json: str) -> dict[str, Any]:
        """Invoke the configured CLI backend and parse the structured output."""
        kwargs = dict(
            payload_json=payload_json,
            model=self._model,
            timeout=self._timeout,
            schema_path=self._schema_path,
            prompt_path=self._prompt_path,
            env=self._env,
        )

        if self._advisor == "cursor":
            result = await invoke_cursor_cli(**kwargs)
        else:
            result = await invoke_claude_cli(**kwargs)

        if not isinstance(result, dict):
            logger.warning("AI advisor returned non-dict: %s", type(result))
            return {"positions": [], "opportunities": []}

        result = _validate_response(result)

        positions_resp = result.get("positions", [])
        opportunities_resp = result.get("opportunities", [])

        logger.info("AI advisor responded: %d position actions, %d opportunity actions",
                    len(positions_resp), len(opportunities_resp))

        for pa in positions_resp:
            logger.info("  Position %s: %s — %s",
                        pa.get("symbol"), pa.get("action"), pa.get("reasoning", ""))
        for oa in opportunities_resp:
            logger.info("  Opportunity %s: %s — %s",
                        oa.get("symbol"), oa.get("action"), oa.get("reasoning", ""))

        return result


_VALID_POSITION_ACTIONS = {"HOLD", "CLOSE", "ADJUST", "SCALE_UP", "FLIP"}
_VALID_OPPORTUNITY_ACTIONS = {"BUY", "SHORT", "HOLD"}


def _validate_response(resp: dict[str, Any]) -> dict[str, Any]:
    """Validate and sanitize the AI response structure.

    Ensures 'positions' and 'opportunities' are lists of dicts with
    required keys (symbol, action, reasoning).  Drops malformed items.
    """
    validated: dict[str, Any] = {}

    # -- positions --
    raw_pos = resp.get("positions")
    if not isinstance(raw_pos, list):
        raw_pos = []
    good_pos: list[dict[str, Any]] = []
    for item in raw_pos:
        if not isinstance(item, dict):
            continue
        sym = item.get("symbol")
        action = item.get("action")
        if not sym or not isinstance(sym, str):
            continue
        if action not in _VALID_POSITION_ACTIONS:
            logger.warning("Dropping position item: invalid action '%s' for %s", action, sym)
            continue
        if "reasoning" not in item:
            item["reasoning"] = ""
        good_pos.append(item)
    validated["positions"] = good_pos

    # -- opportunities --
    raw_opp = resp.get("opportunities")
    if not isinstance(raw_opp, list):
        raw_opp = []
    good_opp: list[dict[str, Any]] = []
    for item in raw_opp:
        if not isinstance(item, dict):
            continue
        sym = item.get("symbol")
        action = item.get("action")
        if not sym or not isinstance(sym, str):
            continue
        if action not in _VALID_OPPORTUNITY_ACTIONS:
            logger.warning("Dropping opportunity item: invalid action '%s' for %s", action, sym)
            continue
        if "reasoning" not in item:
            item["reasoning"] = ""
        good_opp.append(item)
    validated["opportunities"] = good_opp

    dropped_pos = len(raw_pos) - len(good_pos)
    dropped_opp = len(raw_opp) - len(good_opp)
    if dropped_pos or dropped_opp:
        logger.warning(
            "Validation dropped %d position(s), %d opportunity(s) from AI response",
            dropped_pos, dropped_opp,
        )

    return validated
