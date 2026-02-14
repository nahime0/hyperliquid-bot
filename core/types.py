"""Shared types for trading decisions."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Decision:
    """Represents a trading decision from a strategy or AI advisor."""

    action: str  # BUY, SHORT, SELL, HOLD, CLOSE, SCALE_UP, FLIP
    confidence: float  # 0-1
    reasoning: str
    symbol: str | None = None
    size_pct: float | None = None
    order_type: str | None = None  # LIMIT, MARKET
    limit_price: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    strategy_type: str | None = None  # mean_reversion, rsi_divergence, multi
    leverage: int | None = None  # AI-specified leverage override
    entry_price_limit: float | None = None  # max for BUY, min for SHORT
    raw_response: str | None = None
