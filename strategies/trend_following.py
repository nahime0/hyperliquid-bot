"""Trend Following — scoring-based decision generator.

Generates BUY/SHORT/CLOSE decisions for strong directional moves.
Unlike Mean Reversion (counter-trend), this captures trending markets
where MR cannot fire (RSI doesn't reach extremes in trends).

SHORT Entry scoring:
  - 4h price change < -3%           -> +0.30
  - 1h trend BEARISH (TrendFilter)  -> +0.20
  - 4h trend BEARISH (EMA12/EMA26)  -> +0.20
  - Volume ratio >= 1.0             -> +0.10
  - |funding| < max_funding_rate    -> +0.10
  - No per-symbol cooldown          -> +0.10
  Score >= 0.50 -> generate signal (confidence = score)

BUY Entry scoring (mirrored):
  - 4h price change > +3%           -> +0.30
  - 1h trend BULLISH (TrendFilter)  -> +0.20
  - 4h trend BULLISH (EMA12/EMA26)  -> +0.20
  - Volume ratio >= 1.0             -> +0.10
  - |funding| < max_funding_rate    -> +0.10
  - No per-symbol cooldown          -> +0.10

Hard blocks (always reject, bypass scoring):
  - |funding| >= 0.001 (extreme funding)
  - Global cooldown active (3+ consecutive losses)
  - Fresh per-symbol cooldown (loss < 10 min ago)

Exit (trend reversal):
  - SHORT exit: 1h trend turns BULLISH OR 4h trend turns BULLISH
  - LONG exit: 1h trend turns BEARISH OR 4h trend turns BEARISH
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any

import pandas as pd

from config.settings import RiskConfig, StrategyConfig
from core.types import Decision
from core.market_data import MarketData
from data.db import Database
from risk.position_tracker import PositionTracker
from strategies.base import Strategy
from strategies.cooldown import CooldownTracker
from strategies.trend_filter import TrendFilter
from utils.logger import get_logger

logger = get_logger(__name__)

# -- Scoring weights --
W_CHANGE_4H = 0.30       # 4h price change threshold met
W_TREND_1H = 0.20        # 1h trend alignment (TrendFilter)
W_TREND_4H = 0.20        # 4h trend alignment (EMA12/EMA26 on 1h)
W_VOLUME = 0.10           # volume ratio >= threshold
W_FUNDING = 0.10          # funding rate acceptable
W_COOLDOWN = 0.10         # no per-symbol cooldown
SCORE_THRESHOLD = 0.50    # minimum score to generate signal

# -- Thresholds --
CHANGE_4H_THRESHOLD = 3.0  # % price change over 4h to qualify
MIN_VOLUME_RATIO = 1.0     # volume ratio threshold

# -- Hard-block thresholds (shared with MR) --
HARD_FUNDING_RATE = 0.001     # extreme funding -> always block
HARD_COOLDOWN_FRESHNESS = 600  # block if loss < 10 min ago

# -- EMA params for 4h trend on 1h candles --
EMA_FAST_4H = 12
EMA_SLOW_4H = 26
SLOPE_WINDOW_4H = 5  # bars to measure EMA12 slope


@dataclass
class TrendSignal:
    """Signal for one coin at a point in time."""
    symbol: str
    change_4h: float | None      # % change over last 4 hours
    trend_1h: str                 # BULLISH, BEARISH, NEUTRAL (from TrendFilter)
    trend_4h: str                 # BULLISH, BEARISH, NEUTRAL (EMA12/EMA26)
    volume_ratio: float | None
    price: float | None
    updated_at: float


class TrendFollowingStrategy(Strategy):
    """Trend following strategy: captures strong directional moves."""

    def __init__(
        self,
        market_data: MarketData,
        trend_filter: TrendFilter,
        cooldown: CooldownTracker,
        position_tracker: PositionTracker,
        risk_config: RiskConfig,
        coins: list[str] | None = None,
        interval: str = "1h",
        min_candle_volume_usdc: float = 10_000.0,
        change_threshold: float = CHANGE_4H_THRESHOLD,
        db: Database | None = None,
    ) -> None:
        self._md = market_data
        self._trend = trend_filter
        self._cooldown = cooldown
        self._positions = position_tracker
        self._rc = risk_config
        self._coins = coins or []
        self._interval = interval
        self._min_candle_volume_usdc = min_candle_volume_usdc
        self._change_threshold = change_threshold
        self._signals: dict[str, TrendSignal] = {}
        self._funding_rates: dict[str, float] = {}
        self._max_funding_rate: float = 0.0005
        self._db = db
        self._cycle_count: int = 0

    def set_coins(self, coins: list[str]) -> None:
        self._coins = coins
        logger.info("TrendFollowing coins updated: %d", len(coins))

    def set_funding_rates(self, rates: dict[str, float]) -> None:
        self._funding_rates = rates

    def set_max_funding_rate(self, rate: float) -> None:
        self._max_funding_rate = rate

    def set_cycle(self, cycle: int) -> None:
        self._cycle_count = cycle

    # -- Lifecycle --

    async def start(self) -> None:
        logger.info(
            "TrendFollowingStrategy started — scanning %d coins on %s",
            len(self._coins), self._interval,
        )

    async def stop(self) -> None:
        self._signals.clear()
        logger.info("TrendFollowingStrategy stopped")

    # -- Update (called every tick) --

    async def update(self) -> None:
        tasks = [self._scan_coin(sym) for sym in self._coins]
        await asyncio.gather(*tasks)

    async def _scan_coin(self, symbol: str) -> None:
        """Compute trend signals for one coin using 1h candles."""
        try:
            df_1h = self._md.get_candles(symbol, self._interval)
            if df_1h is None or len(df_1h) < EMA_SLOW_4H + SLOPE_WINDOW_4H:
                return

            close = df_1h["close"]
            volume = df_1h["volume"]
            price = float(close.iloc[-1])

            # Volume floor
            if self._min_candle_volume_usdc > 0:
                volume_usdc = float(price * volume.iloc[-1])
                if volume_usdc < self._min_candle_volume_usdc:
                    return

            # 4h price change: close[-1] vs close[-5] (4 x 1h candles = 4h)
            change_4h = self._compute_change_4h(close)

            # 4h trend from EMA12/EMA26 on 1h
            trend_4h = self._compute_trend_4h(close, price)

            # 1h trend from TrendFilter (EMA50/EMA200)
            trend_state = self._trend.get_state(symbol)
            trend_1h = trend_state.get("trend", "NEUTRAL")

            # Volume ratio
            vol_sma = float(volume.rolling(20).mean().iloc[-1]) if len(volume) >= 20 else None
            vol_ratio = float(volume.iloc[-1]) / vol_sma if vol_sma and vol_sma > 0 else None

            self._signals[symbol] = TrendSignal(
                symbol=symbol,
                change_4h=round(change_4h, 3) if change_4h is not None else None,
                trend_1h=trend_1h,
                trend_4h=trend_4h,
                volume_ratio=round(vol_ratio, 2) if vol_ratio else None,
                price=price,
                updated_at=time.time(),
            )
        except Exception:
            logger.exception("Error scanning %s for trend", symbol)

    @staticmethod
    def _compute_change_4h(close: pd.Series) -> float | None:
        """Compute % change over last 4 hours from 1h candles."""
        if len(close) < 5:
            return None
        price_now = float(close.iloc[-1])
        price_4h_ago = float(close.iloc[-5])
        if price_4h_ago <= 0:
            return None
        return (price_now - price_4h_ago) / price_4h_ago * 100

    @staticmethod
    def _compute_trend_4h(close: pd.Series, price: float) -> str:
        """Compute 4h trend using EMA12/EMA26 on 1h candles."""
        if len(close) < EMA_SLOW_4H + SLOPE_WINDOW_4H:
            return "NEUTRAL"

        ema12 = close.ewm(span=EMA_FAST_4H, adjust=False).mean()
        ema26 = close.ewm(span=EMA_SLOW_4H, adjust=False).mean()
        ema12_now = float(ema12.iloc[-1])
        ema26_now = float(ema26.iloc[-1])

        # Slope: EMA12 change over last SLOPE_WINDOW_4H bars
        ema12_ago = float(ema12.iloc[-SLOPE_WINDOW_4H]) if len(ema12) >= SLOPE_WINDOW_4H else ema12_now
        slope = (ema12_now - ema12_ago) / price * 100 if price > 0 else 0

        if ema12_now > ema26_now and price > ema12_now and slope > 0:
            return "BULLISH"
        elif ema12_now < ema26_now and price < ema12_now and slope < 0:
            return "BEARISH"
        return "NEUTRAL"

    # -- Decision generation --

    async def generate_decisions(self) -> list[Decision]:
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

            if pos.get("strategy") != "trend_following":
                continue

            direction = pos.get("direction", "LONG")
            if direction == "LONG":
                exit_d = self._check_long_exit(symbol, sig)
            else:
                exit_d = self._check_short_exit(symbol, sig)

            if exit_d:
                decisions.append(exit_d)

        # ENTRY checks
        scanned = 0
        for symbol in self._coins:
            if symbol in open_symbols:
                continue

            sig = self._signals.get(symbol)
            if not sig:
                continue

            scanned += 1

            # Check SHORT first (trend following is primarily for catching downtrends)
            entry = self._check_short_entry(symbol, sig)
            if entry:
                decisions.append(entry)
                await self._log_signal(entry, sig)
                continue

            entry = self._check_long_entry(symbol, sig)
            if entry:
                decisions.append(entry)
                await self._log_signal(entry, sig)

        entries = sum(1 for d in decisions if d.action in ("BUY", "SHORT"))
        logger.info(
            "[TF SCAN] coins=%d, scanned=%d, signals_generated=%d",
            len(self._coins), scanned, entries,
        )
        return decisions

    def _check_short_entry(self, symbol: str, sig: TrendSignal) -> Decision | None:
        """Score-based SHORT entry for downtrends."""
        if sig.price is None or sig.change_4h is None:
            return None

        # Hard blocks
        funding_rate = abs(self._funding_rates.get(symbol, 0.0))
        if funding_rate >= HARD_FUNDING_RATE:
            return None

        if self._cooldown.is_global_cooldown_active():
            return None

        can_buy, _ = self._cooldown.can_buy(symbol)
        if not can_buy:
            remaining = self._cooldown.get_symbol_cooldown_remaining(symbol)
            time_since = self._rc.symbol_cooldown_sec - remaining
            if time_since < HARD_COOLDOWN_FRESHNESS:
                return None

        # Scoring
        score = 0.0
        components: list[str] = []

        if sig.change_4h <= -self._change_threshold:
            score += W_CHANGE_4H
            components.append(f"chg4h={sig.change_4h:.1f}%")

        if sig.trend_1h == "BEARISH":
            score += W_TREND_1H
            components.append("trend_1h=BEAR")

        if sig.trend_4h == "BEARISH":
            score += W_TREND_4H
            components.append("trend_4h=BEAR")

        if sig.volume_ratio is not None and sig.volume_ratio >= MIN_VOLUME_RATIO:
            score += W_VOLUME
            components.append(f"vol={sig.volume_ratio:.1f}")

        if funding_rate < self._max_funding_rate:
            score += W_FUNDING
            components.append("funding_ok")

        if can_buy:
            score += W_COOLDOWN
            components.append("no_cd")

        score = round(score, 2)

        if score < SCORE_THRESHOLD:
            return None

        vol_str = f"{sig.volume_ratio:.1f}" if sig.volume_ratio is not None else "N/A"

        logger.debug("[TF SHORT] %s: score=%.2f — %s", symbol, score, ", ".join(components))
        return Decision(
            action="SHORT",
            symbol=symbol,
            confidence=score,
            reasoning=(
                f"[TrendFollow SHORT] {symbol}: score={score:.2f} — "
                f"chg4h={sig.change_4h:.1f}%, trend_1h={sig.trend_1h}, "
                f"trend_4h={sig.trend_4h}, vol={vol_str}"
            ),
            strategy_type="trend_following",
            order_type="MARKET",
        )

    def _check_long_entry(self, symbol: str, sig: TrendSignal) -> Decision | None:
        """Score-based LONG entry for uptrends."""
        if sig.price is None or sig.change_4h is None:
            return None

        # Hard blocks
        funding_rate = abs(self._funding_rates.get(symbol, 0.0))
        if funding_rate >= HARD_FUNDING_RATE:
            return None

        if self._cooldown.is_global_cooldown_active():
            return None

        can_buy, _ = self._cooldown.can_buy(symbol)
        if not can_buy:
            remaining = self._cooldown.get_symbol_cooldown_remaining(symbol)
            time_since = self._rc.symbol_cooldown_sec - remaining
            if time_since < HARD_COOLDOWN_FRESHNESS:
                return None

        # Scoring
        score = 0.0
        components: list[str] = []

        if sig.change_4h >= self._change_threshold:
            score += W_CHANGE_4H
            components.append(f"chg4h=+{sig.change_4h:.1f}%")

        if sig.trend_1h == "BULLISH":
            score += W_TREND_1H
            components.append("trend_1h=BULL")

        if sig.trend_4h == "BULLISH":
            score += W_TREND_4H
            components.append("trend_4h=BULL")

        if sig.volume_ratio is not None and sig.volume_ratio >= MIN_VOLUME_RATIO:
            score += W_VOLUME
            components.append(f"vol={sig.volume_ratio:.1f}")

        if funding_rate < self._max_funding_rate:
            score += W_FUNDING
            components.append("funding_ok")

        if can_buy:
            score += W_COOLDOWN
            components.append("no_cd")

        score = round(score, 2)

        if score < SCORE_THRESHOLD:
            return None

        vol_str = f"{sig.volume_ratio:.1f}" if sig.volume_ratio is not None else "N/A"

        logger.debug("[TF LONG] %s: score=%.2f — %s", symbol, score, ", ".join(components))
        return Decision(
            action="BUY",
            symbol=symbol,
            confidence=score,
            reasoning=(
                f"[TrendFollow LONG] {symbol}: score={score:.2f} — "
                f"chg4h=+{sig.change_4h:.1f}%, trend_1h={sig.trend_1h}, "
                f"trend_4h={sig.trend_4h}, vol={vol_str}"
            ),
            strategy_type="trend_following",
            order_type="MARKET",
        )

    def _check_short_exit(self, symbol: str, sig: TrendSignal) -> Decision | None:
        """Exit SHORT if trend reverses to BULLISH."""
        reasons: list[str] = []

        if sig.trend_1h == "BULLISH":
            reasons.append("trend_1h=BULLISH")
        if sig.trend_4h == "BULLISH":
            reasons.append("trend_4h=BULLISH")

        if not reasons:
            return None

        return Decision(
            action="CLOSE",
            symbol=symbol,
            confidence=0.8,
            reasoning=f"[TrendFollow SHORT EXIT] {symbol}: {', '.join(reasons)}",
            strategy_type="trend_following",
            order_type="MARKET",
        )

    def _check_long_exit(self, symbol: str, sig: TrendSignal) -> Decision | None:
        """Exit LONG if trend reverses to BEARISH."""
        reasons: list[str] = []

        if sig.trend_1h == "BEARISH":
            reasons.append("trend_1h=BEARISH")
        if sig.trend_4h == "BEARISH":
            reasons.append("trend_4h=BEARISH")

        if not reasons:
            return None

        return Decision(
            action="CLOSE",
            symbol=symbol,
            confidence=0.8,
            reasoning=f"[TrendFollow LONG EXIT] {symbol}: {', '.join(reasons)}",
            strategy_type="trend_following",
            order_type="MARKET",
        )

    async def _log_signal(self, decision: Decision, sig: TrendSignal) -> None:
        if not self._db or self._cycle_count <= 0:
            return
        try:
            await self._db.insert_event(
                cycle=self._cycle_count,
                symbol=decision.symbol or "",
                event_type="SIGNAL",
                source="trend_following",
                action=decision.action,
                confidence=decision.confidence,
                reasoning=decision.reasoning,
                details={
                    "score": decision.confidence,
                    "change_4h": sig.change_4h,
                    "trend_1h": sig.trend_1h,
                    "trend_4h": sig.trend_4h,
                    "volume_ratio": sig.volume_ratio,
                },
            )
        except Exception:
            logger.debug("Failed to log SIGNAL event", exc_info=True)

    # -- State (for snapshot / dashboard) --

    def get_state(self) -> dict[str, Any]:
        signals: dict[str, Any] = {}
        bearish: list[str] = []
        bullish: list[str] = []

        for sym, sig in self._signals.items():
            signals[sym] = {
                "change_4h": sig.change_4h,
                "trend_1h": sig.trend_1h,
                "trend_4h": sig.trend_4h,
                "volume_ratio": sig.volume_ratio,
                "price": sig.price,
            }
            if sig.trend_4h == "BEARISH" and sig.change_4h is not None and sig.change_4h <= -self._change_threshold:
                bearish.append(sym)
            elif sig.trend_4h == "BULLISH" and sig.change_4h is not None and sig.change_4h >= self._change_threshold:
                bullish.append(sym)

        return {
            "strategy": "trend_following",
            "interval": self._interval,
            "coins_scanned": len(self._signals),
            "bearish_trending": bearish,
            "bullish_trending": bullish,
            "signals": signals,
            "cooldown": self._cooldown.get_state(),
            "trend_filter": self._trend.get_all_states(),
        }
