"""Multi-Strategy Aggregator — merges decisions from multiple sub-strategies.

NOT a voting system (like CombinedRule in backtest). Each sub-strategy produces
its own Decision objects independently. The merger unifies them per coin:

  - 2+ strategies agree on BUY/SHORT → boost confidence +0.1
  - Strategies conflict (BUY vs SHORT on same coin) → highest score wins,
    conflict noted in reasoning so the AI advisor knows there's disagreement
  - Only 1 strategy signals → use original confidence
  - Entries sorted by confidence (highest first), no cap at this stage
"""
from __future__ import annotations

from typing import Any

from core.types import Decision
from strategies.base import Strategy
from utils.logger import get_logger

logger = get_logger(__name__)

# Confidence boost when multiple strategies agree
_AGREEMENT_BOOST = 0.1


class MultiStrategy(Strategy):
    """Aggregates decisions from multiple sub-strategies."""

    def __init__(self, strategies: list[Strategy]) -> None:
        self._strategies = strategies

    def set_cycle(self, cycle: int) -> None:
        for s in self._strategies:
            if hasattr(s, "set_cycle"):
                s.set_cycle(cycle)

    # -- Lifecycle --

    async def start(self) -> None:
        for s in self._strategies:
            await s.start()
        logger.info(
            "MultiStrategy started with %d sub-strategies",
            len(self._strategies),
        )

    async def stop(self) -> None:
        for s in self._strategies:
            await s.stop()
        logger.info("MultiStrategy stopped")

    async def update(self) -> None:
        for s in self._strategies:
            await s.update()

    # -- Decision merging --

    async def generate_raw_decisions(self) -> list[Decision]:
        """Collect from all sub-strategies WITHOUT merging."""
        all_decisions: list[Decision] = []
        for s in self._strategies:
            try:
                decisions = await s.generate_decisions()
                all_decisions.extend(decisions)
            except Exception:
                logger.exception("Error generating decisions from %s", type(s).__name__)
        return all_decisions

    async def generate_decisions(self) -> list[Decision]:
        """Collect decisions from all sub-strategies and merge per coin."""
        all_decisions = await self.generate_raw_decisions()
        if not all_decisions:
            return []
        return self.merge(all_decisions)

    @staticmethod
    def merge(decisions: list[Decision]) -> list[Decision]:
        """Merge decisions: deduplicate per coin, handle conflicts, boost agreement."""
        # Separate exits (CLOSE/SELL) from entries (BUY/SHORT)
        exits: list[Decision] = []
        entries_by_coin: dict[str, list[Decision]] = {}

        for d in decisions:
            if d.action in ("CLOSE", "SELL"):
                exits.append(d)
            elif d.action in ("BUY", "SHORT") and d.symbol:
                entries_by_coin.setdefault(d.symbol, []).append(d)

        # Exits: deduplicate per coin (keep highest confidence)
        exit_seen: dict[str, Decision] = {}
        for d in exits:
            sym = d.symbol or ""
            if sym not in exit_seen or d.confidence > exit_seen[sym].confidence:
                exit_seen[sym] = d
        merged_exits = list(exit_seen.values())

        # Entries: merge per coin
        merged_entries: list[Decision] = []
        for coin, coin_decisions in entries_by_coin.items():
            actions = {d.action for d in coin_decisions}

            # Conflict: BUY and SHORT on same coin → highest score wins
            if "BUY" in actions and "SHORT" in actions:
                best = max(coin_decisions, key=lambda d: d.confidence)
                loser = min(coin_decisions, key=lambda d: d.confidence)
                best = Decision(
                    action=best.action,
                    symbol=best.symbol,
                    confidence=best.confidence,
                    reasoning=(
                        f"[Conflict: {best.strategy_type} {best.action} wins over "
                        f"{loser.strategy_type} {loser.action} ({loser.confidence:.2f})] "
                        f"{best.reasoning}"
                    ),
                    strategy_type=best.strategy_type,
                    size_pct=best.size_pct,
                    order_type=best.order_type,
                    stop_loss=best.stop_loss,
                    take_profit=best.take_profit,
                )
                merged_entries.append(best)
                logger.info(
                    "MultiStrategy conflict on %s: %s %s (%.2f) wins over %s %s (%.2f)",
                    coin, best.strategy_type, best.action, best.confidence,
                    loser.strategy_type, loser.action, loser.confidence,
                )
                continue

            # All agree on direction — pick best, boost if multiple
            best = max(coin_decisions, key=lambda d: d.confidence)

            if len(coin_decisions) > 1:
                # Multiple strategies agree → boost confidence
                boosted = min(1.0, best.confidence + _AGREEMENT_BOOST)
                strat_names = [d.strategy_type or "?" for d in coin_decisions]
                best = Decision(
                    action=best.action,
                    symbol=best.symbol,
                    confidence=boosted,
                    reasoning=(
                        f"[Multi: {'+'.join(strat_names)}] "
                        f"{best.reasoning} (boosted {best.confidence:.2f}->{boosted:.2f})"
                    ),
                    strategy_type=best.strategy_type,
                    size_pct=best.size_pct,
                    order_type=best.order_type,
                    stop_loss=best.stop_loss,
                    take_profit=best.take_profit,
                )

            merged_entries.append(best)

        # Sort entries by confidence (descending)
        merged_entries.sort(key=lambda d: d.confidence, reverse=True)

        return merged_exits + merged_entries

    # -- State (for snapshot / dashboard) --

    def get_state(self) -> dict[str, Any]:
        states: dict[str, Any] = {}
        for s in self._strategies:
            name = type(s).__name__
            states[name] = s.get_state()
        return {
            "strategy": "multi_strategy",
            "sub_strategies": states,
            "num_strategies": len(self._strategies),
        }
