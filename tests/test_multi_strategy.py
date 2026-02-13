"""Tests for strategies.multi_strategy.MultiStrategy."""
from __future__ import annotations

import pytest
import pytest_asyncio

from core.types import Decision
from strategies.multi_strategy import MultiStrategy


class FakeStrategy:
    """Minimal strategy stub for testing."""

    def __init__(self, decisions: list[Decision] | None = None) -> None:
        self._decisions = decisions or []

    async def generate_decisions(self) -> list[Decision]:
        return self._decisions

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def update(self) -> None:
        pass

    def get_state(self):
        return {}


class TestGenerateRawDecisions:
    @pytest.mark.asyncio
    async def test_collects_from_all_strategies(self):
        s1 = FakeStrategy([Decision(action="BUY", symbol="ETH", confidence=0.7, reasoning="mr", strategy_type="mr")])
        s2 = FakeStrategy([Decision(action="SHORT", symbol="BTC", confidence=0.8, reasoning="tf", strategy_type="tf")])
        ms = MultiStrategy([s1, s2])
        raw = await ms.generate_raw_decisions()
        assert len(raw) == 2
        symbols = {d.symbol for d in raw}
        assert symbols == {"ETH", "BTC"}

    @pytest.mark.asyncio
    async def test_raw_does_not_merge(self):
        """Same coin, conflicting signals — raw returns both."""
        s1 = FakeStrategy([Decision(action="BUY", symbol="ETH", confidence=0.7, reasoning="mr", strategy_type="mr")])
        s2 = FakeStrategy([Decision(action="SHORT", symbol="ETH", confidence=0.8, reasoning="tf", strategy_type="tf")])
        ms = MultiStrategy([s1, s2])
        raw = await ms.generate_raw_decisions()
        assert len(raw) == 2  # both kept, not merged

    @pytest.mark.asyncio
    async def test_raw_handles_strategy_exception(self):
        """If one sub-strategy raises, others still collected."""
        class BrokenStrategy(FakeStrategy):
            async def generate_decisions(self):
                raise RuntimeError("boom")

        s1 = BrokenStrategy()
        s2 = FakeStrategy([Decision(action="BUY", symbol="SOL", confidence=0.6, reasoning="ok", strategy_type="mr")])
        ms = MultiStrategy([s1, s2])
        raw = await ms.generate_raw_decisions()
        assert len(raw) == 1
        assert raw[0].symbol == "SOL"

    @pytest.mark.asyncio
    async def test_raw_empty_when_no_signals(self):
        ms = MultiStrategy([FakeStrategy(), FakeStrategy()])
        raw = await ms.generate_raw_decisions()
        assert raw == []


class TestMergeStatic:
    def test_agreement_boosts_confidence(self):
        decisions = [
            Decision(action="BUY", symbol="ETH", confidence=0.7, reasoning="mr", strategy_type="mr"),
            Decision(action="BUY", symbol="ETH", confidence=0.65, reasoning="bb", strategy_type="bb_squeeze"),
        ]
        merged = MultiStrategy.merge(decisions)
        entries = [d for d in merged if d.action == "BUY"]
        assert len(entries) == 1
        assert entries[0].confidence == pytest.approx(0.8, abs=0.01)  # 0.7 + 0.1
        assert entries[0].strategy_type == "mr"  # best strategy preserved

    def test_conflict_highest_score_wins(self):
        decisions = [
            Decision(action="BUY", symbol="ETH", confidence=0.6, reasoning="mr", strategy_type="mr"),
            Decision(action="SHORT", symbol="ETH", confidence=0.8, reasoning="tf", strategy_type="tf"),
        ]
        merged = MultiStrategy.merge(decisions)
        entries = [d for d in merged if d.action in ("BUY", "SHORT")]
        assert len(entries) == 1
        assert entries[0].action == "SHORT"
        assert entries[0].confidence == 0.8
        assert entries[0].strategy_type == "tf"
        assert "[Conflict:" in entries[0].reasoning

    def test_single_strategy_no_boost(self):
        decisions = [
            Decision(action="SHORT", symbol="BTC", confidence=0.75, reasoning="tf", strategy_type="tf"),
        ]
        merged = MultiStrategy.merge(decisions)
        assert len(merged) == 1
        assert merged[0].confidence == 0.75

    def test_strategy_type_preserved_not_multi(self):
        """Winner's strategy_type is preserved, not replaced with 'multi'."""
        decisions = [
            Decision(action="BUY", symbol="SOL", confidence=0.9, reasoning="mr", strategy_type="mean_reversion"),
        ]
        merged = MultiStrategy.merge(decisions)
        assert merged[0].strategy_type == "mean_reversion"

    def test_exits_deduplicated(self):
        decisions = [
            Decision(action="CLOSE", symbol="ETH", confidence=0.8, reasoning="a", strategy_type="mr"),
            Decision(action="CLOSE", symbol="ETH", confidence=0.9, reasoning="b", strategy_type="tf"),
        ]
        merged = MultiStrategy.merge(decisions)
        exits = [d for d in merged if d.action == "CLOSE"]
        assert len(exits) == 1
        assert exits[0].confidence == 0.9

    def test_entries_sorted_by_confidence(self):
        decisions = [
            Decision(action="BUY", symbol="SOL", confidence=0.6, reasoning="a", strategy_type="mr"),
            Decision(action="SHORT", symbol="BTC", confidence=0.9, reasoning="b", strategy_type="tf"),
            Decision(action="BUY", symbol="ETH", confidence=0.75, reasoning="c", strategy_type="mr"),
        ]
        merged = MultiStrategy.merge(decisions)
        entries = [d for d in merged if d.action in ("BUY", "SHORT")]
        confs = [d.confidence for d in entries]
        assert confs == sorted(confs, reverse=True)

    def test_empty_decisions(self):
        assert MultiStrategy.merge([]) == []
