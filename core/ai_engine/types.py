from __future__ import annotations

import enum
from dataclasses import dataclass


class Tier(enum.Enum):
    """Which tier produced the final decision."""

    PRESCREEN = "prescreen"
    HAIKU = "haiku"
    OPUS = "opus"
    FALLBACK = "fallback"


@dataclass
class Decision:
    """Represents a trading decision from the AI engine."""

    action: str  # BUY, SELL, HOLD, CLOSE
    confidence: float  # 0-1
    reasoning: str
    symbol: str | None = None
    size_pct: float | None = None
    order_type: str | None = None  # LIMIT, MARKET
    limit_price: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    strategy_type: str | None = None  # grid, mean_reversion, momentum, other
    raw_response: str | None = None
    tier: Tier = Tier.FALLBACK
