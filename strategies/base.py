"""Abstract base class for all trading strategies."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class Strategy(ABC):
    """Base interface that every strategy must implement.

    The main loop calls these methods:
      - start()      — once at boot (load exchange info, restore state)
      - update()     — every tick (check fills, place new orders)
      - stop()       — on shutdown (cancel open orders, persist state)
      - get_state()  — when building the AI snapshot
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

        Check for filled orders, place new ones, handle trailing, etc.
        """

    @abstractmethod
    def get_state(self) -> dict[str, Any]:
        """Return current strategy state for the AI market snapshot."""
