"""Volume Spike Reversal — scoring-based decision generator.

Generates BUY/SHORT/CLOSE decisions based on volume spikes combined
with reversal candle patterns (hammer, inverted hammer / engulfing).
Uses 5m candles for fast reaction to spike-and-reverse events.

LONG Entry scoring:
  - Volume spike >= 3x 20-bar avg   -> +0.30  (graduated by magnitude)
  - Hammer candle pattern detected   -> +0.25  (graduated by wick ratio)
  - Price near lower Bollinger Band  -> +0.15  (BB %B < 0.3)
  - 1h trend not BEARISH             -> +0.10
  - |funding| < max_funding_rate     -> +0.10
  - No per-symbol cooldown           -> +0.10
  Score >= 0.60 -> generate signal (confidence = score)

SHORT Entry scoring (mirrored):
  - Volume spike >= 3x 20-bar avg   -> +0.30  (graduated by magnitude)
  - Inverted hammer pattern detected -> +0.25  (graduated by wick ratio)
  - Price near upper Bollinger Band  -> +0.15  (BB %B > 0.7)
  - 1h trend not BULLISH             -> +0.10
  - |funding| < max_funding_rate     -> +0.10
  - No per-symbol cooldown           -> +0.10

Hard blocks (always reject, bypass scoring):
  - |funding| >= 0.001 (extreme funding)
  - Global cooldown active (3+ consecutive losses)
  - Fresh per-symbol cooldown (loss < 10 min ago)

Exit:
  - LONG: RSI > 70
  - SHORT: RSI < 30
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

# -- Scoring weights --
W_SPIKE = 0.30            # volume spike magnitude
W_PATTERN = 0.25          # reversal candle pattern
W_PRICE_LEVEL = 0.15      # price near BB extreme
W_TREND = 0.10            # 1h trend alignment
W_FUNDING = 0.10          # funding rate acceptable
W_COOLDOWN = 0.10         # no per-symbol cooldown
SCORE_THRESHOLD = 0.60    # minimum score to generate signal

# -- Indicator params --
RSI_PERIOD = 14
BB_PERIOD = 20
BB_STD = 2.0
VOLUME_SMA_PERIOD = 20
MIN_CANDLES = 26          # need >= 26 candles for BB(20) + volume SMA(20) excl. spike bar

# -- Exit thresholds --
RSI_EXIT_LONG = 70.0      # LONG exit when RSI > 70
RSI_EXIT_SHORT = 30.0     # SHORT exit when RSI < 30

# -- BB %B thresholds for price_level scoring --
BB_PCT_BUY = 0.3          # BUY: %B < 0.3 (near lower band)
BB_PCT_SHORT = 0.7        # SHORT: %B > 0.7 (near upper band)

# -- Body epsilon (avoid division by zero) --
BODY_EPSILON = 0.0001


@dataclass
class VolumeSignal:
    """Signal for one coin at a point in time."""
    symbol: str
    spike_ratio: float | None       # current vol / SMA(20)
    candle_pattern: str | None       # "HAMMER", "INV_HAMMER", None
    pattern_strength: float | None   # 0-1 based on wick ratio
    rsi: float | None
    bb_pct: float | None             # Bollinger %B
    volume_ratio: float | None       # same as spike_ratio (for state)
    trend_1h: str                    # BULLISH, BEARISH, NEUTRAL
    price: float | None
    updated_at: float


class VolumeSpikeStrategy(Strategy):
    """Volume spike reversal strategy: captures reversals after high-volume candles."""

    def __init__(
        self,
        market_data: MarketData,
        trend_filter: TrendFilter,
        cooldown: CooldownTracker,
        position_tracker: PositionTracker,
        risk_config: RiskConfig,
        coins: list[str] | None = None,
        interval: str = "5m",
        min_candle_volume_usdc: float = 10_000.0,
        spike_threshold: float = 3.0,
        wick_ratio: float = 2.0,
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
        self._spike_threshold = spike_threshold
        self._wick_ratio = wick_ratio
        self._signals: dict[str, VolumeSignal] = {}
        self._funding_rates: dict[str, float] = {}
        self._max_funding_rate: float = 0.0005
        self._db = db
        self._cycle_count: int = 0

    def set_coins(self, coins: list[str]) -> None:
        self._coins = coins
        logger.info("VolumeSpike coins updated: %d", len(coins))

    def set_funding_rates(self, rates: dict[str, float]) -> None:
        self._funding_rates = rates

    def set_max_funding_rate(self, rate: float) -> None:
        self._max_funding_rate = rate

    def set_cycle(self, cycle: int) -> None:
        self._cycle_count = cycle

    # -- Lifecycle --

    async def start(self) -> None:
        logger.info(
            "VolumeSpikeStrategy started — scanning %d coins on %s",
            len(self._coins), self._interval,
        )

    async def stop(self) -> None:
        self._signals.clear()
        logger.info("VolumeSpikeStrategy stopped")

    # -- Update (called every tick) --

    async def update(self) -> None:
        tasks = [self._scan_coin(sym) for sym in self._coins]
        await asyncio.gather(*tasks)

    async def _scan_coin(self, symbol: str) -> None:
        """Compute volume spike and candle pattern indicators for one coin."""
        try:
            df = self._md.get_candles(symbol, self._interval)
            if df is None or len(df) < MIN_CANDLES:
                return

            close = df["close"]
            high = df["high"]
            low = df["low"]
            open_ = df["open"]
            volume = df["volume"]
            price = float(close.iloc[-1])

            # Volume floor: skip illiquid coins
            if self._min_candle_volume_usdc > 0:
                volume_usdc = float(price * volume.iloc[-1])
                if volume_usdc < self._min_candle_volume_usdc:
                    return

            # Volume spike ratio
            vol_sma = float(volume.rolling(VOLUME_SMA_PERIOD).mean().iloc[-2])
            spike_ratio = float(volume.iloc[-1]) / vol_sma if vol_sma > 0 else None

            # Candle pattern detection on last candle
            candle_pattern, pattern_strength = self._detect_pattern(
                open_val=float(open_.iloc[-1]),
                close_val=float(close.iloc[-1]),
                high_val=float(high.iloc[-1]),
                low_val=float(low.iloc[-1]),
            )

            # RSI
            rsi_series = ta_lib.momentum.RSIIndicator(close, window=RSI_PERIOD).rsi()
            rsi = float(rsi_series.iloc[-1]) if not rsi_series.empty and pd.notna(rsi_series.iloc[-1]) else None

            # Bollinger Band %B
            bb_pct = None
            try:
                bb = ta_lib.volatility.BollingerBands(close, window=BB_PERIOD, window_dev=BB_STD)
                bb_lower = float(bb.bollinger_lband().iloc[-1])
                bb_upper = float(bb.bollinger_hband().iloc[-1])
                bb_range = bb_upper - bb_lower
                if bb_range > 0:
                    bb_pct = round((price - bb_lower) / bb_range, 4)
            except Exception:
                pass  # BB not available — skip price_level component

            # 1h trend from TrendFilter
            trend_state = self._trend.get_state(symbol)
            trend_1h = trend_state.get("trend", "NEUTRAL")

            self._signals[symbol] = VolumeSignal(
                symbol=symbol,
                spike_ratio=round(spike_ratio, 2) if spike_ratio is not None else None,
                candle_pattern=candle_pattern,
                pattern_strength=round(pattern_strength, 3) if pattern_strength is not None else None,
                rsi=round(rsi, 2) if rsi is not None else None,
                bb_pct=bb_pct,
                volume_ratio=round(spike_ratio, 2) if spike_ratio is not None else None,
                trend_1h=trend_1h,
                price=price,
                updated_at=time.time(),
            )
        except Exception:
            logger.exception("Error scanning %s for volume spike", symbol)

    def _detect_pattern(
        self,
        open_val: float,
        close_val: float,
        high_val: float,
        low_val: float,
    ) -> tuple[str | None, float | None]:
        """Detect reversal candle pattern via wick/body ratio heuristic.

        Returns:
            (pattern_name, strength) where strength is 0-1.
            pattern_name is "HAMMER" (bullish) or "INV_HAMMER" (bearish) or None.
        """
        body = abs(close_val - open_val)
        if body < BODY_EPSILON:
            body = BODY_EPSILON

        lower_wick = min(open_val, close_val) - low_val
        upper_wick = high_val - max(open_val, close_val)

        # Hammer (bullish reversal): close > open, lower wick > body * threshold
        if close_val > open_val and lower_wick > body * self._wick_ratio:
            ratio = lower_wick / body
            # Strength: 1.0 if wick ratio >= 2 * threshold, else proportional
            strength = min(1.0, ratio / (self._wick_ratio * 2))
            return "HAMMER", strength

        # Inverted hammer (bearish reversal): close < open, upper wick > body * threshold
        if close_val < open_val and upper_wick > body * self._wick_ratio:
            ratio = upper_wick / body
            strength = min(1.0, ratio / (self._wick_ratio * 2))
            return "INV_HAMMER", strength

        return None, None

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

            if pos.get("strategy") != "volume_spike":
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

            entry = self._check_long_entry(symbol, sig)
            if entry:
                decisions.append(entry)
                await self._log_signal(entry, sig)
                continue  # one direction per coin per cycle

            entry = self._check_short_entry(symbol, sig)
            if entry:
                decisions.append(entry)
                await self._log_signal(entry, sig)

        entries = sum(1 for d in decisions if d.action in ("BUY", "SHORT"))
        logger.info(
            "[VS SCAN] coins=%d, scanned=%d, signals_generated=%d",
            len(self._coins), scanned, entries,
        )
        return decisions

    def _check_long_entry(self, symbol: str, sig: VolumeSignal) -> Decision | None:
        """Score-based LONG entry: volume spike + hammer pattern."""
        if sig.price is None or sig.spike_ratio is None:
            return None

        # Must have a volume spike at minimum
        if sig.spike_ratio < self._spike_threshold:
            return None

        # RSI guard: don't enter LONG if RSI already near exit threshold
        if sig.rsi is not None and sig.rsi > 55.0:
            return None

        # Hard blocks
        blocked, can_buy, abs_funding = check_hard_blocks(
            symbol,
            funding_rates=self._funding_rates,
            cooldown=self._cooldown,
            risk_config=self._rc,
        )
        if blocked:
            logger.debug("[VS LONG] %s -> HARD BLOCK", symbol)
            return None

        # Hard requirement: must have a hammer reversal candle
        if sig.candle_pattern != "HAMMER":
            return None

        # Scoring
        score = 0.0
        components: list[str] = []

        # Spike magnitude: graduated — bigger spike scores higher
        spike_intensity = min(1.0, (sig.spike_ratio - self._spike_threshold) / self._spike_threshold)
        score += W_SPIKE * max(0.2, spike_intensity)  # floor 0.2 since spike >= threshold
        components.append(f"spike={sig.spike_ratio:.1f}x(i={spike_intensity:.2f})")

        # Candle pattern: hammer
        if sig.candle_pattern == "HAMMER" and sig.pattern_strength is not None:
            score += W_PATTERN * sig.pattern_strength
            components.append(f"hammer(s={sig.pattern_strength:.2f})")

        # Price level: near lower BB
        if sig.bb_pct is not None and sig.bb_pct < BB_PCT_BUY:
            score += W_PRICE_LEVEL
            components.append(f"bb_pct={sig.bb_pct:.2f}<{BB_PCT_BUY}")

        # Trend: 1h not BEARISH
        if sig.trend_1h != "BEARISH":
            score += W_TREND
            components.append(f"trend={sig.trend_1h}")

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
                "[VS LONG] %s: score=%.3f < %.2f — spike=%.1fx, pattern=%s, bb_pct=%s",
                symbol, score, SCORE_THRESHOLD, sig.spike_ratio,
                sig.candle_pattern or "none",
                f"{sig.bb_pct:.2f}" if sig.bb_pct is not None else "N/A",
            )
            return None

        logger.debug("[VS LONG] %s: score=%.3f — %s", symbol, score, ", ".join(components))
        expected_move = sig.pattern_strength * 2.0 if sig.pattern_strength is not None else 0.5
        return Decision(
            action="BUY",
            symbol=symbol,
            confidence=score,
            reasoning=(
                f"[VolSpike LONG] {symbol}: score={score:.3f} — "
                f"spike={sig.spike_ratio:.1f}x, pattern={sig.candle_pattern or 'none'}, "
                f"bb_pct={f'{sig.bb_pct:.2f}' if sig.bb_pct is not None else 'N/A'}, "
                f"trend={sig.trend_1h}"
            ),
            strategy_type="volume_spike",
            order_type="MARKET",
            expected_move_pct=round(expected_move, 2),
        )

    def _check_short_entry(self, symbol: str, sig: VolumeSignal) -> Decision | None:
        """Score-based SHORT entry: volume spike + inverted hammer pattern."""
        if sig.price is None or sig.spike_ratio is None:
            return None

        # Must have a volume spike at minimum
        if sig.spike_ratio < self._spike_threshold:
            return None

        # RSI guard: don't enter SHORT if RSI already near exit threshold
        if sig.rsi is not None and sig.rsi < 45.0:
            return None

        # Hard blocks
        blocked, can_buy, abs_funding = check_hard_blocks(
            symbol,
            funding_rates=self._funding_rates,
            cooldown=self._cooldown,
            risk_config=self._rc,
        )
        if blocked:
            logger.debug("[VS SHORT] %s -> HARD BLOCK", symbol)
            return None

        # Hard requirement: must have an inverted hammer reversal candle
        if sig.candle_pattern != "INV_HAMMER":
            return None

        # Scoring
        score = 0.0
        components: list[str] = []

        # Spike magnitude: graduated
        spike_intensity = min(1.0, (sig.spike_ratio - self._spike_threshold) / self._spike_threshold)
        score += W_SPIKE * max(0.2, spike_intensity)  # floor 0.2 since spike >= threshold
        components.append(f"spike={sig.spike_ratio:.1f}x(i={spike_intensity:.2f})")

        # Candle pattern: inverted hammer
        if sig.candle_pattern == "INV_HAMMER" and sig.pattern_strength is not None:
            score += W_PATTERN * sig.pattern_strength
            components.append(f"inv_hammer(s={sig.pattern_strength:.2f})")

        # Price level: near upper BB
        if sig.bb_pct is not None and sig.bb_pct > BB_PCT_SHORT:
            score += W_PRICE_LEVEL
            components.append(f"bb_pct={sig.bb_pct:.2f}>{BB_PCT_SHORT}")

        # Trend: 1h not BULLISH
        if sig.trend_1h != "BULLISH":
            score += W_TREND
            components.append(f"trend={sig.trend_1h}")

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
                "[VS SHORT] %s: score=%.3f < %.2f — spike=%.1fx, pattern=%s, bb_pct=%s",
                symbol, score, SCORE_THRESHOLD, sig.spike_ratio,
                sig.candle_pattern or "none",
                f"{sig.bb_pct:.2f}" if sig.bb_pct is not None else "N/A",
            )
            return None

        logger.debug("[VS SHORT] %s: score=%.3f — %s", symbol, score, ", ".join(components))
        expected_move = sig.pattern_strength * 2.0 if sig.pattern_strength is not None else 0.5
        return Decision(
            action="SHORT",
            symbol=symbol,
            confidence=score,
            reasoning=(
                f"[VolSpike SHORT] {symbol}: score={score:.3f} — "
                f"spike={sig.spike_ratio:.1f}x, pattern={sig.candle_pattern or 'none'}, "
                f"bb_pct={f'{sig.bb_pct:.2f}' if sig.bb_pct is not None else 'N/A'}, "
                f"trend={sig.trend_1h}"
            ),
            strategy_type="volume_spike",
            order_type="MARKET",
            expected_move_pct=round(expected_move, 2),
        )

    def _check_long_exit(self, symbol: str, sig: VolumeSignal) -> Decision | None:
        """Exit LONG when RSI > 70 OR price crossed above BB midline (profit taking).

        RSI on 5m is very volatile; BB midline gives a price-based profit target
        since volume spike entries happen near the lower band.
        Require BOTH RSI > 60 AND %B > 0.5 for the BB-based exit to reduce noise.
        """
        reasons: list[str] = []

        # Primary: RSI overbought
        if sig.rsi is not None and sig.rsi > RSI_EXIT_LONG:
            reasons.append(f"RSI={sig.rsi:.1f}>{RSI_EXIT_LONG:.0f}")

        # Secondary: price above BB midline with RSI confirmation
        if not reasons and sig.bb_pct is not None and sig.rsi is not None:
            if sig.bb_pct > 0.5 and sig.rsi > 60:
                reasons.append(f"bb_pct={sig.bb_pct:.2f}>0.5+RSI={sig.rsi:.1f}>60")

        if not reasons:
            return None

        return Decision(
            action="CLOSE",
            symbol=symbol,
            confidence=0.8,
            reasoning=f"[VolSpike LONG EXIT] {symbol}: {', '.join(reasons)}",
            strategy_type="volume_spike",
            order_type="MARKET",
        )

    def _check_short_exit(self, symbol: str, sig: VolumeSignal) -> Decision | None:
        """Exit SHORT when RSI < 30 OR price crossed below BB midline."""
        reasons: list[str] = []

        # Primary: RSI oversold
        if sig.rsi is not None and sig.rsi < RSI_EXIT_SHORT:
            reasons.append(f"RSI={sig.rsi:.1f}<{RSI_EXIT_SHORT:.0f}")

        # Secondary: price below BB midline with RSI confirmation
        if not reasons and sig.bb_pct is not None and sig.rsi is not None:
            if sig.bb_pct < 0.5 and sig.rsi < 40:
                reasons.append(f"bb_pct={sig.bb_pct:.2f}<0.5+RSI={sig.rsi:.1f}<40")

        if not reasons:
            return None

        return Decision(
            action="CLOSE",
            symbol=symbol,
            confidence=0.8,
            reasoning=f"[VolSpike SHORT EXIT] {symbol}: {', '.join(reasons)}",
            strategy_type="volume_spike",
            order_type="MARKET",
        )

    async def _log_signal(self, decision: Decision, sig: VolumeSignal) -> None:
        if not self._db or self._cycle_count <= 0:
            return
        try:
            await self._db.insert_event(
                cycle=self._cycle_count,
                symbol=decision.symbol or "",
                event_type="SIGNAL",
                source="volume_spike",
                action=decision.action,
                confidence=decision.confidence,
                reasoning=decision.reasoning,
                details={
                    "score": decision.confidence,
                    "spike_ratio": sig.spike_ratio,
                    "candle_pattern": sig.candle_pattern,
                    "pattern_strength": sig.pattern_strength,
                    "rsi": sig.rsi,
                    "bb_pct": sig.bb_pct,
                    "trend_1h": sig.trend_1h,
                    "expected_move_pct": round(sig.pattern_strength * 2.0, 2) if sig.pattern_strength is not None else None,
                },
            )
        except Exception:
            logger.debug("Failed to log SIGNAL event", exc_info=True)

    # -- State (for snapshot / dashboard) --

    def get_state(self) -> dict[str, Any]:
        signals: dict[str, Any] = {}
        spike_longs: list[str] = []
        spike_shorts: list[str] = []

        for sym, sig in self._signals.items():
            signals[sym] = {
                "spike_ratio": sig.spike_ratio,
                "candle_pattern": sig.candle_pattern,
                "pattern_strength": sig.pattern_strength,
                "rsi": sig.rsi,
                "bb_pct": sig.bb_pct,
                "volume_ratio": sig.volume_ratio,
                "trend_1h": sig.trend_1h,
                "price": sig.price,
            }
            if (
                sig.spike_ratio is not None
                and sig.spike_ratio >= self._spike_threshold
            ):
                if sig.candle_pattern == "HAMMER":
                    spike_longs.append(sym)
                elif sig.candle_pattern == "INV_HAMMER":
                    spike_shorts.append(sym)

        return {
            "strategy": "volume_spike",
            "interval": self._interval,
            "coins_scanned": len(self._signals),
            "spike_longs": spike_longs,
            "spike_shorts": spike_shorts,
            "signals": signals,
            "cooldown": self._cooldown.get_state(),
            "trend_filter": self._trend.get_all_states(),
        }
