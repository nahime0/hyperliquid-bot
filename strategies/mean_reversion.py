"""Mean Reversion — autonomous decision generator.

Generates BUY/SHORT/SELL/CLOSE decisions autonomously.
The AI is demoted to an optional review/veto role.

LONG Entry (ALL must be true):
  - Trend filter BULLISH or NEUTRAL on 1h
  - RSI(14) < 30 on 15m (oversold)
  - Price <= lower Bollinger Band on 15m
  - RSI on 1h < 60 (no macro divergence)
  - Cooldown passed for the symbol
  - No open position on the same symbol

SHORT Entry (ALL must be true):
  - Trend filter BEARISH on 1h (EMA50 < EMA200, price < EMA50, slope < 0)
  - RSI(14) > 70 on 15m (overbought)
  - Price >= upper Bollinger Band on 15m
  - RSI on 1h > 40
  - Cooldown passed + no open position

Funding rate: |funding| < max_funding_rate before any entry.

Exit (ANY trigger):
  - LONG: RSI > 70 or price >= upper BB
  - SHORT: RSI < 30 or price <= lower BB
  - Time stop: 4h+ with PnL < 0.5% (handled by PositionTracker)
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
from risk.position_tracker import PositionTracker
from strategies.base import Strategy
from strategies.cooldown import CooldownTracker
from strategies.trend_filter import TrendFilter
from utils.logger import get_logger

logger = get_logger(__name__)

# ── Signal thresholds ────────────────────────────────────────

RSI_OVERSOLD = 30.0           # standard oversold (was 25)
RSI_OVERBOUGHT = 70.0         # exit for LONG
RSI_OVERBOUGHT_ENTRY = 70.0   # entry for SHORT (was 75)
RSI_1H_MAX = 60.0             # macro RSI ceiling for LONG entries
RSI_1H_MIN_SHORT = 40.0       # macro RSI floor for SHORT entries
RSI_PERIOD = 14
BB_PERIOD = 20
BB_STD = 2.0
MIN_VOLUME_RATIO = 1.0        # no volume filter (was 1.2)


# ── Data classes ─────────────────────────────────────────────

@dataclass
class Signal:
    """Signal for one coin at a point in time."""
    symbol: str
    signal: str            # OVERSOLD, OVERBOUGHT, NEUTRAL
    strength: float        # 0-1
    rsi: float | None
    rsi_1h: float | None
    price: float | None
    bb_lower: float | None
    bb_upper: float | None
    bb_mid: float | None
    bb_pct: float | None
    volume_ratio: float | None
    trend: str             # from TrendFilter
    updated_at: float


# ── Strategy ─────────────────────────────────────────────────

class MeanReversionStrategy(Strategy):
    """Autonomous mean-reversion strategy with trend filter, cooldown, and LONG/SHORT."""

    def __init__(
        self,
        market_data: MarketData,
        trend_filter: TrendFilter,
        cooldown: CooldownTracker,
        position_tracker: PositionTracker,
        risk_config: RiskConfig,
        coins: list[str] | None = None,
        interval: str = "15m",
        min_candle_volume_usdc: float = 10_000.0,
    ) -> None:
        self._md = market_data
        self._trend = trend_filter
        self._cooldown = cooldown
        self._positions = position_tracker
        self._rc = risk_config
        self._coins = coins or []
        self._interval = interval
        self._min_candle_volume_usdc = min_candle_volume_usdc
        self._signals: dict[str, Signal] = {}
        self._funding_rates: dict[str, float] = {}
        self._max_funding_rate: float = 0.0005  # default, overridden from settings

    def set_coins(self, coins: list[str]) -> None:
        """Update the list of coins to scan (for dynamic discovery)."""
        self._coins = coins
        logger.info("MeanReversion coins updated: %d", len(coins))

    def set_funding_rates(self, rates: dict[str, float]) -> None:
        """Update cached funding rates (from main loop)."""
        self._funding_rates = rates

    def set_max_funding_rate(self, rate: float) -> None:
        """Set max acceptable funding rate from settings."""
        self._max_funding_rate = rate

    # ── Lifecycle ────────────────────────────────────────────

    async def start(self) -> None:
        logger.info(
            "MeanReversionStrategy started — scanning %d coins on %s",
            len(self._coins), self._interval,
        )

    async def stop(self) -> None:
        self._signals.clear()
        logger.info("MeanReversionStrategy stopped")

    # ── Update (called every tick) ───────────────────────────

    async def update(self) -> None:
        """Scan all coins and update signals."""
        tasks = [self._scan_pair(sym) for sym in self._coins]
        await asyncio.gather(*tasks)

    async def _scan_pair(self, symbol: str) -> None:
        """Compute indicators and classify signal for one coin."""
        try:
            df = self._md.get_candles(symbol, self._interval)
            if df is None or len(df) < BB_PERIOD + 5:
                return

            close = df["close"]
            volume = df["volume"]

            # Volume floor: skip illiquid coins
            if self._min_candle_volume_usdc > 0:
                volume_usdc = float(close.iloc[-1] * volume.iloc[-1])
                if volume_usdc < self._min_candle_volume_usdc:
                    logger.debug(
                        "Skipping %s: candle volume %.0f USDC < %.0f",
                        symbol, volume_usdc, self._min_candle_volume_usdc,
                    )
                    return

            # RSI on entry timeframe (15m)
            rsi_series = ta_lib.momentum.RSIIndicator(close, window=RSI_PERIOD).rsi()
            rsi = float(rsi_series.iloc[-1]) if not rsi_series.empty and pd.notna(rsi_series.iloc[-1]) else None

            # RSI on 1h (macro filter)
            rsi_1h = None
            df_1h = self._md.get_candles(symbol, "1h")
            if df_1h is not None and len(df_1h) >= RSI_PERIOD + 1:
                rsi_1h_s = ta_lib.momentum.RSIIndicator(df_1h["close"], window=RSI_PERIOD).rsi()
                if not rsi_1h_s.empty and pd.notna(rsi_1h_s.iloc[-1]):
                    rsi_1h = float(rsi_1h_s.iloc[-1])

            # Bollinger Bands
            bb = ta_lib.volatility.BollingerBands(close, window=BB_PERIOD, window_dev=BB_STD)
            bb_lower = float(bb.bollinger_lband().iloc[-1])
            bb_upper = float(bb.bollinger_hband().iloc[-1])
            bb_mid = float(bb.bollinger_mavg().iloc[-1])

            price = float(close.iloc[-1])

            bb_range = bb_upper - bb_lower
            bb_pct = (price - bb_lower) / bb_range if bb_range > 0 else 0.5

            # Volume ratio
            vol_sma = float(volume.rolling(20).mean().iloc[-1]) if len(volume) >= 20 else None
            vol_ratio = float(volume.iloc[-1]) / vol_sma if vol_sma and vol_sma > 0 else None

            # Classify
            sig, strength = self._classify(rsi, price, bb_lower, bb_upper, bb_pct)

            # Get trend from TrendFilter
            trend_state = self._trend.get_state(symbol)

            self._signals[symbol] = Signal(
                symbol=symbol,
                signal=sig,
                strength=strength,
                rsi=rsi,
                rsi_1h=rsi_1h,
                price=price,
                bb_lower=bb_lower,
                bb_upper=bb_upper,
                bb_mid=bb_mid,
                bb_pct=round(bb_pct, 4),
                volume_ratio=round(vol_ratio, 2) if vol_ratio else None,
                trend=trend_state.get("trend", "UNKNOWN"),
                updated_at=time.time(),
            )
        except Exception:
            logger.exception("Error scanning %s", symbol)

    @staticmethod
    def _classify(
        rsi: float | None,
        price: float,
        bb_lower: float,
        bb_upper: float,
        bb_pct: float,
    ) -> tuple[str, float]:
        if rsi is None:
            return "NEUTRAL", 0.0

        if rsi < RSI_OVERSOLD and price <= bb_lower * 1.005:
            rsi_strength = max(0.0, (RSI_OVERSOLD - rsi) / RSI_OVERSOLD)
            bb_strength = max(0.0, 1.0 - bb_pct)
            strength = min(1.0, (rsi_strength + bb_strength) / 2)
            return "OVERSOLD", round(strength, 3)

        if rsi > RSI_OVERBOUGHT and price >= bb_upper * 0.995:
            rsi_strength = max(0.0, (rsi - RSI_OVERBOUGHT) / (100 - RSI_OVERBOUGHT))
            bb_strength = max(0.0, bb_pct - 1.0 + 1.0)
            strength = min(1.0, (rsi_strength + bb_strength) / 2)
            return "OVERBOUGHT", round(strength, 3)

        return "NEUTRAL", 0.0

    # ── Funding rate check ────────────────────────────────────

    def _funding_ok(self, symbol: str) -> bool:
        """Check if funding rate is acceptable for entry."""
        rate = self._funding_rates.get(symbol)
        if rate is None:
            return True  # no data = allow
        return abs(rate) < self._max_funding_rate

    # ── Autonomous decision generation ───────────────────────

    async def generate_decisions(self) -> list[Decision]:
        """Generate BUY/SHORT/SELL/CLOSE decisions based on codified rules.

        Checks exits first (on open positions), then entries for both directions.
        Returns Decision objects ready for risk validation and optional AI review.
        """
        decisions: list[Decision] = []

        open_symbols = await self._positions.get_open_symbols()

        # ── EXIT checks (on open positions) ──
        for symbol in list(open_symbols):
            sig = self._signals.get(symbol)
            if not sig:
                continue

            pos = await self._positions.get_position_for_symbol(symbol)
            if not pos:
                continue

            direction = pos.get("direction", "LONG")

            if direction == "LONG":
                exit_decision = self._check_long_exit(symbol, sig)
            else:
                exit_decision = self._check_short_exit(symbol, sig)

            if exit_decision:
                decisions.append(exit_decision)

        # ── ENTRY checks (LONG or SHORT, never both per symbol) ──
        scanned = 0
        for symbol in self._coins:
            if symbol in open_symbols:
                continue

            sig = self._signals.get(symbol)
            if not sig:
                continue

            scanned += 1
            entry_decision = self._check_long_entry(symbol, sig)
            if entry_decision:
                decisions.append(entry_decision)
                continue  # skip SHORT check — one direction per coin per cycle

            entry_decision = self._check_short_entry(symbol, sig)
            if entry_decision:
                decisions.append(entry_decision)

        entries = sum(1 for d in decisions if d.action in ("BUY", "SHORT"))
        logger.info(
            "[MR SCAN] coins=%d, scanned=%d, signals_generated=%d",
            len(self._coins), scanned, entries,
        )
        return decisions

    def _check_long_exit(self, symbol: str, sig: Signal) -> Decision | None:
        """Check if a LONG position should be closed."""
        reasons: list[str] = []

        if sig.rsi is not None and sig.rsi > RSI_OVERBOUGHT:
            reasons.append(f"RSI={sig.rsi:.1f}>70")

        if sig.price and sig.bb_upper and sig.price >= sig.bb_upper * 0.995:
            reasons.append("price>=upper_BB")

        if not reasons:
            return None

        return Decision(
            action="CLOSE",
            symbol=symbol,
            confidence=0.8,
            reasoning=f"[MeanRev LONG EXIT] {symbol}: {', '.join(reasons)}",
            strategy_type="mean_reversion",
            order_type="MARKET",
        )

    def _check_short_exit(self, symbol: str, sig: Signal) -> Decision | None:
        """Check if a SHORT position should be covered."""
        reasons: list[str] = []

        if sig.rsi is not None and sig.rsi < 30:
            reasons.append(f"RSI={sig.rsi:.1f}<30")

        if sig.price and sig.bb_lower and sig.price <= sig.bb_lower * 1.005:
            reasons.append("price<=lower_BB")

        if not reasons:
            return None

        return Decision(
            action="CLOSE",
            symbol=symbol,
            confidence=0.8,
            reasoning=f"[MeanRev SHORT EXIT] {symbol}: {', '.join(reasons)}",
            strategy_type="mean_reversion",
            order_type="MARKET",
        )

    def _check_long_entry(self, symbol: str, sig: Signal) -> Decision | None:
        """Check if a new LONG entry is warranted. ALL conditions must be true."""
        # BB position label for diagnostics
        bb_pos = "below_BB" if (sig.price and sig.bb_lower and sig.price <= sig.bb_lower * 1.005) else (
            "above_BB" if (sig.price and sig.bb_upper and sig.price >= sig.bb_upper * 0.995) else "inside_BB"
        )
        diag = (
            f"trend={sig.trend}, RSI_15m={f'{sig.rsi:.1f}' if sig.rsi is not None else 'N/A'}, "
            f"BB={bb_pos}, vol_ratio={sig.volume_ratio if sig.volume_ratio else 'N/A'}, "
            f"RSI_1h={f'{sig.rsi_1h:.1f}' if sig.rsi_1h is not None else 'N/A'}"
        )

        if sig.trend not in ("BULLISH", "NEUTRAL"):
            logger.info("[MR LONG] %s: %s → SKIP: trend not bullish/neutral", symbol, diag)
            return None
        if sig.rsi is None or sig.rsi >= RSI_OVERSOLD:
            logger.info("[MR LONG] %s: %s → SKIP: RSI >= %.0f", symbol, diag, RSI_OVERSOLD)
            return None
        if sig.price is None or sig.bb_lower is None or sig.price > sig.bb_lower * 1.005:
            logger.info("[MR LONG] %s: %s → SKIP: price not near lower BB", symbol, diag)
            return None
        if sig.volume_ratio is not None and sig.volume_ratio < MIN_VOLUME_RATIO:
            logger.info("[MR LONG] %s: %s → SKIP: vol_ratio < %.1f", symbol, diag, MIN_VOLUME_RATIO)
            return None
        if sig.rsi_1h is not None and sig.rsi_1h >= RSI_1H_MAX:
            logger.info("[MR LONG] %s: %s → SKIP: RSI_1h >= %.0f", symbol, diag, RSI_1H_MAX)
            return None
        if not self._funding_ok(symbol):
            logger.info("[MR LONG] %s: %s → SKIP: high funding rate", symbol, diag)
            return None

        can_buy, reason = self._cooldown.can_buy(symbol)
        if not can_buy:
            logger.info("[MR LONG] %s: %s → SKIP: %s", symbol, diag, reason)
            return None

        logger.info("[MR LONG] %s: %s → SIGNAL GENERATED", symbol, diag)
        return Decision(
            action="BUY",
            symbol=symbol,
            confidence=min(0.8, 0.5 + sig.strength * 0.3),
            reasoning=(
                f"[MeanRev LONG] {symbol}: RSI={sig.rsi:.1f}, "
                f"BB%={sig.bb_pct:.2f}, vol_ratio={sig.volume_ratio:.1f}, "
                f"trend={sig.trend}, RSI_1h={sig.rsi_1h:.1f}" if sig.rsi_1h else
                f"[MeanRev LONG] {symbol}: RSI={sig.rsi:.1f}, "
                f"BB%={sig.bb_pct:.2f}, vol_ratio={sig.volume_ratio:.1f}, "
                f"trend={sig.trend}"
            ),
            strategy_type="mean_reversion",
            size_pct=10.0,
            order_type="MARKET",
        )

    def _check_short_entry(self, symbol: str, sig: Signal) -> Decision | None:
        """Check if a new SHORT entry is warranted. ALL conditions must be true."""
        bb_pos = "below_BB" if (sig.price and sig.bb_lower and sig.price <= sig.bb_lower * 1.005) else (
            "above_BB" if (sig.price and sig.bb_upper and sig.price >= sig.bb_upper * 0.995) else "inside_BB"
        )
        diag = (
            f"trend={sig.trend}, RSI_15m={f'{sig.rsi:.1f}' if sig.rsi is not None else 'N/A'}, "
            f"BB={bb_pos}, vol_ratio={sig.volume_ratio if sig.volume_ratio else 'N/A'}, "
            f"RSI_1h={f'{sig.rsi_1h:.1f}' if sig.rsi_1h is not None else 'N/A'}"
        )

        if not self._trend.is_bearish(symbol):
            logger.info("[MR SHORT] %s: %s → SKIP: trend not bearish", symbol, diag)
            return None
        if sig.rsi is None or sig.rsi <= RSI_OVERBOUGHT_ENTRY:
            logger.info("[MR SHORT] %s: %s → SKIP: RSI <= %.0f", symbol, diag, RSI_OVERBOUGHT_ENTRY)
            return None
        if sig.price is None or sig.bb_upper is None or sig.price < sig.bb_upper * 0.995:
            logger.info("[MR SHORT] %s: %s → SKIP: price not near upper BB", symbol, diag)
            return None
        if sig.volume_ratio is not None and sig.volume_ratio < MIN_VOLUME_RATIO:
            logger.info("[MR SHORT] %s: %s → SKIP: vol_ratio < %.1f", symbol, diag, MIN_VOLUME_RATIO)
            return None
        if sig.rsi_1h is not None and sig.rsi_1h <= RSI_1H_MIN_SHORT:
            logger.info("[MR SHORT] %s: %s → SKIP: RSI_1h <= %.0f", symbol, diag, RSI_1H_MIN_SHORT)
            return None
        if not self._funding_ok(symbol):
            logger.info("[MR SHORT] %s: %s → SKIP: high funding rate", symbol, diag)
            return None

        can_buy, reason = self._cooldown.can_buy(symbol)
        if not can_buy:
            logger.info("[MR SHORT] %s: %s → SKIP: %s", symbol, diag, reason)
            return None

        logger.info("[MR SHORT] %s: %s → SIGNAL GENERATED", symbol, diag)
        return Decision(
            action="SHORT",
            symbol=symbol,
            confidence=min(0.8, 0.5 + sig.strength * 0.3),
            reasoning=(
                f"[MeanRev SHORT] {symbol}: RSI={sig.rsi:.1f}, "
                f"BB%={sig.bb_pct:.2f}, vol_ratio={sig.volume_ratio:.1f}, "
                f"trend={sig.trend}, RSI_1h={sig.rsi_1h:.1f}" if sig.rsi_1h else
                f"[MeanRev SHORT] {symbol}: RSI={sig.rsi:.1f}, "
                f"BB%={sig.bb_pct:.2f}, vol_ratio={sig.volume_ratio:.1f}, "
                f"trend={sig.trend}"
            ),
            strategy_type="mean_reversion",
            size_pct=10.0,
            order_type="MARKET",
        )

    # ── State (for snapshot / dashboard) ──────────────────────

    def get_state(self) -> dict[str, Any]:
        """Return signal map for the AI market snapshot."""
        signals: dict[str, Any] = {}
        oversold: list[str] = []
        overbought: list[str] = []

        for sym, sig in self._signals.items():
            signals[sym] = {
                "signal": sig.signal,
                "strength": sig.strength,
                "rsi": round(sig.rsi, 2) if sig.rsi is not None else None,
                "rsi_1h": round(sig.rsi_1h, 2) if sig.rsi_1h is not None else None,
                "price": sig.price,
                "bb_lower": sig.bb_lower,
                "bb_upper": sig.bb_upper,
                "bb_mid": sig.bb_mid,
                "bb_pct": sig.bb_pct,
                "volume_ratio": sig.volume_ratio,
                "trend": sig.trend,
            }
            if sig.signal == "OVERSOLD":
                oversold.append(sym)
            elif sig.signal == "OVERBOUGHT":
                overbought.append(sym)

        return {
            "strategy": "mean_reversion",
            "interval": self._interval,
            "pairs_scanned": len(self._signals),
            "oversold": oversold,
            "overbought": overbought,
            "signals": signals,
            "cooldown": self._cooldown.get_state(),
            "trend_filter": self._trend.get_all_states(),
        }
