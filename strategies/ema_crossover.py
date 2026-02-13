"""EMA Crossover — scoring-based decision generator.

Generates BUY/SHORT/CLOSE decisions based on EMA9/EMA21 crossovers on 15m candles.
A golden cross (EMA9 crosses above EMA21) signals BUY, a death cross signals SHORT.

LONG Entry scoring:
  - Golden cross strength (normalized)  -> +0.30
  - 1h trend BULLISH (TrendFilter)      -> +0.20
  - Volume ratio >= 1.0                 -> +0.15
  - RSI < 60 (confirmation)             -> +0.15
  - |funding| < max_funding_rate        -> +0.10
  - No per-symbol cooldown              -> +0.10
  Score >= 0.50 -> generate signal (confidence = score)

SHORT Entry scoring (mirrored):
  - Death cross strength (normalized)   -> +0.30
  - 1h trend BEARISH (TrendFilter)      -> +0.20
  - Volume ratio >= 1.0                 -> +0.15
  - RSI > 40 (confirmation)             -> +0.15
  - |funding| < max_funding_rate        -> +0.10
  - No per-symbol cooldown              -> +0.10

Hard blocks (always reject, bypass scoring):
  - |funding| >= 0.001 (extreme funding)
  - Global cooldown active (3+ consecutive losses)
  - Fresh per-symbol cooldown (loss < 10 min ago)

Exit:
  - LONG: EMA9 crosses back below EMA21 (death cross)
  - SHORT: EMA9 crosses back above EMA21 (golden cross)
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
W_CROSS = 0.30       # cross strength (normalized)
W_TREND = 0.20       # 1h trend alignment (TrendFilter)
W_VOLUME = 0.15      # volume ratio >= threshold
W_RSI = 0.15         # RSI confirmation
W_FUNDING = 0.10     # funding rate acceptable
W_COOLDOWN = 0.10    # no per-symbol cooldown
SCORE_THRESHOLD = 0.50

# -- Thresholds --
MIN_VOLUME_RATIO = 1.0
RSI_PERIOD = 14
CROSS_STRENGTH_CAP = 1.0  # cap cross_strength at 1% of price


@dataclass
class EmaCrossSignal:
    """Signal for one coin at a point in time."""
    symbol: str
    cross_type: str | None     # "GOLDEN", "DEATH", None
    cross_strength: float | None
    trend_1h: str
    volume_ratio: float | None
    rsi: float | None
    price: float | None
    ema_fast: float | None
    ema_slow: float | None
    updated_at: float


class EmaCrossoverStrategy(Strategy):
    """EMA crossover strategy: captures momentum shifts via EMA9/EMA21 crosses."""

    def __init__(
        self,
        market_data: MarketData,
        trend_filter: TrendFilter,
        cooldown: CooldownTracker,
        position_tracker: PositionTracker,
        risk_config: RiskConfig,
        strategy_config: StrategyConfig,
        coins: list[str] | None = None,
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
        self._ema_fast_period: int = strategy_config.ema_cross_fast
        self._ema_slow_period: int = strategy_config.ema_cross_slow
        self._max_bars: int = strategy_config.ema_cross_max_bars
        self._signals: dict[str, EmaCrossSignal] = {}
        self._funding_rates: dict[str, float] = {}
        self._max_funding_rate: float = 0.0005
        self._db = db
        self._cycle_count: int = 0

    # -- Setters --

    def set_coins(self, coins: list[str]) -> None:
        """Update the list of coins to scan (for dynamic discovery)."""
        self._coins = coins
        logger.info("EmaCrossover coins updated: %d", len(coins))

    def set_funding_rates(self, rates: dict[str, float]) -> None:
        """Update cached funding rates (from main loop)."""
        self._funding_rates = rates

    def set_max_funding_rate(self, rate: float) -> None:
        """Set max acceptable funding rate from settings."""
        self._max_funding_rate = rate

    def set_cycle(self, cycle: int) -> None:
        self._cycle_count = cycle

    # -- Lifecycle --

    async def start(self) -> None:
        logger.info(
            "EmaCrossoverStrategy started — scanning %d coins on %s (EMA%d/%d, max_bars=%d)",
            len(self._coins), self._interval,
            self._ema_fast_period, self._ema_slow_period, self._max_bars,
        )

    async def stop(self) -> None:
        self._signals.clear()
        logger.info("EmaCrossoverStrategy stopped")

    # -- Update (called every tick) --

    async def update(self) -> None:
        """Scan all coins and update signals."""
        tasks = [self._scan_coin(sym) for sym in self._coins]
        await asyncio.gather(*tasks)

    async def _scan_coin(self, symbol: str) -> None:
        """Compute EMA9, EMA21 on 15m candles and detect crossover."""
        try:
            df = self._md.get_candles(symbol, self._interval)
            min_candles = self._ema_slow_period + self._max_bars + 5
            if df is None or len(df) < min_candles:
                return

            close = df["close"]
            volume = df["volume"]
            price = float(close.iloc[-1])

            # EMA computation
            ema_fast = close.ewm(span=self._ema_fast_period, adjust=False).mean()
            ema_slow = close.ewm(span=self._ema_slow_period, adjust=False).mean()

            ema_fast_now = float(ema_fast.iloc[-1])
            ema_slow_now = float(ema_slow.iloc[-1])

            # Cross detection within max_bars window
            cross_type = self._detect_cross(ema_fast, ema_slow)

            # Cross strength: how far apart the EMAs are (normalized)
            cross_strength: float | None = None
            if cross_type is not None and price > 0:
                raw_strength = abs(ema_fast_now - ema_slow_now) / price * 100
                cross_strength = min(1.0, raw_strength / CROSS_STRENGTH_CAP)

            # RSI on 15m
            rsi_series = ta_lib.momentum.RSIIndicator(close, window=RSI_PERIOD).rsi()
            rsi = float(rsi_series.iloc[-1]) if not rsi_series.empty and pd.notna(rsi_series.iloc[-1]) else None

            # Volume ratio
            vol_sma = float(volume.rolling(20).mean().iloc[-1]) if len(volume) >= 20 else None
            vol_ratio = float(volume.iloc[-1]) / vol_sma if vol_sma and vol_sma > 0 else None

            # 1h trend from TrendFilter
            trend_state = self._trend.get_state(symbol)
            trend_1h = trend_state.get("trend", "NEUTRAL")

            self._signals[symbol] = EmaCrossSignal(
                symbol=symbol,
                cross_type=cross_type,
                cross_strength=round(cross_strength, 4) if cross_strength is not None else None,
                trend_1h=trend_1h,
                volume_ratio=round(vol_ratio, 2) if vol_ratio else None,
                rsi=round(rsi, 2) if rsi is not None else None,
                price=price,
                ema_fast=round(ema_fast_now, 6),
                ema_slow=round(ema_slow_now, 6),
                updated_at=time.time(),
            )
        except Exception:
            logger.exception("Error scanning %s for EMA crossover", symbol)

    def _detect_cross(self, ema_fast: pd.Series, ema_slow: pd.Series) -> str | None:
        """Detect golden or death cross within the last max_bars bars.

        Golden cross: EMA_fast was below EMA_slow N bars ago but is now above.
        Death cross: EMA_fast was above EMA_slow N bars ago but is now below.
        """
        fast_now = float(ema_fast.iloc[-1])
        slow_now = float(ema_slow.iloc[-1])
        fast_ago = float(ema_fast.iloc[-self._max_bars])
        slow_ago = float(ema_slow.iloc[-self._max_bars])

        # Golden cross: was below, now above
        if fast_ago < slow_ago and fast_now > slow_now:
            return "GOLDEN"

        # Death cross: was above, now below
        if fast_ago > slow_ago and fast_now < slow_now:
            return "DEATH"

        return None

    # -- Decision generation --

    async def generate_decisions(self) -> list[Decision]:
        """Generate BUY/SHORT/CLOSE decisions based on EMA crossovers."""
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

            if pos.get("strategy") != "ema_crossover":
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
            "[EMA SCAN] coins=%d, scanned=%d, signals_generated=%d",
            len(self._coins), scanned, entries,
        )
        return decisions

    def _check_long_entry(self, symbol: str, sig: EmaCrossSignal) -> Decision | None:
        """Score-based LONG entry on golden cross."""
        if sig.cross_type != "GOLDEN" or sig.price is None or sig.cross_strength is None:
            return None

        # Hard blocks
        blocked, can_buy, abs_funding = check_hard_blocks(
            symbol,
            funding_rates=self._funding_rates,
            cooldown=self._cooldown,
            risk_config=self._rc,
        )
        if blocked:
            logger.debug("[EMA LONG] %s -> HARD BLOCK", symbol)
            return None

        # Trend hard block: BEARISH blocks LONG
        if sig.trend_1h == "BEARISH":
            logger.debug("[EMA LONG] %s -> HARD BLOCK: trend is BEARISH", symbol)
            return None

        # Scoring
        score = 0.0
        components: list[str] = []

        # Cross strength (graduated)
        score += W_CROSS * sig.cross_strength
        components.append(f"cross={sig.cross_strength:.3f}")

        # 1h trend alignment
        if sig.trend_1h == "BULLISH":
            score += W_TREND
            components.append("trend_1h=BULL")

        # Volume
        if sig.volume_ratio is not None and sig.volume_ratio >= MIN_VOLUME_RATIO:
            score += W_VOLUME
            components.append(f"vol={sig.volume_ratio:.1f}")

        # RSI confirmation: for BUY, RSI < 60 confirms
        if sig.rsi is not None and sig.rsi < 60:
            score += W_RSI
            components.append(f"RSI={sig.rsi:.1f}<60")

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
                "[EMA LONG] %s: score=%.3f < %.2f — cross=%s, RSI=%s, vol=%s",
                symbol, score, SCORE_THRESHOLD,
                f"{sig.cross_strength:.3f}" if sig.cross_strength else "N/A",
                f"{sig.rsi:.1f}" if sig.rsi else "N/A",
                f"{sig.volume_ratio:.1f}" if sig.volume_ratio else "N/A",
            )
            return None

        vol_str = f"{sig.volume_ratio:.1f}" if sig.volume_ratio is not None else "N/A"
        rsi_str = f"{sig.rsi:.1f}" if sig.rsi is not None else "N/A"

        logger.debug("[EMA LONG] %s: score=%.3f — %s", symbol, score, ", ".join(components))
        return Decision(
            action="BUY",
            symbol=symbol,
            confidence=score,
            reasoning=(
                f"[EmaCross LONG] {symbol}: score={score:.3f} — "
                f"golden_cross(str={sig.cross_strength:.3f}), "
                f"RSI={rsi_str}, vol={vol_str}, trend={sig.trend_1h}"
            ),
            strategy_type="ema_crossover",
            order_type="MARKET",
        )

    def _check_short_entry(self, symbol: str, sig: EmaCrossSignal) -> Decision | None:
        """Score-based SHORT entry on death cross."""
        if sig.cross_type != "DEATH" or sig.price is None or sig.cross_strength is None:
            return None

        # Hard blocks
        blocked, can_buy, abs_funding = check_hard_blocks(
            symbol,
            funding_rates=self._funding_rates,
            cooldown=self._cooldown,
            risk_config=self._rc,
        )
        if blocked:
            logger.debug("[EMA SHORT] %s -> HARD BLOCK", symbol)
            return None

        # Trend hard block: BULLISH blocks SHORT
        if sig.trend_1h == "BULLISH":
            logger.debug("[EMA SHORT] %s -> HARD BLOCK: trend is BULLISH", symbol)
            return None

        # Scoring
        score = 0.0
        components: list[str] = []

        # Cross strength (graduated)
        score += W_CROSS * sig.cross_strength
        components.append(f"cross={sig.cross_strength:.3f}")

        # 1h trend alignment
        if sig.trend_1h == "BEARISH":
            score += W_TREND
            components.append("trend_1h=BEAR")

        # Volume
        if sig.volume_ratio is not None and sig.volume_ratio >= MIN_VOLUME_RATIO:
            score += W_VOLUME
            components.append(f"vol={sig.volume_ratio:.1f}")

        # RSI confirmation: for SHORT, RSI > 40 confirms
        if sig.rsi is not None and sig.rsi > 40:
            score += W_RSI
            components.append(f"RSI={sig.rsi:.1f}>40")

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
                "[EMA SHORT] %s: score=%.3f < %.2f — cross=%s, RSI=%s, vol=%s",
                symbol, score, SCORE_THRESHOLD,
                f"{sig.cross_strength:.3f}" if sig.cross_strength else "N/A",
                f"{sig.rsi:.1f}" if sig.rsi else "N/A",
                f"{sig.volume_ratio:.1f}" if sig.volume_ratio else "N/A",
            )
            return None

        vol_str = f"{sig.volume_ratio:.1f}" if sig.volume_ratio is not None else "N/A"
        rsi_str = f"{sig.rsi:.1f}" if sig.rsi is not None else "N/A"

        logger.debug("[EMA SHORT] %s: score=%.3f — %s", symbol, score, ", ".join(components))
        return Decision(
            action="SHORT",
            symbol=symbol,
            confidence=score,
            reasoning=(
                f"[EmaCross SHORT] {symbol}: score={score:.3f} — "
                f"death_cross(str={sig.cross_strength:.3f}), "
                f"RSI={rsi_str}, vol={vol_str}, trend={sig.trend_1h}"
            ),
            strategy_type="ema_crossover",
            order_type="MARKET",
        )

    def _check_long_exit(self, symbol: str, sig: EmaCrossSignal) -> Decision | None:
        """Exit LONG if EMA9 crosses back below EMA21 (death cross)."""
        if sig.ema_fast is None or sig.ema_slow is None:
            return None

        if sig.ema_fast >= sig.ema_slow:
            return None  # still golden — hold

        return Decision(
            action="CLOSE",
            symbol=symbol,
            confidence=0.8,
            reasoning=(
                f"[EmaCross LONG EXIT] {symbol}: death_cross "
                f"EMA{self._ema_fast_period}={sig.ema_fast:.6f} < "
                f"EMA{self._ema_slow_period}={sig.ema_slow:.6f}"
            ),
            strategy_type="ema_crossover",
            order_type="MARKET",
        )

    def _check_short_exit(self, symbol: str, sig: EmaCrossSignal) -> Decision | None:
        """Exit SHORT if EMA9 crosses back above EMA21 (golden cross)."""
        if sig.ema_fast is None or sig.ema_slow is None:
            return None

        if sig.ema_fast <= sig.ema_slow:
            return None  # still death — hold

        return Decision(
            action="CLOSE",
            symbol=symbol,
            confidence=0.8,
            reasoning=(
                f"[EmaCross SHORT EXIT] {symbol}: golden_cross "
                f"EMA{self._ema_fast_period}={sig.ema_fast:.6f} > "
                f"EMA{self._ema_slow_period}={sig.ema_slow:.6f}"
            ),
            strategy_type="ema_crossover",
            order_type="MARKET",
        )

    async def _log_signal(self, decision: Decision, sig: EmaCrossSignal) -> None:
        """Log a SIGNAL event to the database."""
        if not self._db or self._cycle_count <= 0:
            return
        try:
            await self._db.insert_event(
                cycle=self._cycle_count,
                symbol=decision.symbol or "",
                event_type="SIGNAL",
                source="ema_crossover",
                action=decision.action,
                confidence=decision.confidence,
                reasoning=decision.reasoning,
                details={
                    "score": decision.confidence,
                    "cross_type": sig.cross_type,
                    "cross_strength": sig.cross_strength,
                    "rsi": sig.rsi,
                    "trend_1h": sig.trend_1h,
                    "volume_ratio": sig.volume_ratio,
                    "ema_fast": sig.ema_fast,
                    "ema_slow": sig.ema_slow,
                },
            )
        except Exception:
            logger.debug("Failed to log SIGNAL event", exc_info=True)

    # -- State (for snapshot / dashboard) --

    def get_state(self) -> dict[str, Any]:
        """Return signal map for the AI market snapshot."""
        signals: dict[str, Any] = {}
        golden_crosses: list[str] = []
        death_crosses: list[str] = []

        for sym, sig in self._signals.items():
            signals[sym] = {
                "cross_type": sig.cross_type,
                "cross_strength": sig.cross_strength,
                "trend_1h": sig.trend_1h,
                "volume_ratio": sig.volume_ratio,
                "rsi": sig.rsi,
                "price": sig.price,
                "ema_fast": sig.ema_fast,
                "ema_slow": sig.ema_slow,
            }
            if sig.cross_type == "GOLDEN":
                golden_crosses.append(sym)
            elif sig.cross_type == "DEATH":
                death_crosses.append(sym)

        return {
            "strategy": "ema_crossover",
            "interval": self._interval,
            "ema_fast_period": self._ema_fast_period,
            "ema_slow_period": self._ema_slow_period,
            "max_bars": self._max_bars,
            "coins_scanned": len(self._signals),
            "golden_crosses": golden_crosses,
            "death_crosses": death_crosses,
            "signals": signals,
            "cooldown": self._cooldown.get_state(),
            "trend_filter": self._trend.get_all_states(),
        }
