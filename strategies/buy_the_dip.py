"""Buy the Dip -- scoring-based decision generator (LONG only).

Generates BUY/CLOSE decisions for dips in confirmed uptrends.
Uses 5m candles for dip detection and 1h candles for 4h trend computation.

LONG Entry scoring:
  - Dip magnitude in range [-3%, -0.5%]  -> +0.30
  - 4h trend BULLISH (EMA12/EMA26)       -> +0.20
  - 1h trend BULLISH (TrendFilter)        -> +0.15
  - Volume spike >= 1.5x 20-bar SMA      -> +0.15
  - |funding| < max_funding_rate          -> +0.10
  - No per-symbol cooldown               -> +0.10
  Score >= 0.50 -> generate signal (confidence = score)

Hard blocks (always reject, bypass scoring):
  - |funding| >= 0.001 (extreme funding)
  - Global cooldown active (3+ consecutive losses)
  - Fresh per-symbol cooldown (loss < 10 min ago)
  - 4h trend NOT BULLISH -> skip entirely (extra hard block)

Exit (ANY trigger):
  - RSI(5m, 14) > 70  (overbought on entry timeframe)
  - Price >= pre-dip high (high of candle 3 bars ago on 5m)
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

# Scoring weights
W_DIP = 0.30
W_TREND_4H = 0.20
W_TREND_1H = 0.15
W_VOLUME = 0.15
W_FUNDING = 0.10
W_COOLDOWN = 0.10
SCORE_THRESHOLD = 0.60

# EMA params for 4h trend on 1h candles (same as TrendFollowing)
EMA_FAST_4H = 12
EMA_SLOW_4H = 26
SLOPE_WINDOW_4H = 5

# RSI for exit
RSI_PERIOD = 14
RSI_EXIT_OVERBOUGHT = 70.0


@dataclass
class BtdSignal:
    """Signal for one coin at a point in time."""
    symbol: str
    dip_pct: float | None      # % drop in last 15 min (negative)
    trend_1h: str              # BULLISH, BEARISH, NEUTRAL
    trend_4h: str              # BULLISH, BEARISH, NEUTRAL
    volume_ratio: float | None
    rsi_5m: float | None       # RSI on 5m for exit
    price: float | None
    pre_dip_high: float | None  # high of candle 3 bars ago (recovery target)
    updated_at: float


class BuyTheDipStrategy(Strategy):
    """Buy-the-dip strategy: buys short-term dips in confirmed uptrends (LONG only)."""

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
        self._signals: dict[str, BtdSignal] = {}
        self._funding_rates: dict[str, float] = {}
        self._max_funding_rate: float = 0.0005
        self._db = db
        self._cycle_count: int = 0

    # -- Standard setters --

    def set_coins(self, coins: list[str]) -> None:
        self._coins = coins
        logger.info("BuyTheDip coins updated: %d", len(coins))

    def set_funding_rates(self, rates: dict[str, float]) -> None:
        self._funding_rates = rates

    def set_max_funding_rate(self, rate: float) -> None:
        self._max_funding_rate = rate

    def set_cycle(self, cycle: int) -> None:
        self._cycle_count = cycle

    # -- Lifecycle --

    async def start(self) -> None:
        logger.info(
            "BuyTheDipStrategy started -- scanning %d coins on 5m+1h",
            len(self._coins),
        )

    async def stop(self) -> None:
        self._signals.clear()
        logger.info("BuyTheDipStrategy stopped")

    # -- Update (called every tick) --

    async def update(self) -> None:
        """Scan all coins and update signals."""
        tasks = [self._scan_coin(sym) for sym in self._coins]
        await asyncio.gather(*tasks)

    async def _scan_coin(self, symbol: str) -> None:
        """Compute dip signal for one coin using 5m and 1h candles."""
        try:
            # 5m candles for dip detection
            df_5m = self._md.get_candles(symbol, "5m")
            if df_5m is None or len(df_5m) < 24:  # need at least 20 for vol SMA + 4 for dip
                return

            close_5m = df_5m["close"]
            high_5m = df_5m["high"]
            volume_5m = df_5m["volume"]
            price = float(close_5m.iloc[-1])

            # Dip: (close[-1] - close[-4]) / close[-4] * 100
            # close[-4] is the close 3 candles ago (15 min window on 5m)
            if len(close_5m) < 4:
                return
            price_3_ago = float(close_5m.iloc[-4])
            if price_3_ago <= 0:
                return
            dip_pct = (price - price_3_ago) / price_3_ago * 100

            # Pre-dip high: high of the candle 3 bars ago
            pre_dip_high = float(high_5m.iloc[-4])

            # Volume ratio: current 5m volume vs 20-bar SMA
            vol_ratio = None
            if len(volume_5m) >= 20:
                vol_sma = float(volume_5m.rolling(20).mean().iloc[-1])
                if vol_sma > 0:
                    vol_ratio = float(volume_5m.iloc[-1]) / vol_sma

            # RSI on 5m for exit
            rsi_5m = None
            rsi_series = ta_lib.momentum.RSIIndicator(close_5m, window=RSI_PERIOD).rsi()
            if not rsi_series.empty and pd.notna(rsi_series.iloc[-1]):
                rsi_5m = float(rsi_series.iloc[-1])

            # 1h candles for 4h trend (EMA12/EMA26)
            df_1h = self._md.get_candles(symbol, "1h")
            if df_1h is None or len(df_1h) < EMA_SLOW_4H + SLOPE_WINDOW_4H:
                trend_4h = "NEUTRAL"
            else:
                close_1h = df_1h["close"]
                price_1h = float(close_1h.iloc[-1])
                trend_4h = self._compute_trend_4h(close_1h, price_1h)

            # 1h trend from TrendFilter (EMA50/EMA200)
            trend_state = self._trend.get_state(symbol)
            trend_1h = trend_state.get("trend", "NEUTRAL")

            self._signals[symbol] = BtdSignal(
                symbol=symbol,
                dip_pct=round(dip_pct, 3),
                trend_1h=trend_1h,
                trend_4h=trend_4h,
                volume_ratio=round(vol_ratio, 2) if vol_ratio is not None else None,
                rsi_5m=round(rsi_5m, 2) if rsi_5m is not None else None,
                price=price,
                pre_dip_high=pre_dip_high,
                updated_at=time.time(),
            )
        except Exception:
            logger.exception("Error scanning %s for buy-the-dip", symbol)

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
        """Generate BUY/CLOSE decisions based on current signals.

        Checks exits first (on positions opened by this strategy),
        then entries (LONG only -- no SHORT method).
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

            if pos.get("strategy") != "buy_the_dip":
                continue

            exit_d = self._check_exit(symbol, sig)
            if exit_d:
                decisions.append(exit_d)

        # ENTRY checks (LONG only)
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

        entries = sum(1 for d in decisions if d.action == "BUY")
        logger.info(
            "[BTD SCAN] coins=%d, scanned=%d, signals_generated=%d",
            len(self._coins), scanned, entries,
        )
        return decisions

    def _check_long_entry(self, symbol: str, sig: BtdSignal) -> Decision | None:
        """Score-based LONG entry for dips in uptrends."""
        if sig.price is None or sig.dip_pct is None:
            return None

        # Hard blocks via check_hard_blocks()
        blocked, can_buy, abs_funding = check_hard_blocks(
            symbol,
            funding_rates=self._funding_rates,
            cooldown=self._cooldown,
            risk_config=self._rc,
        )
        if blocked:
            return None

        # Extra hard block: 4h trend must be BULLISH
        if sig.trend_4h != "BULLISH":
            logger.debug("[BTD LONG] %s -> HARD BLOCK: trend_4h=%s (need BULLISH)", symbol, sig.trend_4h)
            return None

        # Dip must be negative (price dropped) and within configured range
        abs_dip = abs(sig.dip_pct)
        if sig.dip_pct >= 0 or abs_dip < self._sc.btd_dip_min_pct or abs_dip > self._sc.btd_dip_max_pct:
            return None

        # Scoring
        score = 0.0
        components: list[str] = []

        # Dip magnitude: graduated -- deeper dip (within range) scores higher
        dip_range = self._sc.btd_dip_max_pct - self._sc.btd_dip_min_pct
        if dip_range > 0:
            intensity = min(1.0, (abs_dip - self._sc.btd_dip_min_pct) / dip_range)
        else:
            intensity = 1.0
        score += W_DIP * max(0.3, intensity)  # floor 0.3 to reward any qualifying dip
        components.append(f"dip={sig.dip_pct:.2f}%(i={intensity:.2f})")

        # 4h trend BULLISH (guaranteed by hard block above)
        score += W_TREND_4H
        components.append("trend_4h=BULL")

        # 1h trend
        if sig.trend_1h == "BULLISH":
            score += W_TREND_1H
            components.append("trend_1h=BULL")

        # Volume spike
        if sig.volume_ratio is not None and sig.volume_ratio >= self._sc.btd_volume_spike:
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
                "[BTD LONG] %s: score=%.3f < %.2f -- dip=%.2f%%, trend_4h=%s, trend_1h=%s, vol=%s",
                symbol, score, SCORE_THRESHOLD, sig.dip_pct, sig.trend_4h, sig.trend_1h,
                f"{sig.volume_ratio:.1f}" if sig.volume_ratio is not None else "N/A",
            )
            return None

        vol_str = f"{sig.volume_ratio:.1f}" if sig.volume_ratio is not None else "N/A"
        rsi_str = f"{sig.rsi_5m:.1f}" if sig.rsi_5m is not None else "N/A"

        logger.debug("[BTD LONG] %s: score=%.3f -- %s", symbol, score, ", ".join(components))
        return Decision(
            action="BUY",
            symbol=symbol,
            confidence=score,
            reasoning=(
                f"[BuyTheDip LONG] {symbol}: score={score:.3f} -- "
                f"dip={sig.dip_pct:.2f}%, trend_4h={sig.trend_4h}, "
                f"trend_1h={sig.trend_1h}, vol={vol_str}, RSI_5m={rsi_str}"
            ),
            strategy_type="buy_the_dip",
            order_type="MARKET",
        )

    def _check_exit(self, symbol: str, sig: BtdSignal) -> Decision | None:
        """Exit if RSI(5m) > 70 or price recovers above pre-dip high."""
        reasons: list[str] = []

        if sig.rsi_5m is not None and sig.rsi_5m > RSI_EXIT_OVERBOUGHT:
            reasons.append(f"RSI_5m={sig.rsi_5m:.1f}>{RSI_EXIT_OVERBOUGHT:.0f}")

        if sig.price is not None and sig.pre_dip_high is not None and sig.price >= sig.pre_dip_high:
            reasons.append(f"price={sig.price:.4f}>=pre_dip_high={sig.pre_dip_high:.4f}")

        if not reasons:
            return None

        return Decision(
            action="CLOSE",
            symbol=symbol,
            confidence=0.8,
            reasoning=f"[BuyTheDip EXIT] {symbol}: {', '.join(reasons)}",
            strategy_type="buy_the_dip",
            order_type="MARKET",
        )

    async def _log_signal(self, decision: Decision, sig: BtdSignal) -> None:
        """Log a SIGNAL event to the database."""
        if not self._db or self._cycle_count <= 0:
            return
        try:
            await self._db.insert_event(
                cycle=self._cycle_count,
                symbol=decision.symbol or "",
                event_type="SIGNAL",
                source="buy_the_dip",
                action=decision.action,
                confidence=decision.confidence,
                reasoning=decision.reasoning,
                details={
                    "score": decision.confidence,
                    "dip_pct": sig.dip_pct,
                    "trend_1h": sig.trend_1h,
                    "trend_4h": sig.trend_4h,
                    "volume_ratio": sig.volume_ratio,
                    "rsi_5m": sig.rsi_5m,
                    "pre_dip_high": sig.pre_dip_high,
                },
            )
        except Exception:
            logger.debug("Failed to log SIGNAL event", exc_info=True)

    # -- State (for snapshot / dashboard) --

    def get_state(self) -> dict[str, Any]:
        """Return signal map for the AI market snapshot."""
        signals: dict[str, Any] = {}
        dip_candidates: list[str] = []

        for sym, sig in self._signals.items():
            signals[sym] = {
                "dip_pct": sig.dip_pct,
                "trend_1h": sig.trend_1h,
                "trend_4h": sig.trend_4h,
                "volume_ratio": sig.volume_ratio,
                "rsi_5m": sig.rsi_5m,
                "price": sig.price,
                "pre_dip_high": sig.pre_dip_high,
            }
            if (
                sig.trend_4h == "BULLISH"
                and sig.dip_pct is not None
                and sig.dip_pct < 0
                and abs(sig.dip_pct) >= self._sc.btd_dip_min_pct
                and abs(sig.dip_pct) <= self._sc.btd_dip_max_pct
            ):
                dip_candidates.append(sym)

        return {
            "strategy": "buy_the_dip",
            "intervals": ["5m", "1h"],
            "coins_scanned": len(self._signals),
            "dip_candidates": dip_candidates,
            "signals": signals,
            "cooldown": self._cooldown.get_state(),
            "trend_filter": self._trend.get_all_states(),
        }
