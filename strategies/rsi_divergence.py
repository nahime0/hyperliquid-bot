"""RSI Divergence — autonomous decision generator for live trading.

Detects RSI divergences on 5m candles and generates BUY/SHORT/CLOSE decisions.

LONG Entry (ALL must be true):
  - Trend filter BULLISH or NEUTRAL on 1h
  - Bullish divergence: price makes lower low, RSI makes higher low
  - Cooldown passed for the symbol
  - No open position on the same symbol
  - Funding rate acceptable

SHORT Entry (ALL must be true):
  - Trend filter BEARISH or NEUTRAL on 1h
  - Bearish divergence: price makes higher high, RSI makes lower high
  - Cooldown passed + no open position
  - Funding rate acceptable

Exit (ANY trigger):
  - LONG: RSI > rsi_long_exit (default 60)
  - SHORT: RSI < rsi_short_exit (default 40)
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
import ta as ta_lib

from config.settings import RiskConfig, StrategyConfig
from core.ai_engine.types import Decision, Tier
from core.market_data import MarketData
from risk.position_tracker import PositionTracker
from strategies.base import Strategy
from strategies.cooldown import CooldownTracker
from strategies.trend_filter import TrendFilter
from utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class DivergenceSignal:
    """Divergence detection result for one coin."""
    symbol: str
    divergence: str        # BULLISH_DIV, BEARISH_DIV, NONE
    rsi: float | None
    price: float | None
    trend: str             # from TrendFilter
    confidence: float      # 0-1
    reason: str
    updated_at: float


class RSIDivergenceStrategy(Strategy):
    """Live RSI Divergence strategy with swing detection on 5m candles."""

    def __init__(
        self,
        market_data: MarketData,
        trend_filter: TrendFilter,
        cooldown: CooldownTracker,
        position_tracker: PositionTracker,
        risk_config: RiskConfig,
        strategy_config: StrategyConfig,
        pairs: list[str] | None = None,
    ) -> None:
        self._md = market_data
        self._trend = trend_filter
        self._cooldown = cooldown
        self._positions = position_tracker
        self._rc = risk_config
        self._sc = strategy_config
        self._pairs = pairs or []
        self._interval = strategy_config.primary_interval  # 5m
        self._signals: dict[str, DivergenceSignal] = {}
        self._funding_rates: dict[str, float] = {}
        self._max_funding_rate: float = 0.0005

        # RSI Div params from config
        self._rsi_period = strategy_config.rsi_div_period
        self._swing_window = strategy_config.rsi_div_swing_window
        self._rsi_long_exit = strategy_config.rsi_div_long_exit
        self._rsi_short_exit = strategy_config.rsi_div_short_exit

    def set_pairs(self, pairs: list[str]) -> None:
        self._pairs = pairs
        logger.info("RSIDivergence pairs updated: %d coins", len(pairs))

    def set_funding_rates(self, rates: dict[str, float]) -> None:
        self._funding_rates = rates

    def set_max_funding_rate(self, rate: float) -> None:
        self._max_funding_rate = rate

    # -- Lifecycle --

    async def start(self) -> None:
        logger.info(
            "RSIDivergenceStrategy started — scanning %d coins on %s "
            "(rsi=%d, swing=%d, exit_l=%.0f, exit_s=%.0f)",
            len(self._pairs), self._interval,
            self._rsi_period, self._swing_window,
            self._rsi_long_exit, self._rsi_short_exit,
        )

    async def stop(self) -> None:
        self._signals.clear()
        logger.info("RSIDivergenceStrategy stopped")

    # -- Update (called every tick) --

    async def update(self) -> None:
        tasks = [self._scan_pair(sym) for sym in self._pairs]
        await asyncio.gather(*tasks)

    async def _scan_pair(self, symbol: str) -> None:
        """Detect RSI divergence for one coin on 5m candles."""
        try:
            df = self._md.get_candles(symbol, self._interval)
            if df is None or len(df) < self._rsi_period + 2 * self._swing_window + 10:
                return

            close = df["close"]
            high = df["high"]
            low = df["low"]

            # Compute RSI
            rsi_series = ta_lib.momentum.RSIIndicator(
                close, window=self._rsi_period
            ).rsi()

            if rsi_series.empty or pd.isna(rsi_series.iloc[-1]):
                return

            rsi = float(rsi_series.iloc[-1])
            price = float(close.iloc[-1])

            # Swing detection via rolling min/max
            w = self._swing_window
            window_size = 2 * w + 1

            swing_low = low.rolling(window_size, center=True).min()
            is_swing_low = (low == swing_low)

            swing_high = high.rolling(window_size, center=True).max()
            is_swing_high = (high == swing_high)

            # Find last two swing lows and swing highs
            # Only look at confirmed swings (not the last w bars which are incomplete)
            confirmed_end = len(df) - w
            if confirmed_end < w + 1:
                return

            # Find recent swing lows (for bullish divergence)
            swing_low_indices = [
                i for i in range(confirmed_end)
                if is_swing_low.iloc[i] and pd.notna(rsi_series.iloc[i])
            ]
            # Find recent swing highs (for bearish divergence)
            swing_high_indices = [
                i for i in range(confirmed_end)
                if is_swing_high.iloc[i] and pd.notna(rsi_series.iloc[i])
            ]

            # Get trend from TrendFilter
            trend_state = self._trend.get_state(symbol)
            trend = trend_state.get("trend", "UNKNOWN")

            divergence = "NONE"
            confidence = 0.0
            reason = ""

            # Check bullish divergence (last two swing lows)
            if len(swing_low_indices) >= 2:
                prev_i = swing_low_indices[-2]
                curr_i = swing_low_indices[-1]
                prev_price = float(low.iloc[prev_i])
                curr_price = float(low.iloc[curr_i])
                prev_rsi = float(rsi_series.iloc[prev_i])
                curr_rsi = float(rsi_series.iloc[curr_i])

                # Price lower low + RSI higher low = bullish divergence
                if curr_price < prev_price and curr_rsi > prev_rsi:
                    # Recency check: divergence must be within last 50 bars
                    if len(df) - curr_i <= 50:
                        price_diff = (prev_price - curr_price) / prev_price
                        rsi_diff = curr_rsi - prev_rsi
                        confidence = min(0.85, 0.5 + price_diff * 5 + rsi_diff * 0.01)
                        divergence = "BULLISH_DIV"
                        reason = (
                            f"Bullish div: price {prev_price:.4f}->{curr_price:.4f} "
                            f"(lower), RSI {prev_rsi:.1f}->{curr_rsi:.1f} (higher)"
                        )

            # Check bearish divergence (last two swing highs)
            if divergence == "NONE" and len(swing_high_indices) >= 2:
                prev_i = swing_high_indices[-2]
                curr_i = swing_high_indices[-1]
                prev_price = float(high.iloc[prev_i])
                curr_price = float(high.iloc[curr_i])
                prev_rsi = float(rsi_series.iloc[prev_i])
                curr_rsi = float(rsi_series.iloc[curr_i])

                # Price higher high + RSI lower high = bearish divergence
                if curr_price > prev_price and curr_rsi < prev_rsi:
                    if len(df) - curr_i <= 50:
                        price_diff = (curr_price - prev_price) / prev_price
                        rsi_diff = prev_rsi - curr_rsi
                        confidence = min(0.85, 0.5 + price_diff * 5 + rsi_diff * 0.01)
                        divergence = "BEARISH_DIV"
                        reason = (
                            f"Bearish div: price {prev_price:.4f}->{curr_price:.4f} "
                            f"(higher), RSI {prev_rsi:.1f}->{curr_rsi:.1f} (lower)"
                        )

            self._signals[symbol] = DivergenceSignal(
                symbol=symbol,
                divergence=divergence,
                rsi=rsi,
                price=price,
                trend=trend,
                confidence=confidence,
                reason=reason,
                updated_at=time.time(),
            )
        except Exception:
            logger.exception("Error scanning RSI Div %s", symbol)

    # -- Funding rate check --

    def _funding_ok(self, symbol: str) -> bool:
        rate = self._funding_rates.get(symbol)
        if rate is None:
            return True
        return abs(rate) < self._max_funding_rate

    # -- Decision generation --

    async def generate_decisions(self) -> list[Decision]:
        """Generate BUY/SHORT/CLOSE decisions based on RSI divergence rules."""
        decisions: list[Decision] = []
        open_symbols = await self._positions.get_open_symbols()

        # EXIT checks on open positions
        for symbol in list(open_symbols):
            sig = self._signals.get(symbol)
            if not sig or sig.rsi is None:
                continue

            pos = await self._positions.get_position_for_symbol(symbol)
            if not pos:
                continue

            direction = pos.get("direction", "LONG")
            exit_decision = self._check_exit(symbol, sig, direction)
            if exit_decision:
                decisions.append(exit_decision)

        # ENTRY checks
        for symbol in self._pairs:
            if symbol in open_symbols:
                continue

            sig = self._signals.get(symbol)
            if not sig or sig.divergence == "NONE":
                continue

            if sig.divergence == "BULLISH_DIV":
                entry = self._check_long_entry(symbol, sig)
            elif sig.divergence == "BEARISH_DIV":
                entry = self._check_short_entry(symbol, sig)
            else:
                entry = None

            if entry:
                decisions.append(entry)

        return decisions

    def _check_exit(self, symbol: str, sig: DivergenceSignal, direction: str) -> Decision | None:
        """Check if a position should be closed based on RSI thresholds."""
        if sig.rsi is None:
            return None

        reasons: list[str] = []
        if direction == "LONG" and sig.rsi > self._rsi_long_exit:
            reasons.append(f"RSI={sig.rsi:.1f}>{self._rsi_long_exit}")
        elif direction == "SHORT" and sig.rsi < self._rsi_short_exit:
            reasons.append(f"RSI={sig.rsi:.1f}<{self._rsi_short_exit}")

        if not reasons:
            return None

        return Decision(
            action="CLOSE",
            symbol=symbol,
            confidence=0.8,
            reasoning=f"[RSIDiv {direction} EXIT] {symbol}: {', '.join(reasons)}",
            strategy_type="rsi_divergence",
            order_type="MARKET",
            tier=Tier.FALLBACK,
        )

    def _check_long_entry(self, symbol: str, sig: DivergenceSignal) -> Decision | None:
        """Check if a LONG entry via bullish divergence is warranted."""
        # Trend gate: BULLISH or NEUTRAL
        if sig.trend not in ("BULLISH", "NEUTRAL"):
            return None

        if not self._funding_ok(symbol):
            logger.debug("RSIDiv LONG blocked for %s: high funding rate", symbol)
            return None

        can_buy, reason = self._cooldown.can_buy(symbol)
        if not can_buy:
            logger.debug("RSIDiv entry blocked for %s: %s", symbol, reason)
            return None

        return Decision(
            action="BUY",
            symbol=symbol,
            confidence=sig.confidence,
            reasoning=f"[RSIDiv LONG] {symbol}: {sig.reason}, trend={sig.trend}",
            strategy_type="rsi_divergence",
            size_pct=10.0,
            order_type="MARKET",
            tier=Tier.FALLBACK,
        )

    def _check_short_entry(self, symbol: str, sig: DivergenceSignal) -> Decision | None:
        """Check if a SHORT entry via bearish divergence is warranted."""
        # Trend gate: BEARISH or NEUTRAL
        if sig.trend not in ("BEARISH", "NEUTRAL"):
            return None

        if not self._funding_ok(symbol):
            logger.debug("RSIDiv SHORT blocked for %s: high funding rate", symbol)
            return None

        can_buy, reason = self._cooldown.can_buy(symbol)
        if not can_buy:
            logger.debug("RSIDiv entry blocked for %s: %s", symbol, reason)
            return None

        return Decision(
            action="SHORT",
            symbol=symbol,
            confidence=sig.confidence,
            reasoning=f"[RSIDiv SHORT] {symbol}: {sig.reason}, trend={sig.trend}",
            strategy_type="rsi_divergence",
            size_pct=10.0,
            order_type="MARKET",
            tier=Tier.FALLBACK,
        )

    # -- State (for snapshot / dashboard) --

    def get_state(self) -> dict[str, Any]:
        signals: dict[str, Any] = {}
        bullish_divs: list[str] = []
        bearish_divs: list[str] = []

        for sym, sig in self._signals.items():
            signals[sym] = {
                "divergence": sig.divergence,
                "rsi": round(sig.rsi, 2) if sig.rsi is not None else None,
                "price": sig.price,
                "trend": sig.trend,
                "confidence": sig.confidence,
                "reason": sig.reason,
            }
            if sig.divergence == "BULLISH_DIV":
                bullish_divs.append(sym)
            elif sig.divergence == "BEARISH_DIV":
                bearish_divs.append(sym)

        return {
            "strategy": "rsi_divergence",
            "interval": self._interval,
            "pairs_scanned": len(self._signals),
            "bullish_divergences": bullish_divs,
            "bearish_divergences": bearish_divs,
            "signals": signals,
        }
