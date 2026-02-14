"""MACD Divergence — scoring-based decision generator.

Generates BUY/SHORT/CLOSE decisions based on MACD histogram divergence
with price action on 15m candles.

LONG Entry scoring (bullish divergence: price lower low + histogram higher low):
  - Divergence strength              -> +0.30
  - Histogram rising (last > prev)   -> +0.20
  - Volume ratio >= 1.0              -> +0.15
  - 1h trend alignment (not BEARISH) -> +0.15
  - |funding| < max_funding_rate     -> +0.10
  - No per-symbol cooldown           -> +0.10
  Score >= 0.50 -> generate signal (confidence = score)

SHORT Entry scoring (bearish divergence: price higher high + histogram lower high):
  - Divergence strength              -> +0.30
  - Histogram falling (last < prev)  -> +0.20
  - Volume ratio >= 1.0              -> +0.15
  - 1h trend alignment (not BULLISH) -> +0.15
  - |funding| < max_funding_rate     -> +0.10
  - No per-symbol cooldown           -> +0.10

Hard blocks (always reject, bypass scoring):
  - |funding| >= 0.001 (extreme funding)
  - Global cooldown active (3+ consecutive losses)
  - Fresh per-symbol cooldown (loss < 10 min ago)

Exit:
  - LONG exit: MACD histogram < 0 (crossed zero going negative)
  - SHORT exit: MACD histogram > 0 (crossed zero going positive)
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any

import pandas as pd
import ta as ta_lib

from config.settings import RiskConfig, StrategyConfig
from core.types import Decision
from core.market_data import MarketData
from data.db import Database
from risk.position_tracker import PositionTracker
from strategies.base import Strategy
from strategies.cooldown import CooldownTracker
from strategies.hard_blocks import check_hard_blocks
from strategies.trend_filter import TrendFilter
from utils.logger import get_logger

logger = get_logger(__name__)

# -- MACD params --
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9

# -- Scoring weights --
W_DIVERGENCE = 0.30       # divergence strength
W_HISTOGRAM = 0.20        # histogram direction (rising/falling)
W_VOLUME = 0.15            # volume ratio >= threshold
W_TREND = 0.15             # 1h trend alignment
W_FUNDING = 0.10           # funding rate acceptable
W_COOLDOWN = 0.10          # no per-symbol cooldown
SCORE_THRESHOLD = 0.60     # minimum score to generate signal

# -- Thresholds --
MIN_VOLUME_RATIO = 1.0     # volume ratio threshold
VOLUME_SMA_PERIOD = 20     # SMA period for volume ratio


@dataclass
class MacdDivSignal:
    """Signal for one coin at a point in time."""
    symbol: str
    divergence_type: str | None   # "BULLISH", "BEARISH", None
    divergence_strength: float | None
    histogram_rising: bool
    histogram_value: float | None
    volume_ratio: float | None
    trend_1h: str
    price: float | None
    updated_at: float


class MacdDivergenceStrategy(Strategy):
    """MACD histogram divergence strategy on 15m candles."""

    def __init__(
        self,
        market_data: MarketData,
        trend_filter: TrendFilter,
        cooldown: CooldownTracker,
        position_tracker: PositionTracker,
        risk_config: RiskConfig,
        strategy_config: StrategyConfig,
        coins: list[str] | None = None,
        min_candle_volume_usdc: float = 10_000.0,
        db: Database | None = None,
    ) -> None:
        self._md = market_data
        self._trend = trend_filter
        self._cooldown = cooldown
        self._positions = position_tracker
        self._rc = risk_config
        self._sc = strategy_config
        self._coins = coins or []
        self._interval = "15m"
        self._min_candle_volume_usdc = min_candle_volume_usdc
        self._signals: dict[str, MacdDivSignal] = {}
        self._funding_rates: dict[str, float] = {}
        self._max_funding_rate: float = 0.0005
        self._db = db
        self._cycle_count: int = 0

        # Config params
        self._swing_window = strategy_config.macd_div_swing_window
        self._recency = strategy_config.macd_div_recency

    @property
    def strategy_type(self) -> str:
        return "macd_divergence"

    def set_coins(self, coins: list[str]) -> None:
        self._coins = coins
        logger.info("MacdDivergence coins updated: %d", len(coins))

    def set_funding_rates(self, rates: dict[str, float]) -> None:
        self._funding_rates = rates

    def set_max_funding_rate(self, rate: float) -> None:
        self._max_funding_rate = rate

    def set_cycle(self, cycle: int) -> None:
        self._cycle_count = cycle

    # -- Lifecycle --

    async def start(self) -> None:
        logger.info(
            "MacdDivergenceStrategy started -- scanning %d coins on %s "
            "(swing_window=%d, recency=%d)",
            len(self._coins), self._interval,
            self._swing_window, self._recency,
        )

    async def stop(self) -> None:
        self._signals.clear()
        logger.info("MacdDivergenceStrategy stopped")

    # -- Update (called every tick) --

    async def update(self) -> None:
        tasks = [self._scan_coin(sym) for sym in self._coins]
        await asyncio.gather(*tasks)

    async def _scan_coin(self, symbol: str) -> None:
        """Compute MACD histogram divergence for one coin on 15m candles."""
        try:
            df = self._md.get_candles(symbol, self._interval)
            min_bars = MACD_SLOW + self._recency
            if df is None or len(df) < min_bars:
                return

            close = df["close"]
            volume = df["volume"]
            price = float(close.iloc[-1])

            # Volume floor: skip illiquid coins
            if self._min_candle_volume_usdc > 0:
                volume_usdc = float(price * volume.iloc[-1])
                if volume_usdc < self._min_candle_volume_usdc:
                    return

            # Compute MACD histogram
            macd_ind = ta_lib.trend.MACD(
                close, window_slow=MACD_SLOW, window_fast=MACD_FAST, window_sign=MACD_SIGNAL,
            )
            histogram = macd_ind.macd_diff()

            if histogram.empty or pd.isna(histogram.iloc[-1]):
                return

            hist_value = float(histogram.iloc[-1])

            # Histogram direction: compare last bar to previous bar
            hist_prev = float(histogram.iloc[-2]) if len(histogram) >= 2 and pd.notna(histogram.iloc[-2]) else None
            histogram_rising = hist_value > hist_prev if hist_prev is not None else False

            # Volume ratio
            vol_sma = float(volume.rolling(VOLUME_SMA_PERIOD).mean().iloc[-1]) if len(volume) >= VOLUME_SMA_PERIOD else None
            vol_ratio = float(volume.iloc[-1]) / vol_sma if vol_sma and vol_sma > 0 else None

            # 1h trend from TrendFilter
            trend_state = self._trend.get_state(symbol)
            trend_1h = trend_state.get("trend", "NEUTRAL")

            # Swing detection for divergence
            divergence_type, divergence_strength = self._detect_divergence(close, histogram)

            self._signals[symbol] = MacdDivSignal(
                symbol=symbol,
                divergence_type=divergence_type,
                divergence_strength=divergence_strength,
                histogram_rising=histogram_rising,
                histogram_value=round(hist_value, 6),
                volume_ratio=round(vol_ratio, 2) if vol_ratio else None,
                trend_1h=trend_1h,
                price=price,
                updated_at=time.time(),
            )
        except Exception:
            logger.exception("Error scanning MACD Div %s", symbol)

    def _detect_divergence(
        self,
        close: pd.Series,
        histogram: pd.Series,
    ) -> tuple[str | None, float | None]:
        """Detect bullish or bearish MACD histogram divergence.

        Swing low: bar where close is lower than `swing_window` bars before AND after.
        Swing high: bar where close is higher than `swing_window` bars before AND after.

        Bullish: last 2 price swing lows -> lower low, histogram at those bars -> higher low.
        Bearish: last 2 price swing highs -> higher high, histogram at those bars -> lower high.

        Returns:
            (divergence_type, divergence_strength) or (None, None).
        """
        w = self._swing_window
        n = len(close)
        recency = self._recency

        # Only look within the last `recency` bars, but need w bars after for confirmation
        start_idx = max(w, n - recency - w)
        end_idx = n - w  # last confirmed bar (needs w bars after)

        if end_idx <= start_idx:
            return None, None

        # Find swing lows (price lower than w bars before AND after)
        swing_lows: list[int] = []
        swing_highs: list[int] = []

        for i in range(start_idx, end_idx):
            if pd.isna(histogram.iloc[i]):
                continue

            price_i = float(close.iloc[i])

            # Check swing low
            is_low = True
            for j in range(1, w + 1):
                if float(close.iloc[i - j]) <= price_i or float(close.iloc[i + j]) <= price_i:
                    is_low = False
                    break
            if is_low:
                swing_lows.append(i)

            # Check swing high
            is_high = True
            for j in range(1, w + 1):
                if float(close.iloc[i - j]) >= price_i or float(close.iloc[i + j]) >= price_i:
                    is_high = False
                    break
            if is_high:
                swing_highs.append(i)

        # Check bullish divergence (last 2 swing lows)
        if len(swing_lows) >= 2:
            prev_i = swing_lows[-2]
            curr_i = swing_lows[-1]
            prev_price = float(close.iloc[prev_i])
            curr_price = float(close.iloc[curr_i])
            prev_hist = float(histogram.iloc[prev_i])
            curr_hist = float(histogram.iloc[curr_i])

            # Price lower low + histogram higher low = bullish divergence
            if curr_price < prev_price and curr_hist > prev_hist:
                strength = self._compute_divergence_strength(
                    prev_price, curr_price, prev_hist, curr_hist,
                )
                return "BULLISH", strength

        # Check bearish divergence (last 2 swing highs)
        if len(swing_highs) >= 2:
            prev_i = swing_highs[-2]
            curr_i = swing_highs[-1]
            prev_price = float(close.iloc[prev_i])
            curr_price = float(close.iloc[curr_i])
            prev_hist = float(histogram.iloc[prev_i])
            curr_hist = float(histogram.iloc[curr_i])

            # Price higher high + histogram lower high = bearish divergence
            if curr_price > prev_price and curr_hist < prev_hist:
                strength = self._compute_divergence_strength(
                    prev_price, curr_price, prev_hist, curr_hist,
                )
                return "BEARISH", strength

        return None, None

    @staticmethod
    def _compute_divergence_strength(
        prev_price: float,
        curr_price: float,
        prev_hist: float,
        curr_hist: float,
    ) -> float:
        """Compute divergence strength as abs(price_change - histogram_change), normalized.

        Both changes are expressed as fractional changes so they are comparable.
        Result is capped at 1.0.
        """
        if prev_price == 0:
            return 0.0

        price_change = (curr_price - prev_price) / abs(prev_price)

        hist_range = max(abs(prev_hist), abs(curr_hist))
        if hist_range == 0:
            return 0.0

        hist_change = (curr_hist - prev_hist) / hist_range

        # Divergence = they moved in opposite directions, so the diff is large
        raw = abs(price_change - hist_change)
        return min(1.0, raw)

    # -- Decision generation --

    async def generate_decisions(self) -> list[Decision]:
        """Generate BUY/SHORT/CLOSE decisions based on MACD divergence rules."""
        decisions: list[Decision] = []
        open_symbols = await self._positions.get_open_symbols()

        # EXIT checks (only on positions opened by this strategy)
        for symbol in list(open_symbols):
            sig = self._signals.get(symbol)
            if not sig:
                continue

            pos = await self._positions.get_position_for_symbol(symbol)
            if not pos:
                continue

            if pos.get("strategy") != "macd_divergence":
                continue

            direction = pos.get("direction", "LONG")
            exit_d = self._check_exit(symbol, sig, direction)
            if exit_d:
                decisions.append(exit_d)

        # ENTRY checks
        scanned = 0
        for symbol in self._coins:
            if symbol in open_symbols:
                continue

            sig = self._signals.get(symbol)
            if not sig or sig.divergence_type is None:
                continue

            scanned += 1

            if sig.divergence_type == "BULLISH":
                entry = self._check_long_entry(symbol, sig)
            elif sig.divergence_type == "BEARISH":
                entry = self._check_short_entry(symbol, sig)
            else:
                entry = None

            if entry:
                decisions.append(entry)
                await self._log_signal(entry, sig)

        entries = sum(1 for d in decisions if d.action in ("BUY", "SHORT"))
        logger.info(
            "[MD SCAN] coins=%d, scanned=%d, signals_generated=%d",
            len(self._coins), scanned, entries,
        )
        return decisions

    def _check_exit(self, symbol: str, sig: MacdDivSignal, direction: str) -> Decision | None:
        """Check if a position should be closed based on histogram zero-cross."""
        if sig.histogram_value is None:
            return None

        reasons: list[str] = []
        if direction == "LONG" and sig.histogram_value < 0:
            reasons.append(f"histogram={sig.histogram_value:.6f}<0")
        elif direction == "SHORT" and sig.histogram_value > 0:
            reasons.append(f"histogram={sig.histogram_value:.6f}>0")

        if not reasons:
            return None

        return Decision(
            action="CLOSE",
            symbol=symbol,
            confidence=0.8,
            reasoning=f"[MacdDiv {direction} EXIT] {symbol}: {', '.join(reasons)}",
            strategy_type="macd_divergence",
            order_type="MARKET",
        )

    def _check_long_entry(self, symbol: str, sig: MacdDivSignal) -> Decision | None:
        """Score-based LONG entry from bullish MACD divergence."""
        if sig.price is None or sig.divergence_strength is None:
            return None

        # Hard blocks
        blocked, can_buy, abs_funding = check_hard_blocks(
            symbol,
            funding_rates=self._funding_rates,
            cooldown=self._cooldown,
            risk_config=self._rc,
        )
        if blocked:
            logger.debug("[MD LONG] %s -> HARD BLOCK", symbol)
            return None

        # Trend hard block: BEARISH blocks LONG
        if sig.trend_1h == "BEARISH":
            logger.debug("[MD LONG] %s -> HARD BLOCK: trend is BEARISH", symbol)
            return None

        # Scoring
        score = 0.0
        components: list[str] = []

        # Divergence strength (graduated)
        intensity = min(1.0, sig.divergence_strength)
        score += W_DIVERGENCE * intensity
        components.append(f"div_str={intensity:.2f}")

        # Histogram direction: should be rising for bullish
        if sig.histogram_rising:
            score += W_HISTOGRAM
            components.append("hist_rising")

        # Volume
        if sig.volume_ratio is not None and sig.volume_ratio >= MIN_VOLUME_RATIO:
            score += W_VOLUME
            components.append(f"vol={sig.volume_ratio:.1f}")

        # Trend alignment: BULLISH or NEUTRAL (BEARISH already blocked above)
        if sig.trend_1h == "BULLISH":
            score += W_TREND
            components.append("trend=BULL")
        elif sig.trend_1h == "NEUTRAL":
            score += W_TREND * 0.5
            components.append("trend=NEUT(0.5)")

        # Funding
        if abs_funding < self._max_funding_rate:
            score += W_FUNDING
            components.append("funding_ok")

        # Cooldown
        if can_buy:
            score += W_COOLDOWN
            components.append("no_cd")

        score = round(score, 4)

        if score < SCORE_THRESHOLD:
            logger.debug(
                "[MD LONG] %s: score=%.3f < %.2f -- div=%s, hist=%.6f, vol=%s, trend=%s",
                symbol, score, SCORE_THRESHOLD, sig.divergence_type,
                sig.histogram_value or 0.0,
                f"{sig.volume_ratio:.1f}" if sig.volume_ratio else "N/A",
                sig.trend_1h,
            )
            return None

        vol_str = f"{sig.volume_ratio:.1f}" if sig.volume_ratio is not None else "N/A"

        logger.debug("[MD LONG] %s: score=%.3f -- %s", symbol, score, ", ".join(components))
        return Decision(
            action="BUY",
            symbol=symbol,
            confidence=score,
            reasoning=(
                f"[MacdDiv LONG] {symbol}: score={score:.3f} -- "
                f"div_str={sig.divergence_strength:.2f}, "
                f"hist={sig.histogram_value:.6f}, "
                f"vol={vol_str}, trend={sig.trend_1h}"
            ),
            strategy_type="macd_divergence",
            order_type="MARKET",
        )

    def _check_short_entry(self, symbol: str, sig: MacdDivSignal) -> Decision | None:
        """Score-based SHORT entry from bearish MACD divergence."""
        if sig.price is None or sig.divergence_strength is None:
            return None

        # Hard blocks
        blocked, can_buy, abs_funding = check_hard_blocks(
            symbol,
            funding_rates=self._funding_rates,
            cooldown=self._cooldown,
            risk_config=self._rc,
        )
        if blocked:
            logger.debug("[MD SHORT] %s -> HARD BLOCK", symbol)
            return None

        # Trend hard block: BULLISH blocks SHORT
        if sig.trend_1h == "BULLISH":
            logger.debug("[MD SHORT] %s -> HARD BLOCK: trend is BULLISH", symbol)
            return None

        # Scoring
        score = 0.0
        components: list[str] = []

        # Divergence strength (graduated)
        intensity = min(1.0, sig.divergence_strength)
        score += W_DIVERGENCE * intensity
        components.append(f"div_str={intensity:.2f}")

        # Histogram direction: should be falling for bearish
        if not sig.histogram_rising:
            score += W_HISTOGRAM
            components.append("hist_falling")

        # Volume
        if sig.volume_ratio is not None and sig.volume_ratio >= MIN_VOLUME_RATIO:
            score += W_VOLUME
            components.append(f"vol={sig.volume_ratio:.1f}")

        # Trend alignment: BEARISH or NEUTRAL (BULLISH already blocked above)
        if sig.trend_1h == "BEARISH":
            score += W_TREND
            components.append("trend=BEAR")
        elif sig.trend_1h == "NEUTRAL":
            score += W_TREND * 0.5
            components.append("trend=NEUT(0.5)")

        # Funding
        if abs_funding < self._max_funding_rate:
            score += W_FUNDING
            components.append("funding_ok")

        # Cooldown
        if can_buy:
            score += W_COOLDOWN
            components.append("no_cd")

        score = round(score, 4)

        if score < SCORE_THRESHOLD:
            logger.debug(
                "[MD SHORT] %s: score=%.3f < %.2f -- div=%s, hist=%.6f, vol=%s, trend=%s",
                symbol, score, SCORE_THRESHOLD, sig.divergence_type,
                sig.histogram_value or 0.0,
                f"{sig.volume_ratio:.1f}" if sig.volume_ratio else "N/A",
                sig.trend_1h,
            )
            return None

        vol_str = f"{sig.volume_ratio:.1f}" if sig.volume_ratio is not None else "N/A"

        logger.debug("[MD SHORT] %s: score=%.3f -- %s", symbol, score, ", ".join(components))
        return Decision(
            action="SHORT",
            symbol=symbol,
            confidence=score,
            reasoning=(
                f"[MacdDiv SHORT] {symbol}: score={score:.3f} -- "
                f"div_str={sig.divergence_strength:.2f}, "
                f"hist={sig.histogram_value:.6f}, "
                f"vol={vol_str}, trend={sig.trend_1h}"
            ),
            strategy_type="macd_divergence",
            order_type="MARKET",
        )

    async def _log_signal(self, decision: Decision, sig: MacdDivSignal) -> None:
        if not self._db or self._cycle_count <= 0:
            return
        try:
            await self._db.insert_event(
                cycle=self._cycle_count,
                symbol=decision.symbol or "",
                event_type="SIGNAL",
                source="macd_divergence",
                action=decision.action,
                confidence=decision.confidence,
                reasoning=decision.reasoning,
                details={
                    "score": decision.confidence,
                    "divergence_type": sig.divergence_type,
                    "divergence_strength": sig.divergence_strength,
                    "histogram_value": sig.histogram_value,
                    "histogram_rising": sig.histogram_rising,
                    "volume_ratio": sig.volume_ratio,
                    "trend_1h": sig.trend_1h,
                },
            )
        except Exception:
            logger.debug("Failed to log SIGNAL event", exc_info=True)

    # -- State (for snapshot / dashboard) --

    def get_state(self) -> dict[str, Any]:
        signals: dict[str, Any] = {}
        bullish_divs: list[str] = []
        bearish_divs: list[str] = []

        for sym, sig in self._signals.items():
            signals[sym] = {
                "divergence_type": sig.divergence_type,
                "divergence_strength": sig.divergence_strength,
                "histogram_value": sig.histogram_value,
                "histogram_rising": sig.histogram_rising,
                "volume_ratio": sig.volume_ratio,
                "trend_1h": sig.trend_1h,
                "price": sig.price,
            }
            if sig.divergence_type == "BULLISH":
                bullish_divs.append(sym)
            elif sig.divergence_type == "BEARISH":
                bearish_divs.append(sym)

        return {
            "strategy": "macd_divergence",
            "interval": self._interval,
            "coins_scanned": len(self._signals),
            "bullish_divergences": bullish_divs,
            "bearish_divergences": bearish_divs,
            "signals": signals,
            "cooldown": self._cooldown.get_state(),
            "trend_filter": self._trend.get_all_states(),
        }
