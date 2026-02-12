"""Trend Filter — EMA50/EMA200 on 1h candles.

Classifies each symbol as BULLISH, BEARISH, or NEUTRAL.
Used as a gate: MR LONG allows BULLISH+NEUTRAL, MR SHORT requires BEARISH.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd
import ta as ta_lib

from core.market_data import MarketData
from utils.logger import get_logger

logger = get_logger(__name__)

EMA_FAST = 50
EMA_SLOW = 200
SLOPE_WINDOW = 5  # bars to measure EMA50 slope


@dataclass
class TrendState:
    trend: str  # BULLISH, BEARISH, NEUTRAL
    ema50: float | None
    ema200: float | None
    slope: float | None  # EMA50 slope (positive = rising)
    price: float | None


class TrendFilter:
    """EMA50/EMA200 trend filter on 1h candles."""

    def __init__(self, market_data: MarketData, interval: str = "1h") -> None:
        self._md = market_data
        self._interval = interval
        self._states: dict[str, TrendState] = {}

    async def update(self, symbols: list[str]) -> None:
        """Recompute trend state for all symbols."""
        for symbol in symbols:
            try:
                self._compute(symbol)
            except Exception:
                logger.debug("TrendFilter error for %s", symbol, exc_info=True)

    def _compute(self, symbol: str) -> None:
        df = self._md.get_candles(symbol, self._interval)
        if df is None or len(df) < EMA_SLOW + SLOPE_WINDOW:
            self._states[symbol] = TrendState("NEUTRAL", None, None, None, None)
            return

        close = df["close"]
        price = float(close.iloc[-1])

        ema50_s = ta_lib.trend.EMAIndicator(close, window=EMA_FAST).ema_indicator()
        ema200_s = ta_lib.trend.EMAIndicator(close, window=EMA_SLOW).ema_indicator()

        ema50 = float(ema50_s.iloc[-1])
        ema200 = float(ema200_s.iloc[-1])

        # Slope: change over last SLOPE_WINDOW bars (normalized by price)
        if len(ema50_s) >= SLOPE_WINDOW + 1 and pd.notna(ema50_s.iloc[-SLOPE_WINDOW - 1]):
            slope = (ema50 - float(ema50_s.iloc[-SLOPE_WINDOW - 1])) / price * 100
        else:
            slope = 0.0

        # Classification
        if ema50 > ema200 and price > ema50 and slope > 0:
            trend = "BULLISH"
        elif ema50 < ema200 and price < ema50 and slope < 0:
            trend = "BEARISH"
        else:
            trend = "NEUTRAL"

        self._states[symbol] = TrendState(trend, round(ema50, 6), round(ema200, 6), round(slope, 4), price)

    def is_bullish(self, symbol: str) -> bool:
        state = self._states.get(symbol)
        return state is not None and state.trend == "BULLISH"

    def is_bearish(self, symbol: str) -> bool:
        state = self._states.get(symbol)
        return state is not None and state.trend == "BEARISH"

    def get_state(self, symbol: str) -> dict[str, Any]:
        state = self._states.get(symbol)
        if not state:
            return {"trend": "NEUTRAL"}
        return {
            "trend": state.trend,
            "ema50": state.ema50,
            "ema200": state.ema200,
            "slope": state.slope,
            "price": state.price,
        }

    def get_all_states(self) -> dict[str, dict[str, Any]]:
        return {sym: self.get_state(sym) for sym in self._states}
