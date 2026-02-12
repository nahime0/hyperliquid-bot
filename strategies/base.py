"""Abstract base class for all trading strategies."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.types import Decision


class Strategy(ABC):
    """Base interface that every strategy must implement.

    The main loop calls these methods:
      - start()                — once at boot (load exchange info, restore state)
      - update()               — every tick (compute signals)
      - generate_decisions()   — produce BUY/SHORT/CLOSE Decisions
      - stop()                 — on shutdown (cancel open orders, persist state)
      - get_state()            — when building the AI snapshot
    """

    @abstractmethod
    async def start(self) -> None:
        """Initialize the strategy (load exchange info, restore state, etc.)."""

    @abstractmethod
    async def stop(self) -> None:
        """Graceful shutdown: cancel open orders, persist state."""

    @abstractmethod
    async def update(self) -> None:
        """Called every tick by the main loop.

        Compute indicators, update signals, etc.
        """

    @abstractmethod
    async def generate_decisions(self) -> list[Decision]:
        """Generate trading decisions based on current signals.

        Returns a list of Decision objects ready for risk validation.
        """

    @abstractmethod
    def get_state(self) -> dict[str, Any]:
        """Return current strategy state for the AI market snapshot."""
