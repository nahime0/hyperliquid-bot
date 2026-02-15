"""Mean Reversion — scoring-based decision generator.

Generates BUY/SHORT/SELL/CLOSE decisions using a scoring system.
Each condition contributes a weighted score; signal fires when score >= 0.60.
The AI reviews every signal and makes the final call.

LONG Entry scoring:
  - RSI(14) < 35 on 15m           → +0.25
  - Price <= lower Bollinger Band  → +0.25
  - Volume ratio >= 1.0            → +0.15
  - RSI on 1h < 60 (macro align)  → +0.15
  - |funding| < max_funding_rate   → +0.10
  - No per-symbol cooldown         → +0.10
  Score >= 0.60 → generate signal (confidence = score)

SHORT Entry scoring (mirrored):
  - RSI(14) > 65 on 15m           → +0.25
  - Price >= upper Bollinger Band  → +0.25
  - Volume ratio >= 1.0            → +0.15
  - RSI on 1h > 40 (macro align)  → +0.15
  - |funding| < max_funding_rate   → +0.10
  - No per-symbol cooldown         → +0.10

Hard blocks (always reject, bypass scoring):
  - |funding| >= 0.001 (extreme funding)
  - Global cooldown active (3+ consecutive losses)
  - Fresh per-symbol cooldown (loss < 10 min ago)
  - Trend filter: BEARISH blocks LONG, BULLISH blocks SHORT (NEUTRAL allowed)

Exit (ANY trigger, unchanged):
  - LONG: RSI > 65 or price >= upper BB
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
from data.db import Database
from risk.position_tracker import PositionTracker
from strategies.base import Strategy
from strategies.cooldown import CooldownTracker
from strategies.structure_filter import check_structure_confirmation
from strategies.trend_filter import TrendFilter
from utils.logger import get_logger

logger = get_logger(__name__)

# ── Signal thresholds ────────────────────────────────────────

RSI_OVERSOLD = 35.0           # LONG threshold
RSI_OVERBOUGHT = 65.0         # exit for LONG
RSI_OVERBOUGHT_ENTRY = 65.0   # SHORT entry threshold
RSI_1H_MAX = 60.0             # macro RSI ceiling for LONG entries
RSI_1H_MIN_SHORT = 40.0       # macro RSI floor for SHORT entries
RSI_PERIOD = 14
BB_PERIOD = 20
BB_STD = 2.0
MIN_VOLUME_RATIO = 1.0        # volume ratio threshold

# ── Scoring weights ─────────────────────────────────────────
W_RSI = 0.25                  # RSI oversold/overbought
W_BB = 0.25                   # price at/beyond Bollinger Band
W_VOLUME = 0.15               # volume ratio >= threshold
W_MACRO_RSI = 0.15            # 1h RSI alignment
W_FUNDING = 0.10              # funding rate acceptable
W_COOLDOWN = 0.10             # no per-symbol cooldown
SCORE_THRESHOLD = 0.60        # minimum score to generate signal

# ── Hard-block thresholds ───────────────────────────────────
HARD_FUNDING_RATE = 0.001     # extreme funding → always block
HARD_COOLDOWN_FRESHNESS = 600 # block if loss < 10 min ago


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
        self._signals: dict[str, Signal] = {}
        self._funding_rates: dict[str, float] = {}
        self._max_funding_rate: float = 0.0005  # default, overridden from settings
        self._db = db
        self._cycle_count: int = 0

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

    def set_cycle(self, cycle: int) -> None:
        self._cycle_count = cycle

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
        """Classify signal using OR logic (either RSI or BB triggers classification)."""
        if rsi is None:
            return "NEUTRAL", 0.0

        rsi_oversold = rsi < RSI_OVERSOLD
        bb_oversold = price <= bb_lower * 1.005

        if rsi_oversold or bb_oversold:
            parts: list[float] = []
            if rsi_oversold:
                parts.append(max(0.0, (RSI_OVERSOLD - rsi) / RSI_OVERSOLD))
            if bb_oversold:
                parts.append(max(0.0, 1.0 - bb_pct))
            strength = min(1.0, sum(parts) / len(parts))
            return "OVERSOLD", round(strength, 3)

        rsi_overbought = rsi > RSI_OVERBOUGHT
        bb_overbought = price >= bb_upper * 0.995

        if rsi_overbought or bb_overbought:
            parts = []
            if rsi_overbought:
                parts.append(max(0.0, (rsi - RSI_OVERBOUGHT) / (100 - RSI_OVERBOUGHT)))
            if bb_overbought:
                parts.append(min(1.0, bb_pct))
            strength = min(1.0, sum(parts) / len(parts))
            return "OVERBOUGHT", round(strength, 3)

        return "NEUTRAL", 0.0

    # ── Autonomous decision generation ───────────────────────

    async def generate_decisions(self) -> list[Decision]:
        """Generate BUY/SHORT/SELL/CLOSE decisions based on codified rules.

        Checks exits first (on open positions), then entries for both directions.
        Returns Decision objects ready for risk validation and optional AI review.
        """
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

            if pos.get("strategy") != "mean_reversion":
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
                await self._log_signal(entry_decision, sig)
                continue  # skip SHORT check — one direction per coin per cycle

            entry_decision = self._check_short_entry(symbol, sig)
            if entry_decision:
                decisions.append(entry_decision)
                await self._log_signal(entry_decision, sig)

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
            reasons.append(f"RSI={sig.rsi:.1f}>65")

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
        """Score-based LONG entry. Score >= 0.60 generates a signal for AI review."""
        if sig.rsi is None or sig.price is None or sig.bb_lower is None:
            return None

        # ── Hard blocks ──────────────────────────────────────
        funding_rate = abs(self._funding_rates.get(symbol, 0.0))
        if funding_rate >= HARD_FUNDING_RATE:
            logger.debug("[MR LONG] %s → HARD BLOCK: extreme funding %.4f", symbol, funding_rate)
            return None

        if self._cooldown.is_global_cooldown_active():
            logger.debug("[MR LONG] %s → HARD BLOCK: global cooldown", symbol)
            return None

        can_buy, _cd_reason = self._cooldown.can_buy(symbol)
        if not can_buy:
            remaining = self._cooldown.get_symbol_cooldown_remaining(symbol)
            time_since = self._rc.symbol_cooldown_sec - remaining
            if time_since < HARD_COOLDOWN_FRESHNESS:
                logger.debug("[MR LONG] %s → HARD BLOCK: fresh cooldown (%ds ago)", symbol, int(time_since))
                return None

        # ── Trend hard block ───────────────────────────────────
        if sig.trend == "BEARISH":
            logger.debug("[MR LONG] %s → HARD BLOCK: trend is BEARISH", symbol)
            return None

        # ── Scoring (graduated intensity) ──────────────────────
        score = 0.0
        components: list[str] = []

        # RSI: graduated — deeper oversold scores higher
        if sig.rsi < RSI_OVERSOLD:
            intensity = min(1.0, (RSI_OVERSOLD - sig.rsi) / RSI_OVERSOLD)
            score += W_RSI * intensity
            components.append(f"RSI={sig.rsi:.1f}<{RSI_OVERSOLD:.0f}(i={intensity:.2f})")

        # BB: graduated — farther below BB scores higher (floor 0.2)
        if sig.price <= sig.bb_lower * 1.005:
            bb_distance = sig.bb_lower - sig.price
            intensity = max(0.2, min(1.0, bb_distance / (sig.bb_lower * 0.01))) if sig.bb_lower > 0 else 0.2
            score += W_BB * intensity
            components.append(f"below_BB(i={intensity:.2f})")

        if sig.volume_ratio is not None and sig.volume_ratio >= MIN_VOLUME_RATIO:
            score += W_VOLUME
            components.append(f"vol={sig.volume_ratio:.1f}")

        if sig.rsi_1h is not None and sig.rsi_1h < RSI_1H_MAX:
            score += W_MACRO_RSI
            components.append(f"RSI_1h={sig.rsi_1h:.1f}")

        if funding_rate < self._max_funding_rate:
            score += W_FUNDING
            components.append("funding_ok")

        if can_buy:
            score += W_COOLDOWN
            components.append("no_cd")

        score = round(score, 4)

        if score < SCORE_THRESHOLD:
            logger.debug(
                "[MR LONG] %s: score=%.3f < %.2f — RSI=%.1f, BB%%=%.2f, vol=%s, RSI_1h=%s",
                symbol, score, SCORE_THRESHOLD, sig.rsi, sig.bb_pct,
                f"{sig.volume_ratio:.1f}" if sig.volume_ratio else "N/A",
                f"{sig.rsi_1h:.1f}" if sig.rsi_1h else "N/A",
            )
            return None

        # ── Structure confirmation on 5m candles ─────────────────
        df_5m = self._md.get_candles(symbol, "5m")
        confirmed, sc_reason = check_structure_confirmation(df_5m, "LONG")
        if not confirmed:
            logger.debug("[MR LONG] %s -> no structure confirmation (%s), skipping", symbol, sc_reason)
            return None

        vol_str = f"{sig.volume_ratio:.1f}" if sig.volume_ratio is not None else "N/A"
        rsi_1h_str = f"{sig.rsi_1h:.1f}" if sig.rsi_1h is not None else "N/A"

        logger.debug("[MR LONG] %s: score=%.3f — %s", symbol, score, ", ".join(components))
        expected_move = abs(sig.price - sig.bb_mid) / sig.price * 100
        return Decision(
            action="BUY",
            symbol=symbol,
            confidence=score,
            reasoning=(
                f"[MeanRev LONG] {symbol}: score={score:.3f} — "
                f"RSI={sig.rsi:.1f}, BB%={sig.bb_pct:.2f}, "
                f"vol={vol_str}, RSI_1h={rsi_1h_str}, trend={sig.trend}"
            ),
            strategy_type="mean_reversion",
            order_type="MARKET",
            expected_move_pct=round(expected_move, 2),
        )

    def _check_short_entry(self, symbol: str, sig: Signal) -> Decision | None:
        """Score-based SHORT entry. Score >= 0.60 generates a signal for AI review."""
        if sig.rsi is None or sig.price is None or sig.bb_upper is None:
            return None

        # ── Hard blocks ──────────────────────────────────────
        funding_rate = abs(self._funding_rates.get(symbol, 0.0))
        if funding_rate >= HARD_FUNDING_RATE:
            logger.debug("[MR SHORT] %s → HARD BLOCK: extreme funding %.4f", symbol, funding_rate)
            return None

        if self._cooldown.is_global_cooldown_active():
            logger.debug("[MR SHORT] %s → HARD BLOCK: global cooldown", symbol)
            return None

        can_buy, _cd_reason = self._cooldown.can_buy(symbol)
        if not can_buy:
            remaining = self._cooldown.get_symbol_cooldown_remaining(symbol)
            time_since = self._rc.symbol_cooldown_sec - remaining
            if time_since < HARD_COOLDOWN_FRESHNESS:
                logger.debug("[MR SHORT] %s → HARD BLOCK: fresh cooldown (%ds ago)", symbol, int(time_since))
                return None

        # ── Trend hard block ───────────────────────────────────
        if sig.trend == "BULLISH":
            logger.debug("[MR SHORT] %s → HARD BLOCK: trend is BULLISH", symbol)
            return None

        # ── Scoring (graduated intensity) ──────────────────────
        score = 0.0
        components: list[str] = []

        # RSI: graduated — deeper overbought scores higher
        if sig.rsi > RSI_OVERBOUGHT_ENTRY:
            intensity = min(1.0, (sig.rsi - RSI_OVERBOUGHT_ENTRY) / (100 - RSI_OVERBOUGHT_ENTRY))
            score += W_RSI * intensity
            components.append(f"RSI={sig.rsi:.1f}>{RSI_OVERBOUGHT_ENTRY:.0f}(i={intensity:.2f})")

        # BB: graduated — farther above BB scores higher (floor 0.2)
        if sig.price >= sig.bb_upper * 0.995:
            bb_distance = sig.price - sig.bb_upper
            intensity = max(0.2, min(1.0, bb_distance / (sig.bb_upper * 0.01))) if sig.bb_upper > 0 else 0.2
            score += W_BB * intensity
            components.append(f"above_BB(i={intensity:.2f})")

        if sig.volume_ratio is not None and sig.volume_ratio >= MIN_VOLUME_RATIO:
            score += W_VOLUME
            components.append(f"vol={sig.volume_ratio:.1f}")

        if sig.rsi_1h is not None and sig.rsi_1h > RSI_1H_MIN_SHORT:
            score += W_MACRO_RSI
            components.append(f"RSI_1h={sig.rsi_1h:.1f}")

        if funding_rate < self._max_funding_rate:
            score += W_FUNDING
            components.append("funding_ok")

        if can_buy:
            score += W_COOLDOWN
            components.append("no_cd")

        score = round(score, 4)

        if score < SCORE_THRESHOLD:
            logger.debug(
                "[MR SHORT] %s: score=%.3f < %.2f — RSI=%.1f, BB%%=%.2f, vol=%s, RSI_1h=%s",
                symbol, score, SCORE_THRESHOLD, sig.rsi, sig.bb_pct,
                f"{sig.volume_ratio:.1f}" if sig.volume_ratio else "N/A",
                f"{sig.rsi_1h:.1f}" if sig.rsi_1h else "N/A",
            )
            return None

        # ── Structure confirmation on 5m candles ─────────────────
        df_5m = self._md.get_candles(symbol, "5m")
        confirmed, sc_reason = check_structure_confirmation(df_5m, "SHORT")
        if not confirmed:
            logger.debug("[MR SHORT] %s -> no structure confirmation (%s), skipping", symbol, sc_reason)
            return None

        vol_str = f"{sig.volume_ratio:.1f}" if sig.volume_ratio is not None else "N/A"
        rsi_1h_str = f"{sig.rsi_1h:.1f}" if sig.rsi_1h is not None else "N/A"

        logger.debug("[MR SHORT] %s: score=%.3f — %s", symbol, score, ", ".join(components))
        expected_move = abs(sig.price - sig.bb_mid) / sig.price * 100
        return Decision(
            action="SHORT",
            symbol=symbol,
            confidence=score,
            reasoning=(
                f"[MeanRev SHORT] {symbol}: score={score:.3f} — "
                f"RSI={sig.rsi:.1f}, BB%={sig.bb_pct:.2f}, "
                f"vol={vol_str}, RSI_1h={rsi_1h_str}, trend={sig.trend}"
            ),
            strategy_type="mean_reversion",
            order_type="MARKET",
            expected_move_pct=round(expected_move, 2),
        )

    async def _log_signal(self, decision: Decision, sig: Signal) -> None:
        if not self._db or self._cycle_count <= 0:
            return
        try:
            await self._db.insert_event(
                cycle=self._cycle_count,
                symbol=decision.symbol or "",
                event_type="SIGNAL",
                source="mean_reversion",
                action=decision.action,
                confidence=decision.confidence,
                reasoning=decision.reasoning,
                details={
                    "score": decision.confidence,
                    "rsi": round(sig.rsi, 2) if sig.rsi is not None else None,
                    "rsi_1h": round(sig.rsi_1h, 2) if sig.rsi_1h is not None else None,
                    "bb_pct": sig.bb_pct,
                    "trend": sig.trend,
                    "volume_ratio": sig.volume_ratio,
                    "expected_move_pct": round(abs(sig.price - sig.bb_mid) / sig.price * 100, 2) if sig.price and sig.bb_mid else None,
                },
            )
        except Exception:
            logger.debug("Failed to log SIGNAL event", exc_info=True)

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
