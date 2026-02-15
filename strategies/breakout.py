"""Support/Resistance Breakout — scoring-based decision generator.

Generates BUY/SHORT/CLOSE decisions when price breaks through key S/R levels.
Uses 1h candles for S/R level identification and 15m candles for breakout
detection and confirmation.

BUY Entry scoring (breakout above resistance):
  - breakout_strength (distance past level)   -> +0.25
  - volume_confirmation (ratio >= 1.5)        -> +0.25
  - trend_1h alignment (BULLISH)              -> +0.20
  - clean_break (all confirm bars above)      -> +0.10
  - |funding| < max_funding_rate              -> +0.10
  - No per-symbol cooldown                    -> +0.10
  Score >= 0.60 -> generate signal (confidence = score)

SHORT Entry scoring (breakout below support):
  - breakout_strength (distance past level)   -> +0.25
  - volume_confirmation (ratio >= 1.5)        -> +0.25
  - trend_1h alignment (BEARISH)              -> +0.20
  - clean_break (all confirm bars below)      -> +0.10
  - |funding| < max_funding_rate              -> +0.10
  - No per-symbol cooldown                    -> +0.10

Hard blocks (always reject, bypass scoring):
  - |funding| >= 0.001 (extreme funding)
  - Global cooldown active (3+ consecutive losses)
  - Fresh per-symbol cooldown (loss < 10 min ago)

Exit (failed breakout):
  - LONG exit: price drops back below resistance level
  - SHORT exit: price rises back above support level
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
from strategies.hard_blocks import check_hard_blocks
from strategies.trend_filter import TrendFilter
from utils.logger import get_logger

logger = get_logger(__name__)

# -- Scoring weights --
W_BREAKOUT_STRENGTH = 0.25   # how far past the S/R level
W_VOLUME = 0.25              # volume confirmation
W_TREND = 0.20               # 1h trend alignment
W_CLEAN_BREAK = 0.10         # all confirm bars above/below level
W_FUNDING = 0.10             # funding rate acceptable
W_COOLDOWN = 0.10            # no per-symbol cooldown
SCORE_THRESHOLD = 0.60       # minimum score to generate signal

# -- Thresholds --
MIN_1H_CANDLES = 24           # need at least 24 1h candles for S/R
MIN_15M_CANDLES = 10          # need at least 10 15m candles for breakout detection
BREAKOUT_STRENGTH_CAP = 2.0   # cap breakout strength normalization at 2%
VOLUME_FULL_THRESHOLD = 1.5   # volume ratio >= 1.5 -> full points
VOLUME_PARTIAL_THRESHOLD = 1.0 # volume ratio >= 1.0 -> partial points


@dataclass
class BreakoutSignal:
    """Signal for one coin at a point in time."""
    symbol: str
    breakout_dir: str | None    # "UP", "DOWN", None
    resistance: float | None
    support: float | None
    breakout_strength: float | None
    clean_break: bool
    volume_ratio: float | None
    trend_1h: str
    price: float | None
    updated_at: float


class BreakoutStrategy(Strategy):
    """Support/Resistance breakout strategy: captures level breaks."""

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
        self._signals: dict[str, BreakoutSignal] = {}
        self._funding_rates: dict[str, float] = {}
        self._max_funding_rate: float = 0.0005
        self._db = db
        self._cycle_count: int = 0
        # Store S/R levels at entry time per symbol (resistance, support)
        # so exits use frozen levels, not recalculated ones
        self._entry_levels: dict[str, tuple[float, float]] = {}
        # Track signaled breakout levels to avoid re-signaling same level
        self._signaled_levels: dict[str, tuple[float, str]] = {}  # symbol -> (level, direction)

    # -- Setters --

    def set_coins(self, coins: list[str]) -> None:
        self._coins = coins
        logger.info("Breakout coins updated: %d", len(coins))

    def set_funding_rates(self, rates: dict[str, float]) -> None:
        self._funding_rates = rates

    def set_max_funding_rate(self, rate: float) -> None:
        self._max_funding_rate = rate

    def set_cycle(self, cycle: int) -> None:
        self._cycle_count = cycle

    # -- Lifecycle --

    async def start(self) -> None:
        logger.info(
            "BreakoutStrategy started — scanning %d coins "
            "(lookback=%dh, min_breakout=%.1f%%, confirm_bars=%d)",
            len(self._coins),
            self._sc.bo_lookback_hours,
            self._sc.bo_min_breakout_pct,
            self._sc.bo_confirm_bars,
        )

    async def stop(self) -> None:
        self._signals.clear()
        self._entry_levels.clear()
        self._signaled_levels.clear()
        logger.info("BreakoutStrategy stopped")

    # -- Update (called every tick) --

    async def update(self) -> None:
        """Scan all coins and update breakout signals."""
        tasks = [self._scan_coin(sym) for sym in self._coins]
        await asyncio.gather(*tasks)

    async def _scan_coin(self, symbol: str) -> None:
        """Compute S/R levels and detect breakouts for one coin."""
        try:
            # 1h candles for S/R levels
            df_1h = self._md.get_candles(symbol, "1h")
            if df_1h is None or len(df_1h) < MIN_1H_CANDLES:
                return

            # 15m candles for breakout detection
            df_15m = self._md.get_candles(symbol, "15m")
            if df_15m is None or len(df_15m) < MIN_15M_CANDLES:
                return

            lookback = self._sc.bo_lookback_hours
            min_breakout_pct = self._sc.bo_min_breakout_pct
            confirm_bars = self._sc.bo_confirm_bars

            # S/R levels from last `lookback` 1h candles
            window_1h = df_1h.tail(lookback)
            resistance = float(window_1h["high"].max())
            support = float(window_1h["low"].min())

            # Current price from 15m
            price = float(df_15m["close"].iloc[-1])

            # 1h trend from TrendFilter
            trend_state = self._trend.get_state(symbol)
            trend_1h = trend_state.get("trend", "NEUTRAL")

            # Volume ratio on 15m
            vol_15m = df_15m["volume"]
            vol_sma = (
                float(vol_15m.rolling(20).mean().iloc[-1])
                if len(vol_15m) >= 20
                else None
            )
            vol_ratio = (
                float(vol_15m.iloc[-1]) / vol_sma
                if vol_sma and vol_sma > 0
                else None
            )

            # Breakout detection
            breakout_dir: str | None = None
            breakout_strength: float | None = None
            clean_break = False

            up_threshold = resistance * (1 + min_breakout_pct / 100)
            down_threshold = support * (1 - min_breakout_pct / 100)

            if price > up_threshold:
                breakout_dir = "UP"
                breakout_strength = abs(price - resistance) / resistance * 100
                # Clean break: check that last N 15m closes are all above resistance
                if confirm_bars > 0 and len(df_15m) >= confirm_bars:
                    recent_closes = df_15m["close"].iloc[-confirm_bars:]
                    bars_above = int((recent_closes > resistance).sum())
                    clean_break = bars_above == confirm_bars
                else:
                    clean_break = True

            elif price < down_threshold:
                breakout_dir = "DOWN"
                breakout_strength = abs(support - price) / support * 100 if support > 0 else 0.0
                # Clean break: check that last N 15m closes are all below support
                if confirm_bars > 0 and len(df_15m) >= confirm_bars:
                    recent_closes = df_15m["close"].iloc[-confirm_bars:]
                    bars_below = int((recent_closes < support).sum())
                    clean_break = bars_below == confirm_bars
                else:
                    clean_break = True

            # Staleness check for breakout levels
            if breakout_dir is None:
                self._signaled_levels.pop(symbol, None)
            else:
                # Skip if same level already signaled
                signaled = self._signaled_levels.get(symbol)
                if signaled and abs(signaled[0] - (resistance if breakout_dir == "UP" else support)) / signaled[0] < 0.001 and signaled[1] == breakout_dir:
                    return  # Same level already signaled
                # Record this level
                self._signaled_levels[symbol] = (resistance if breakout_dir == "UP" else support, breakout_dir)

            self._signals[symbol] = BreakoutSignal(
                symbol=symbol,
                breakout_dir=breakout_dir,
                resistance=round(resistance, 6),
                support=round(support, 6),
                breakout_strength=round(breakout_strength, 4) if breakout_strength is not None else None,
                clean_break=clean_break,
                volume_ratio=round(vol_ratio, 2) if vol_ratio is not None else None,
                trend_1h=trend_1h,
                price=price,
                updated_at=time.time(),
            )
        except Exception:
            logger.exception("Error scanning %s for breakout", symbol)

    # -- Decision generation --

    async def generate_decisions(self) -> list[Decision]:
        """Generate BUY/SHORT/CLOSE decisions based on breakout signals.

        Checks exits first (on open positions), then entries for both directions.
        """
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

            if pos.get("strategy") != "breakout":
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
                # Freeze S/R levels at entry time
                if sig.resistance is not None and sig.support is not None:
                    self._entry_levels[symbol] = (sig.resistance, sig.support)
                decisions.append(entry)
                await self._log_signal(entry, sig)
                continue  # one direction per coin per cycle

            entry = self._check_short_entry(symbol, sig)
            if entry:
                # Freeze S/R levels at entry time
                if sig.resistance is not None and sig.support is not None:
                    self._entry_levels[symbol] = (sig.resistance, sig.support)
                decisions.append(entry)
                await self._log_signal(entry, sig)

        entries = sum(1 for d in decisions if d.action in ("BUY", "SHORT"))
        logger.info(
            "[BO SCAN] coins=%d, scanned=%d, signals_generated=%d",
            len(self._coins), scanned, entries,
        )
        return decisions

    # -- Exit checks --

    def _check_long_exit(self, symbol: str, sig: BreakoutSignal) -> Decision | None:
        """Exit LONG if price drops back below the entry-time resistance (failed breakout).

        Uses frozen S/R levels from entry, not recalculated rolling values.
        """
        if sig.price is None:
            return None

        # Use entry-time resistance, fall back to current if not stored
        entry_levels = self._entry_levels.get(symbol)
        resistance = entry_levels[0] if entry_levels else sig.resistance
        if resistance is None:
            return None

        if sig.price < resistance:
            self._entry_levels.pop(symbol, None)  # clean up on exit
            return Decision(
                action="CLOSE",
                symbol=symbol,
                confidence=0.8,
                reasoning=(
                    f"[Breakout LONG EXIT] {symbol}: price={sig.price:.4f} "
                    f"dropped below entry_resistance={resistance:.4f} (failed breakout)"
                ),
                strategy_type="breakout",
                order_type="MARKET",
            )
        return None

    def _check_short_exit(self, symbol: str, sig: BreakoutSignal) -> Decision | None:
        """Exit SHORT if price rises back above the entry-time support (failed breakout).

        Uses frozen S/R levels from entry, not recalculated rolling values.
        """
        if sig.price is None:
            return None

        # Use entry-time support, fall back to current if not stored
        entry_levels = self._entry_levels.get(symbol)
        support = entry_levels[1] if entry_levels else sig.support
        if support is None:
            return None

        if sig.price > support:
            self._entry_levels.pop(symbol, None)  # clean up on exit
            return Decision(
                action="CLOSE",
                symbol=symbol,
                confidence=0.8,
                reasoning=(
                    f"[Breakout SHORT EXIT] {symbol}: price={sig.price:.4f} "
                    f"rose above entry_support={support:.4f} (failed breakout)"
                ),
                strategy_type="breakout",
                order_type="MARKET",
            )
        return None

    # -- Entry checks --

    def _check_long_entry(self, symbol: str, sig: BreakoutSignal) -> Decision | None:
        """Score-based LONG entry: breakout above resistance."""
        if sig.price is None or sig.resistance is None:
            return None

        if sig.breakout_dir != "UP":
            return None

        # Hard blocks
        blocked, can_buy, abs_funding = check_hard_blocks(
            symbol,
            funding_rates=self._funding_rates,
            cooldown=self._cooldown,
            risk_config=self._rc,
        )
        if blocked:
            logger.debug("[BO LONG] %s -> HARD BLOCK", symbol)
            return None

        # Trend hard block: BEARISH blocks LONG
        if sig.trend_1h == "BEARISH":
            logger.debug("[BO LONG] %s -> HARD BLOCK: trend is BEARISH", symbol)
            return None

        # Scoring
        score = 0.0
        components: list[str] = []

        # Breakout strength: how far past resistance, normalized 0-1 (cap at 2%)
        if sig.breakout_strength is not None and sig.breakout_strength > 0:
            intensity = min(1.0, sig.breakout_strength / BREAKOUT_STRENGTH_CAP)
            score += W_BREAKOUT_STRENGTH * intensity
            components.append(f"strength={sig.breakout_strength:.2f}%(i={intensity:.2f})")

        # Volume confirmation
        if sig.volume_ratio is not None:
            if sig.volume_ratio >= VOLUME_FULL_THRESHOLD:
                score += W_VOLUME
                components.append(f"vol={sig.volume_ratio:.1f}(full)")
            elif sig.volume_ratio >= VOLUME_PARTIAL_THRESHOLD:
                partial = (sig.volume_ratio - VOLUME_PARTIAL_THRESHOLD) / (
                    VOLUME_FULL_THRESHOLD - VOLUME_PARTIAL_THRESHOLD
                )
                score += W_VOLUME * partial
                components.append(f"vol={sig.volume_ratio:.1f}(partial)")

        # Trend alignment
        if sig.trend_1h == "BULLISH":
            score += W_TREND
            components.append("trend=BULL")
        elif sig.trend_1h == "NEUTRAL":
            score += W_TREND * 0.5
            components.append("trend=NEUTRAL(0.5)")

        # Clean break
        if sig.clean_break:
            score += W_CLEAN_BREAK
            components.append("clean_break")
        else:
            # Partial credit based on confirm bars
            confirm_bars = self._sc.bo_confirm_bars
            if confirm_bars > 0 and sig.price is not None and sig.resistance is not None:
                df_15m = self._md.get_candles(symbol, "15m")
                if df_15m is not None and len(df_15m) >= confirm_bars:
                    recent_closes = df_15m["close"].iloc[-confirm_bars:]
                    bars_above = int((recent_closes > sig.resistance).sum())
                    ratio = bars_above / confirm_bars
                    score += W_CLEAN_BREAK * ratio
                    components.append(f"partial_break({bars_above}/{confirm_bars})")

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
                "[BO LONG] %s: score=%.3f < %.2f — strength=%s, vol=%s, trend=%s",
                symbol, score, SCORE_THRESHOLD,
                f"{sig.breakout_strength:.2f}%" if sig.breakout_strength else "N/A",
                f"{sig.volume_ratio:.1f}" if sig.volume_ratio else "N/A",
                sig.trend_1h,
            )
            return None

        vol_str = f"{sig.volume_ratio:.1f}" if sig.volume_ratio is not None else "N/A"
        strength_str = f"{sig.breakout_strength:.2f}%" if sig.breakout_strength is not None else "N/A"

        logger.debug("[BO LONG] %s: score=%.3f — %s", symbol, score, ", ".join(components))
        # Breakout strength is distance already traveled; expected FUTURE move is based on the range
        expected_move = max(0.5, (sig.resistance - sig.support) / sig.price * 100 if sig.resistance and sig.support and sig.price and sig.price > 0 else 1.0)
        return Decision(
            action="BUY",
            symbol=symbol,
            confidence=score,
            reasoning=(
                f"[Breakout LONG] {symbol}: score={score:.3f} — "
                f"resistance={sig.resistance:.4f}, strength={strength_str}, "
                f"vol={vol_str}, trend={sig.trend_1h}, "
                f"clean={'yes' if sig.clean_break else 'no'}"
            ),
            strategy_type="breakout",
            order_type="MARKET",
            expected_move_pct=round(expected_move, 2),
        )

    def _check_short_entry(self, symbol: str, sig: BreakoutSignal) -> Decision | None:
        """Score-based SHORT entry: breakout below support."""
        if sig.price is None or sig.support is None:
            return None

        if sig.breakout_dir != "DOWN":
            return None

        # Hard blocks
        blocked, can_buy, abs_funding = check_hard_blocks(
            symbol,
            funding_rates=self._funding_rates,
            cooldown=self._cooldown,
            risk_config=self._rc,
        )
        if blocked:
            logger.debug("[BO SHORT] %s -> HARD BLOCK", symbol)
            return None

        # Trend hard block: BULLISH blocks SHORT
        if sig.trend_1h == "BULLISH":
            logger.debug("[BO SHORT] %s -> HARD BLOCK: trend is BULLISH", symbol)
            return None

        # Scoring
        score = 0.0
        components: list[str] = []

        # Breakout strength: how far past support, normalized 0-1 (cap at 2%)
        if sig.breakout_strength is not None and sig.breakout_strength > 0:
            intensity = min(1.0, sig.breakout_strength / BREAKOUT_STRENGTH_CAP)
            score += W_BREAKOUT_STRENGTH * intensity
            components.append(f"strength={sig.breakout_strength:.2f}%(i={intensity:.2f})")

        # Volume confirmation
        if sig.volume_ratio is not None:
            if sig.volume_ratio >= VOLUME_FULL_THRESHOLD:
                score += W_VOLUME
                components.append(f"vol={sig.volume_ratio:.1f}(full)")
            elif sig.volume_ratio >= VOLUME_PARTIAL_THRESHOLD:
                partial = (sig.volume_ratio - VOLUME_PARTIAL_THRESHOLD) / (
                    VOLUME_FULL_THRESHOLD - VOLUME_PARTIAL_THRESHOLD
                )
                score += W_VOLUME * partial
                components.append(f"vol={sig.volume_ratio:.1f}(partial)")

        # Trend alignment
        if sig.trend_1h == "BEARISH":
            score += W_TREND
            components.append("trend=BEAR")
        elif sig.trend_1h == "NEUTRAL":
            score += W_TREND * 0.5
            components.append("trend=NEUTRAL(0.5)")

        # Clean break
        if sig.clean_break:
            score += W_CLEAN_BREAK
            components.append("clean_break")
        else:
            # Partial credit based on confirm bars
            confirm_bars = self._sc.bo_confirm_bars
            if confirm_bars > 0 and sig.price is not None and sig.support is not None:
                df_15m = self._md.get_candles(symbol, "15m")
                if df_15m is not None and len(df_15m) >= confirm_bars:
                    recent_closes = df_15m["close"].iloc[-confirm_bars:]
                    bars_below = int((recent_closes < sig.support).sum())
                    ratio = bars_below / confirm_bars
                    score += W_CLEAN_BREAK * ratio
                    components.append(f"partial_break({bars_below}/{confirm_bars})")

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
                "[BO SHORT] %s: score=%.3f < %.2f — strength=%s, vol=%s, trend=%s",
                symbol, score, SCORE_THRESHOLD,
                f"{sig.breakout_strength:.2f}%" if sig.breakout_strength else "N/A",
                f"{sig.volume_ratio:.1f}" if sig.volume_ratio else "N/A",
                sig.trend_1h,
            )
            return None

        vol_str = f"{sig.volume_ratio:.1f}" if sig.volume_ratio is not None else "N/A"
        strength_str = f"{sig.breakout_strength:.2f}%" if sig.breakout_strength is not None else "N/A"

        logger.debug("[BO SHORT] %s: score=%.3f — %s", symbol, score, ", ".join(components))
        # Breakout strength is distance already traveled; expected FUTURE move is based on the range
        expected_move = max(0.5, (sig.resistance - sig.support) / sig.price * 100 if sig.resistance and sig.support and sig.price and sig.price > 0 else 1.0)
        return Decision(
            action="SHORT",
            symbol=symbol,
            confidence=score,
            reasoning=(
                f"[Breakout SHORT] {symbol}: score={score:.3f} — "
                f"support={sig.support:.4f}, strength={strength_str}, "
                f"vol={vol_str}, trend={sig.trend_1h}, "
                f"clean={'yes' if sig.clean_break else 'no'}"
            ),
            strategy_type="breakout",
            order_type="MARKET",
            expected_move_pct=round(expected_move, 2),
        )

    # -- Event logging --

    async def _log_signal(self, decision: Decision, sig: BreakoutSignal) -> None:
        if not self._db or self._cycle_count <= 0:
            return
        try:
            await self._db.insert_event(
                cycle=self._cycle_count,
                symbol=decision.symbol or "",
                event_type="SIGNAL",
                source="breakout",
                action=decision.action,
                confidence=decision.confidence,
                reasoning=decision.reasoning,
                details={
                    "score": decision.confidence,
                    "breakout_dir": sig.breakout_dir,
                    "resistance": sig.resistance,
                    "support": sig.support,
                    "breakout_strength": sig.breakout_strength,
                    "clean_break": sig.clean_break,
                    "volume_ratio": sig.volume_ratio,
                    "trend_1h": sig.trend_1h,
                    "expected_move_pct": round(max(0.5, (sig.resistance - sig.support) / sig.price * 100 if sig.resistance and sig.support and sig.price and sig.price > 0 else 1.0), 2),
                },
            )
        except Exception:
            logger.debug("Failed to log SIGNAL event", exc_info=True)

    # -- State (for snapshot / dashboard) --

    def get_state(self) -> dict[str, Any]:
        """Return signal map for the AI market snapshot."""
        signals: dict[str, Any] = {}
        breakouts_up: list[str] = []
        breakouts_down: list[str] = []

        for sym, sig in self._signals.items():
            signals[sym] = {
                "breakout_dir": sig.breakout_dir,
                "resistance": sig.resistance,
                "support": sig.support,
                "breakout_strength": sig.breakout_strength,
                "clean_break": sig.clean_break,
                "volume_ratio": sig.volume_ratio,
                "trend_1h": sig.trend_1h,
                "price": sig.price,
            }
            if sig.breakout_dir == "UP":
                breakouts_up.append(sym)
            elif sig.breakout_dir == "DOWN":
                breakouts_down.append(sym)

        return {
            "strategy": "breakout",
            "lookback_hours": self._sc.bo_lookback_hours,
            "min_breakout_pct": self._sc.bo_min_breakout_pct,
            "confirm_bars": self._sc.bo_confirm_bars,
            "coins_scanned": len(self._signals),
            "breakouts_up": breakouts_up,
            "breakouts_down": breakouts_down,
            "signals": signals,
            "cooldown": self._cooldown.get_state(),
            "trend_filter": self._trend.get_all_states(),
        }
