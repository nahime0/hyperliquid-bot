"""Multi-Timeframe Confluence — scoring-based decision generator.

Generates BUY/SHORT/CLOSE decisions when all three timeframes (5m, 15m, 1h)
agree on direction.  Unlike single-timeframe strategies, MTF Confluence
requires alignment across fast, medium, and slow timeframes before entry,
which filters out noise and catches higher-probability setups.

LONG Entry scoring:
  - 5m RSI < mtf_rsi_oversold              -> +0.25
  - 15m RSI slope > 0 (trending up)        -> +0.25
  - 1h trend BULLISH (TrendFilter)         -> +0.20
  - Volume ratio >= 1.0 (5m candles)       -> +0.10
  - |funding| < max_funding_rate           -> +0.10
  - No per-symbol cooldown                 -> +0.10
  Score >= 0.60 -> generate signal (confidence = score)

SHORT Entry scoring (mirrored):
  - 5m RSI > mtf_rsi_overbought            -> +0.25
  - 15m RSI slope < 0 (trending down)      -> +0.25
  - 1h trend BEARISH (TrendFilter)         -> +0.20
  - Volume ratio >= 1.0 (5m candles)       -> +0.10
  - |funding| < max_funding_rate           -> +0.10
  - No per-symbol cooldown                 -> +0.10

Hard blocks (always reject, bypass scoring):
  - |funding| >= 0.001 (extreme funding)
  - Global cooldown active (3+ consecutive losses)
  - Fresh per-symbol cooldown (loss < 10 min ago)

Exit (2-of-3 timeframe divergence):
  - LONG exit: 2+ of {RSI(5m) > overbought, 15m RSI slope < 0, 1h trend != BULLISH}
  - SHORT exit: 2+ of {RSI(5m) < oversold, 15m RSI slope > 0, 1h trend != BEARISH}
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
W_TF_5M = 0.25        # 5m RSI oversold/overbought
W_TF_15M = 0.25       # 15m RSI slope alignment
W_TF_1H = 0.20        # 1h trend alignment
W_VOLUME = 0.10        # volume ratio >= threshold
W_FUNDING = 0.10       # funding rate acceptable
W_COOLDOWN = 0.10      # no per-symbol cooldown
SCORE_THRESHOLD = 0.60  # minimum score to generate signal

# -- Indicator params --
RSI_PERIOD = 14
MIN_CANDLES = 20       # minimum candles on each timeframe
MIN_VOLUME_RATIO = 1.0  # volume ratio threshold
VOLUME_SMA_PERIOD = 20  # SMA period for volume ratio


@dataclass
class MtfSignal:
    """Signal for one coin at a point in time across multiple timeframes."""
    symbol: str
    rsi_5m: float | None
    rsi_15m: float | None
    rsi_15m_slope: float | None
    trend_1h: str
    volume_ratio: float | None
    price: float | None
    updated_at: float


class MtfConfluenceStrategy(Strategy):
    """Multi-Timeframe Confluence strategy: enters when 5m, 15m, and 1h agree."""

    strategy_type = "mtf_confluence"

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
        self._signals: dict[str, MtfSignal] = {}
        self._funding_rates: dict[str, float] = {}
        self._max_funding_rate: float = 0.0005
        self._db = db
        self._cycle_count: int = 0

    # -- Setters (called from main loop / MultiStrategy) --

    def set_coins(self, coins: list[str]) -> None:
        self._coins = coins
        logger.info("MtfConfluence coins updated: %d", len(coins))

    def set_funding_rates(self, rates: dict[str, float]) -> None:
        self._funding_rates = rates

    def set_max_funding_rate(self, rate: float) -> None:
        self._max_funding_rate = rate

    def set_cycle(self, cycle: int) -> None:
        self._cycle_count = cycle

    # -- Lifecycle --

    async def start(self) -> None:
        logger.info(
            "MtfConfluenceStrategy started — scanning %d coins on 5m+15m+1h",
            len(self._coins),
        )

    async def stop(self) -> None:
        self._signals.clear()
        logger.info("MtfConfluenceStrategy stopped")

    # -- Update (called every tick) --

    async def update(self) -> None:
        """Scan all coins and update multi-timeframe signals."""
        tasks = [self._scan_coin(sym) for sym in self._coins]
        await asyncio.gather(*tasks)

    async def _scan_coin(self, symbol: str) -> None:
        """Compute multi-timeframe indicators for one coin."""
        try:
            # ── 5m candles ──
            df_5m = self._md.get_candles(symbol, "5m")
            if df_5m is None or len(df_5m) < MIN_CANDLES:
                return

            close_5m = df_5m["close"]
            volume_5m = df_5m["volume"]
            price = float(close_5m.iloc[-1])

            # ── 15m candles ──
            df_15m = self._md.get_candles(symbol, "15m")
            if df_15m is None or len(df_15m) < MIN_CANDLES:
                return

            close_15m = df_15m["close"]

            # ── 1h trend from TrendFilter ──
            trend_state = self._trend.get_state(symbol)
            trend_1h = trend_state.get("trend", "NEUTRAL")

            # ── RSI on 5m ──
            rsi_5m_series = ta_lib.momentum.RSIIndicator(close_5m, window=RSI_PERIOD).rsi()
            rsi_5m: float | None = None
            if not rsi_5m_series.empty and pd.notna(rsi_5m_series.iloc[-1]):
                rsi_5m = float(rsi_5m_series.iloc[-1])

            # ── RSI on 15m + slope ──
            rsi_15m_series = ta_lib.momentum.RSIIndicator(close_15m, window=RSI_PERIOD).rsi()
            rsi_15m: float | None = None
            rsi_15m_slope: float | None = None

            if not rsi_15m_series.empty and pd.notna(rsi_15m_series.iloc[-1]):
                rsi_15m = float(rsi_15m_series.iloc[-1])
                slope_bars = self._sc.mtf_rsi_slope_bars
                if len(rsi_15m_series) >= slope_bars + 1 and pd.notna(rsi_15m_series.iloc[-slope_bars]):
                    rsi_15m_slope = (
                        float(rsi_15m_series.iloc[-1]) - float(rsi_15m_series.iloc[-slope_bars])
                    ) / (slope_bars - 1)

            # ── Volume ratio on 5m ──
            vol_ratio: float | None = None
            if len(volume_5m) >= VOLUME_SMA_PERIOD:
                vol_sma = float(volume_5m.rolling(VOLUME_SMA_PERIOD).mean().iloc[-1])
                if vol_sma > 0:
                    vol_ratio = float(volume_5m.iloc[-1]) / vol_sma

            self._signals[symbol] = MtfSignal(
                symbol=symbol,
                rsi_5m=round(rsi_5m, 2) if rsi_5m is not None else None,
                rsi_15m=round(rsi_15m, 2) if rsi_15m is not None else None,
                rsi_15m_slope=round(rsi_15m_slope, 4) if rsi_15m_slope is not None else None,
                trend_1h=trend_1h,
                volume_ratio=round(vol_ratio, 2) if vol_ratio is not None else None,
                price=price,
                updated_at=time.time(),
            )
        except Exception:
            logger.exception("Error scanning %s for MTF confluence", symbol)

    # -- Decision generation --

    async def generate_decisions(self) -> list[Decision]:
        """Generate BUY/SHORT/CLOSE decisions based on multi-timeframe alignment."""
        decisions: list[Decision] = []
        open_symbols = await self._positions.get_open_symbols()

        # ── EXIT checks (only on positions opened by this strategy) ──
        for symbol in list(open_symbols):
            sig = self._signals.get(symbol)
            if not sig:
                continue

            pos = await self._positions.get_position_for_symbol(symbol)
            if not pos:
                continue

            if pos.get("strategy") != self.strategy_type:
                continue

            direction = pos.get("direction", "LONG")
            if direction == "LONG":
                exit_d = self._check_long_exit(symbol, sig)
            else:
                exit_d = self._check_short_exit(symbol, sig)

            if exit_d:
                decisions.append(exit_d)

        # ── ENTRY checks ──
        scanned = 0
        for symbol in self._coins:
            if symbol in open_symbols:
                continue

            sig = self._signals.get(symbol)
            if not sig:
                continue

            scanned += 1

            # LONG first, then SHORT (only one direction per coin per cycle)
            entry = self._check_long_entry(symbol, sig)
            if entry:
                decisions.append(entry)
                await self._log_signal(entry, sig)
                continue

            entry = self._check_short_entry(symbol, sig)
            if entry:
                decisions.append(entry)
                await self._log_signal(entry, sig)

        entries = sum(1 for d in decisions if d.action in ("BUY", "SHORT"))
        logger.info(
            "[MTF SCAN] coins=%d, scanned=%d, signals_generated=%d",
            len(self._coins), scanned, entries,
        )
        return decisions

    # -- Entry checks --

    def _check_long_entry(self, symbol: str, sig: MtfSignal) -> Decision | None:
        """Score-based LONG entry requiring multi-timeframe confluence."""
        if sig.rsi_5m is None or sig.rsi_15m_slope is None or sig.price is None:
            return None

        # ── Hard blocks ──
        blocked, can_buy, abs_funding = check_hard_blocks(
            symbol,
            funding_rates=self._funding_rates,
            cooldown=self._cooldown,
            risk_config=self._rc,
        )
        if blocked:
            logger.debug("[MTF LONG] %s -> HARD BLOCK", symbol)
            return None

        rsi_oversold = self._sc.mtf_rsi_oversold

        # ── All timeframes must agree for confluence ──
        # 5m: RSI must be oversold
        if sig.rsi_5m >= rsi_oversold:
            return None
        # 15m: RSI must be trending up (slope > 0)
        if sig.rsi_15m_slope <= 0:
            return None
        # 1h: trend must be BULLISH
        if sig.trend_1h != "BULLISH":
            return None

        # ── Scoring (graduated intensity) ──
        score = 0.0
        components: list[str] = []

        # 5m RSI: how oversold (distance below threshold, normalized)
        intensity_5m = min(1.0, (rsi_oversold - sig.rsi_5m) / rsi_oversold)
        score += W_TF_5M * intensity_5m
        components.append(f"RSI_5m={sig.rsi_5m:.1f}(i={intensity_5m:.2f})")

        # 15m RSI slope: stronger upward slope scores higher
        intensity_15m = min(1.0, abs(sig.rsi_15m_slope) / 2.0)
        score += W_TF_15M * intensity_15m
        components.append(f"slope_15m={sig.rsi_15m_slope:+.3f}(i={intensity_15m:.2f})")

        # 1h trend: full score (already gated by the confluence check above)
        score += W_TF_1H
        components.append("trend_1h=BULL")

        # Volume
        if sig.volume_ratio is not None and sig.volume_ratio >= MIN_VOLUME_RATIO:
            score += W_VOLUME
            components.append(f"vol={sig.volume_ratio:.1f}")

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
                "[MTF LONG] %s: score=%.3f < %.2f — RSI_5m=%.1f, slope_15m=%.3f, trend=%s",
                symbol, score, SCORE_THRESHOLD, sig.rsi_5m,
                sig.rsi_15m_slope, sig.trend_1h,
            )
            return None

        vol_str = f"{sig.volume_ratio:.1f}" if sig.volume_ratio is not None else "N/A"

        logger.debug("[MTF LONG] %s: score=%.3f — %s", symbol, score, ", ".join(components))
        expected_move = abs(50 - sig.rsi_5m) / 50 * 2.0
        return Decision(
            action="BUY",
            symbol=symbol,
            confidence=score,
            reasoning=(
                f"[MtfConfl LONG] {symbol}: score={score:.3f} — "
                f"RSI_5m={sig.rsi_5m:.1f}, slope_15m={sig.rsi_15m_slope:+.3f}, "
                f"trend_1h={sig.trend_1h}, vol={vol_str}"
            ),
            strategy_type=self.strategy_type,
            order_type="MARKET",
            expected_move_pct=round(expected_move, 2),
        )

    def _check_short_entry(self, symbol: str, sig: MtfSignal) -> Decision | None:
        """Score-based SHORT entry requiring multi-timeframe confluence."""
        if sig.rsi_5m is None or sig.rsi_15m_slope is None or sig.price is None:
            return None

        # ── Hard blocks ──
        blocked, can_buy, abs_funding = check_hard_blocks(
            symbol,
            funding_rates=self._funding_rates,
            cooldown=self._cooldown,
            risk_config=self._rc,
        )
        if blocked:
            logger.debug("[MTF SHORT] %s -> HARD BLOCK", symbol)
            return None

        rsi_overbought = self._sc.mtf_rsi_overbought

        # ── All timeframes must agree for confluence ──
        # 5m: RSI must be overbought
        if sig.rsi_5m <= rsi_overbought:
            return None
        # 15m: RSI must be trending down (slope < 0)
        if sig.rsi_15m_slope >= 0:
            return None
        # 1h: trend must be BEARISH
        if sig.trend_1h != "BEARISH":
            return None

        # ── Scoring (graduated intensity) ──
        score = 0.0
        components: list[str] = []

        # 5m RSI: how overbought (distance above threshold, normalized)
        intensity_5m = min(1.0, (sig.rsi_5m - rsi_overbought) / (100.0 - rsi_overbought))
        score += W_TF_5M * intensity_5m
        components.append(f"RSI_5m={sig.rsi_5m:.1f}(i={intensity_5m:.2f})")

        # 15m RSI slope: stronger downward slope scores higher
        intensity_15m = min(1.0, abs(sig.rsi_15m_slope) / 2.0)
        score += W_TF_15M * intensity_15m
        components.append(f"slope_15m={sig.rsi_15m_slope:+.3f}(i={intensity_15m:.2f})")

        # 1h trend: full score (already gated by the confluence check above)
        score += W_TF_1H
        components.append("trend_1h=BEAR")

        # Volume
        if sig.volume_ratio is not None and sig.volume_ratio >= MIN_VOLUME_RATIO:
            score += W_VOLUME
            components.append(f"vol={sig.volume_ratio:.1f}")

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
                "[MTF SHORT] %s: score=%.3f < %.2f — RSI_5m=%.1f, slope_15m=%.3f, trend=%s",
                symbol, score, SCORE_THRESHOLD, sig.rsi_5m,
                sig.rsi_15m_slope, sig.trend_1h,
            )
            return None

        vol_str = f"{sig.volume_ratio:.1f}" if sig.volume_ratio is not None else "N/A"

        logger.debug("[MTF SHORT] %s: score=%.3f — %s", symbol, score, ", ".join(components))
        expected_move = abs(50 - sig.rsi_5m) / 50 * 2.0
        return Decision(
            action="SHORT",
            symbol=symbol,
            confidence=score,
            reasoning=(
                f"[MtfConfl SHORT] {symbol}: score={score:.3f} — "
                f"RSI_5m={sig.rsi_5m:.1f}, slope_15m={sig.rsi_15m_slope:+.3f}, "
                f"trend_1h={sig.trend_1h}, vol={vol_str}"
            ),
            strategy_type=self.strategy_type,
            order_type="MARKET",
            expected_move_pct=round(expected_move, 2),
        )

    # -- Exit checks --

    def _check_long_exit(self, symbol: str, sig: MtfSignal) -> Decision | None:
        """Exit LONG if 2+ of 3 timeframes diverge from the entry thesis.

        Entry requires ALL 3 aligned (AND). Using ANY for exit (OR) causes
        ~49% exit probability per cycle. Require 2-of-3 to diverge for symmetry.
        """
        reasons: list[str] = []

        rsi_overbought = self._sc.mtf_rsi_overbought

        # 5m RSI crossed overbought
        if sig.rsi_5m is not None and sig.rsi_5m > rsi_overbought:
            reasons.append(f"RSI_5m={sig.rsi_5m:.1f}>{rsi_overbought:.0f}")

        # 15m RSI slope turned negative
        if sig.rsi_15m_slope is not None and sig.rsi_15m_slope < 0:
            reasons.append(f"slope_15m={sig.rsi_15m_slope:+.3f}<0")

        # 1h trend no longer BULLISH
        if sig.trend_1h != "BULLISH":
            reasons.append(f"trend_1h={sig.trend_1h}")

        # If indicator data is unavailable, lower exit threshold
        available_checks = sum([
            sig.rsi_5m is not None,
            sig.rsi_15m_slope is not None,
            True,  # trend_1h is always available
        ])
        min_divergences = 2 if available_checks >= 3 else 1

        # Require sufficient divergences to exit
        if len(reasons) < min_divergences:
            return None

        return Decision(
            action="CLOSE",
            symbol=symbol,
            confidence=0.8,
            reasoning=f"[MtfConfl LONG EXIT] {symbol}: {len(reasons)}/3 diverged — {', '.join(reasons)}",
            strategy_type=self.strategy_type,
            order_type="MARKET",
        )

    def _check_short_exit(self, symbol: str, sig: MtfSignal) -> Decision | None:
        """Exit SHORT if 2+ of 3 timeframes diverge from the entry thesis."""
        reasons: list[str] = []

        rsi_oversold = self._sc.mtf_rsi_oversold

        # 5m RSI crossed oversold
        if sig.rsi_5m is not None and sig.rsi_5m < rsi_oversold:
            reasons.append(f"RSI_5m={sig.rsi_5m:.1f}<{rsi_oversold:.0f}")

        # 15m RSI slope turned positive
        if sig.rsi_15m_slope is not None and sig.rsi_15m_slope > 0:
            reasons.append(f"slope_15m={sig.rsi_15m_slope:+.3f}>0")

        # 1h trend no longer BEARISH
        if sig.trend_1h != "BEARISH":
            reasons.append(f"trend_1h={sig.trend_1h}")

        # If indicator data is unavailable, lower exit threshold
        available_checks = sum([
            sig.rsi_5m is not None,
            sig.rsi_15m_slope is not None,
            True,  # trend_1h is always available
        ])
        min_divergences = 2 if available_checks >= 3 else 1

        # Require sufficient divergences to exit
        if len(reasons) < min_divergences:
            return None

        return Decision(
            action="CLOSE",
            symbol=symbol,
            confidence=0.8,
            reasoning=f"[MtfConfl SHORT EXIT] {symbol}: {len(reasons)}/3 diverged — {', '.join(reasons)}",
            strategy_type=self.strategy_type,
            order_type="MARKET",
        )

    # -- Event logging --

    async def _log_signal(self, decision: Decision, sig: MtfSignal) -> None:
        if not self._db or self._cycle_count <= 0:
            return
        try:
            await self._db.insert_event(
                cycle=self._cycle_count,
                symbol=decision.symbol or "",
                event_type="SIGNAL",
                source=self.strategy_type,
                action=decision.action,
                confidence=decision.confidence,
                reasoning=decision.reasoning,
                details={
                    "score": decision.confidence,
                    "rsi_5m": sig.rsi_5m,
                    "rsi_15m": sig.rsi_15m,
                    "rsi_15m_slope": sig.rsi_15m_slope,
                    "trend_1h": sig.trend_1h,
                    "volume_ratio": sig.volume_ratio,
                    "expected_move_pct": round(abs(50 - sig.rsi_5m) / 50 * 2.0, 2) if sig.rsi_5m is not None else None,
                },
            )
        except Exception:
            logger.debug("Failed to log SIGNAL event", exc_info=True)

    # -- State (for snapshot / dashboard) --

    def get_state(self) -> dict[str, Any]:
        """Return signal map for the AI market snapshot."""
        signals: dict[str, Any] = {}
        long_candidates: list[str] = []
        short_candidates: list[str] = []

        rsi_oversold = self._sc.mtf_rsi_oversold
        rsi_overbought = self._sc.mtf_rsi_overbought

        for sym, sig in self._signals.items():
            signals[sym] = {
                "rsi_5m": sig.rsi_5m,
                "rsi_15m": sig.rsi_15m,
                "rsi_15m_slope": sig.rsi_15m_slope,
                "trend_1h": sig.trend_1h,
                "volume_ratio": sig.volume_ratio,
                "price": sig.price,
            }
            # Classify for summary
            if (
                sig.rsi_5m is not None
                and sig.rsi_5m < rsi_oversold
                and sig.rsi_15m_slope is not None
                and sig.rsi_15m_slope > 0
                and sig.trend_1h == "BULLISH"
            ):
                long_candidates.append(sym)
            elif (
                sig.rsi_5m is not None
                and sig.rsi_5m > rsi_overbought
                and sig.rsi_15m_slope is not None
                and sig.rsi_15m_slope < 0
                and sig.trend_1h == "BEARISH"
            ):
                short_candidates.append(sym)

        return {
            "strategy": self.strategy_type,
            "timeframes": ["5m", "15m", "1h"],
            "coins_scanned": len(self._signals),
            "long_candidates": long_candidates,
            "short_candidates": short_candidates,
            "signals": signals,
            "cooldown": self._cooldown.get_state(),
            "trend_filter": self._trend.get_all_states(),
        }
