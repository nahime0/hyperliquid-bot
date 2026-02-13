"""Funding Rate Contrarian — scoring-based decision generator.

Generates BUY/SHORT/CLOSE decisions based on extreme funding rates.
When funding is extremely negative (crowded shorts), go LONG (contrarian).
When funding is extremely positive (crowded longs), go SHORT (contrarian).

LONG Entry scoring (very negative funding → crowded shorts):
  - Funding extremity past threshold     → +0.30
  - RSI < 50 (price depressed)           → +0.20
  - Volume ratio >= 1.0                  → +0.15
  - 1h trend not opposing (not BULLISH)  → +0.15
  - No per-symbol cooldown               → +0.10
  - Volume ratio >= 1.5 (crowded proxy)  → +0.10
  Score >= 0.50 → generate signal (confidence = score)

SHORT Entry scoring (very positive funding → crowded longs):
  - Funding extremity past threshold     → +0.30
  - RSI > 50 (price elevated)            → +0.20
  - Volume ratio >= 1.0                  → +0.15
  - 1h trend not opposing (not BEARISH)  → +0.15
  - No per-symbol cooldown               → +0.10
  - Volume ratio >= 1.5 (crowded proxy)  → +0.10

Hard blocks (ONLY cooldown-based — extreme funding IS the signal):
  - Global cooldown active (3+ consecutive losses)
  - Fresh per-symbol cooldown (loss < 10 min ago)

Exit (funding normalizes):
  - LONG exit: funding >= -fr_normalize_threshold (recovered toward zero)
  - SHORT exit: funding <= fr_normalize_threshold (recovered toward zero)
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
from strategies.trend_filter import TrendFilter
from utils.logger import get_logger

logger = get_logger(__name__)

# -- Scoring weights --
W_FUNDING_EXTREMITY = 0.30   # how far past the extreme threshold
W_PRICE_ACTION = 0.20        # RSI alignment (contrarian confirmation)
W_VOLUME = 0.15              # volume ratio >= threshold
W_TREND = 0.15               # 1h trend not opposing
W_COOLDOWN = 0.10            # no per-symbol cooldown
W_CROWDED = 0.10             # high volume as proxy for crowded positioning
SCORE_THRESHOLD = 0.50       # minimum score to generate signal

# -- Thresholds --
RSI_PERIOD = 14
MIN_VOLUME_RATIO = 1.0       # volume ratio threshold for scoring
CROWDED_VOLUME_RATIO = 1.5   # higher volume suggests crowded positioning
MIN_CANDLES = 20             # need at least 20 1h candles for volume ratio

# -- Hard-block thresholds (cooldown only — no funding block!) --
HARD_COOLDOWN_FRESHNESS = 600  # block if loss < 10 min ago


@dataclass
class FundingSignal:
    """Signal for one coin at a point in time."""
    symbol: str
    funding_rate: float | None
    funding_extreme: str | None   # "NEGATIVE", "POSITIVE", None
    rsi: float | None
    volume_ratio: float | None
    trend_1h: str                  # BULLISH, BEARISH, NEUTRAL
    price: float | None
    updated_at: float


class FundingRateStrategy(Strategy):
    """Funding rate contrarian strategy: trade against crowded positions."""

    def __init__(
        self,
        market_data: MarketData,
        trend_filter: TrendFilter,
        cooldown: CooldownTracker,
        position_tracker: PositionTracker,
        risk_config: RiskConfig,
        strategy_config: StrategyConfig | None = None,
        coins: list[str] | None = None,
        interval: str = "1h",
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
        self._interval = interval
        self._min_candle_volume_usdc = min_candle_volume_usdc
        self._signals: dict[str, FundingSignal] = {}
        self._funding_rates: dict[str, float] = {}
        self._db = db
        self._cycle_count: int = 0

        # Config from StrategyConfig (with defaults)
        if strategy_config:
            self._fr_extreme_negative = strategy_config.fr_extreme_negative
            self._fr_extreme_positive = strategy_config.fr_extreme_positive
            self._fr_normalize_threshold = strategy_config.fr_normalize_threshold
        else:
            self._fr_extreme_negative = -0.0005
            self._fr_extreme_positive = 0.0005
            self._fr_normalize_threshold = 0.0001

    def set_coins(self, coins: list[str]) -> None:
        """Update the list of coins to scan (for dynamic discovery)."""
        self._coins = coins
        logger.info("FundingRate coins updated: %d", len(coins))

    def set_funding_rates(self, rates: dict[str, float]) -> None:
        """Update cached funding rates (from main loop)."""
        self._funding_rates = rates

    def set_max_funding_rate(self, rate: float) -> None:
        """Not used by this strategy (extreme funding IS the signal), kept for interface compat."""
        pass

    def set_cycle(self, cycle: int) -> None:
        self._cycle_count = cycle

    # -- Lifecycle --

    async def start(self) -> None:
        logger.info(
            "FundingRateStrategy started — scanning %d coins on %s, "
            "extreme_neg=%.4f, extreme_pos=%.4f, normalize=%.4f",
            len(self._coins), self._interval,
            self._fr_extreme_negative, self._fr_extreme_positive,
            self._fr_normalize_threshold,
        )

    async def stop(self) -> None:
        self._signals.clear()
        logger.info("FundingRateStrategy stopped")

    # -- Update (called every tick) --

    async def update(self) -> None:
        """Scan all coins and update signals."""
        tasks = [self._scan_coin(sym) for sym in self._coins]
        await asyncio.gather(*tasks)

    async def _scan_coin(self, symbol: str) -> None:
        """Compute funding signal for one coin using 1h candles + funding rate."""
        try:
            df_1h = self._md.get_candles(symbol, self._interval)
            if df_1h is None or len(df_1h) < MIN_CANDLES:
                return

            close = df_1h["close"]
            volume = df_1h["volume"]
            price = float(close.iloc[-1])

            # Volume floor: skip illiquid coins
            if self._min_candle_volume_usdc > 0:
                volume_usdc = float(price * volume.iloc[-1])
                if volume_usdc < self._min_candle_volume_usdc:
                    return

            # RSI on 1h
            rsi_series = ta_lib.momentum.RSIIndicator(close, window=RSI_PERIOD).rsi()
            rsi = float(rsi_series.iloc[-1]) if not rsi_series.empty and pd.notna(rsi_series.iloc[-1]) else None

            # Volume ratio (current vs 20-bar SMA)
            vol_sma = float(volume.rolling(20).mean().iloc[-1]) if len(volume) >= 20 else None
            vol_ratio = float(volume.iloc[-1]) / vol_sma if vol_sma and vol_sma > 0 else None

            # 1h trend from TrendFilter
            trend_state = self._trend.get_state(symbol)
            trend_1h = trend_state.get("trend", "NEUTRAL")

            # Funding rate classification
            funding_rate = self._funding_rates.get(symbol)
            funding_extreme = self._classify_funding(funding_rate)

            self._signals[symbol] = FundingSignal(
                symbol=symbol,
                funding_rate=funding_rate,
                funding_extreme=funding_extreme,
                rsi=round(rsi, 2) if rsi is not None else None,
                volume_ratio=round(vol_ratio, 2) if vol_ratio else None,
                trend_1h=trend_1h,
                price=price,
                updated_at=time.time(),
            )
        except Exception:
            logger.exception("Error scanning %s for funding rate", symbol)

    def _classify_funding(self, funding_rate: float | None) -> str | None:
        """Classify funding rate as NEGATIVE (extreme short), POSITIVE (extreme long), or None."""
        if funding_rate is None:
            return None
        if funding_rate <= self._fr_extreme_negative:
            return "NEGATIVE"
        if funding_rate >= self._fr_extreme_positive:
            return "POSITIVE"
        return None

    # -- Decision generation --

    async def generate_decisions(self) -> list[Decision]:
        """Generate BUY/SHORT/CLOSE decisions based on funding rate extremes."""
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

            if pos.get("strategy") != "funding_rate":
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

            # Check BUY first (negative funding → contrarian long)
            entry = self._check_long_entry(symbol, sig)
            if entry:
                decisions.append(entry)
                await self._log_signal(entry, sig)
                continue  # one direction per coin per cycle

            # Check SHORT (positive funding → contrarian short)
            entry = self._check_short_entry(symbol, sig)
            if entry:
                decisions.append(entry)
                await self._log_signal(entry, sig)

        entries = sum(1 for d in decisions if d.action in ("BUY", "SHORT"))
        logger.info(
            "[FR SCAN] coins=%d, scanned=%d, signals_generated=%d",
            len(self._coins), scanned, entries,
        )
        return decisions

    def _check_long_entry(self, symbol: str, sig: FundingSignal) -> Decision | None:
        """Score-based LONG entry: very negative funding → crowded shorts → contrarian BUY."""
        if sig.price is None or sig.funding_rate is None:
            return None

        # Must have extreme negative funding to even consider
        if sig.funding_extreme != "NEGATIVE":
            return None

        # -- Only cooldown blocks -- funding extreme IS our signal --
        if self._cooldown.is_global_cooldown_active():
            logger.debug("[FR LONG] %s -> HARD BLOCK: global cooldown", symbol)
            return None

        can_buy, _ = self._cooldown.can_buy(symbol)
        if not can_buy:
            remaining = self._cooldown.get_symbol_cooldown_remaining(symbol)
            time_since = self._rc.symbol_cooldown_sec - remaining
            if time_since < HARD_COOLDOWN_FRESHNESS:
                logger.debug("[FR LONG] %s -> HARD BLOCK: fresh cooldown (%ds ago)", symbol, int(time_since))
                return None

        # -- Scoring --
        score = 0.0
        components: list[str] = []

        # Funding extremity: how far past the negative threshold
        # More extreme = higher score (capped at 1.0)
        extremity = min(1.0, abs(sig.funding_rate - self._fr_extreme_negative) / abs(self._fr_extreme_negative))
        score += W_FUNDING_EXTREMITY * extremity
        components.append(f"fund={sig.funding_rate:.5f}(ext={extremity:.2f})")

        # Price action: RSI < 50 means price is depressed (shorts winning → good contrarian entry)
        if sig.rsi is not None and sig.rsi < 50:
            intensity = min(1.0, (50 - sig.rsi) / 50)
            score += W_PRICE_ACTION * intensity
            components.append(f"RSI={sig.rsi:.1f}<50(i={intensity:.2f})")

        # Volume ratio >= 1.0
        if sig.volume_ratio is not None and sig.volume_ratio >= MIN_VOLUME_RATIO:
            score += W_VOLUME
            components.append(f"vol={sig.volume_ratio:.1f}")

        # Trend: not opposing (BULLISH blocks contrarian LONG? No — for contrarian,
        # BULLISH trend actually confirms the contrarian bet. BEARISH trend is fine too
        # since we're betting on reversal. Only skip if no data available.)
        # Actually: for contrarian LONG against shorts, BEARISH trend means shorts
        # are aligned with trend — riskier. NEUTRAL/BULLISH is better.
        if sig.trend_1h != "BEARISH":
            score += W_TREND
            components.append(f"trend={sig.trend_1h}")

        # No cooldown
        if can_buy:
            score += W_COOLDOWN
            components.append("no_cd")

        # Crowded proxy: high volume suggests crowded positioning
        if sig.volume_ratio is not None and sig.volume_ratio >= CROWDED_VOLUME_RATIO:
            score += W_CROWDED
            components.append(f"crowded_vol={sig.volume_ratio:.1f}")

        score = round(score, 4)

        if score < SCORE_THRESHOLD:
            logger.debug(
                "[FR LONG] %s: score=%.3f < %.2f — fund=%.5f, RSI=%s, vol=%s",
                symbol, score, SCORE_THRESHOLD, sig.funding_rate,
                f"{sig.rsi:.1f}" if sig.rsi else "N/A",
                f"{sig.volume_ratio:.1f}" if sig.volume_ratio else "N/A",
            )
            return None

        vol_str = f"{sig.volume_ratio:.1f}" if sig.volume_ratio is not None else "N/A"
        rsi_str = f"{sig.rsi:.1f}" if sig.rsi is not None else "N/A"

        logger.debug("[FR LONG] %s: score=%.3f — %s", symbol, score, ", ".join(components))
        return Decision(
            action="BUY",
            symbol=symbol,
            confidence=score,
            reasoning=(
                f"[FundingRate LONG] {symbol}: score={score:.3f} — "
                f"funding={sig.funding_rate:.5f}, RSI={rsi_str}, "
                f"vol={vol_str}, trend={sig.trend_1h}"
            ),
            strategy_type="funding_rate",
            order_type="MARKET",
        )

    def _check_short_entry(self, symbol: str, sig: FundingSignal) -> Decision | None:
        """Score-based SHORT entry: very positive funding → crowded longs → contrarian SHORT."""
        if sig.price is None or sig.funding_rate is None:
            return None

        # Must have extreme positive funding to even consider
        if sig.funding_extreme != "POSITIVE":
            return None

        # -- Only cooldown blocks -- funding extreme IS our signal --
        if self._cooldown.is_global_cooldown_active():
            logger.debug("[FR SHORT] %s -> HARD BLOCK: global cooldown", symbol)
            return None

        can_buy, _ = self._cooldown.can_buy(symbol)
        if not can_buy:
            remaining = self._cooldown.get_symbol_cooldown_remaining(symbol)
            time_since = self._rc.symbol_cooldown_sec - remaining
            if time_since < HARD_COOLDOWN_FRESHNESS:
                logger.debug("[FR SHORT] %s -> HARD BLOCK: fresh cooldown (%ds ago)", symbol, int(time_since))
                return None

        # -- Scoring --
        score = 0.0
        components: list[str] = []

        # Funding extremity: how far past the positive threshold
        extremity = min(1.0, abs(sig.funding_rate - self._fr_extreme_positive) / abs(self._fr_extreme_positive))
        score += W_FUNDING_EXTREMITY * extremity
        components.append(f"fund={sig.funding_rate:.5f}(ext={extremity:.2f})")

        # Price action: RSI > 50 means price is elevated (longs winning → good contrarian short)
        if sig.rsi is not None and sig.rsi > 50:
            intensity = min(1.0, (sig.rsi - 50) / 50)
            score += W_PRICE_ACTION * intensity
            components.append(f"RSI={sig.rsi:.1f}>50(i={intensity:.2f})")

        # Volume ratio >= 1.0
        if sig.volume_ratio is not None and sig.volume_ratio >= MIN_VOLUME_RATIO:
            score += W_VOLUME
            components.append(f"vol={sig.volume_ratio:.1f}")

        # Trend: not opposing — for contrarian SHORT, BULLISH trend means longs are
        # aligned with trend (riskier). NEUTRAL/BEARISH is better for shorting.
        if sig.trend_1h != "BULLISH":
            score += W_TREND
            components.append(f"trend={sig.trend_1h}")

        # No cooldown
        if can_buy:
            score += W_COOLDOWN
            components.append("no_cd")

        # Crowded proxy: high volume suggests crowded positioning
        if sig.volume_ratio is not None and sig.volume_ratio >= CROWDED_VOLUME_RATIO:
            score += W_CROWDED
            components.append(f"crowded_vol={sig.volume_ratio:.1f}")

        score = round(score, 4)

        if score < SCORE_THRESHOLD:
            logger.debug(
                "[FR SHORT] %s: score=%.3f < %.2f — fund=%.5f, RSI=%s, vol=%s",
                symbol, score, SCORE_THRESHOLD, sig.funding_rate,
                f"{sig.rsi:.1f}" if sig.rsi else "N/A",
                f"{sig.volume_ratio:.1f}" if sig.volume_ratio else "N/A",
            )
            return None

        vol_str = f"{sig.volume_ratio:.1f}" if sig.volume_ratio is not None else "N/A"
        rsi_str = f"{sig.rsi:.1f}" if sig.rsi is not None else "N/A"

        logger.debug("[FR SHORT] %s: score=%.3f — %s", symbol, score, ", ".join(components))
        return Decision(
            action="SHORT",
            symbol=symbol,
            confidence=score,
            reasoning=(
                f"[FundingRate SHORT] {symbol}: score={score:.3f} — "
                f"funding={sig.funding_rate:.5f}, RSI={rsi_str}, "
                f"vol={vol_str}, trend={sig.trend_1h}"
            ),
            strategy_type="funding_rate",
            order_type="MARKET",
        )

    def _check_long_exit(self, symbol: str, sig: FundingSignal) -> Decision | None:
        """Exit LONG when funding normalizes (recovers toward zero)."""
        if sig.funding_rate is None:
            return None

        # LONG exit: funding >= -fr_normalize_threshold (no longer extremely negative)
        if sig.funding_rate >= -self._fr_normalize_threshold:
            return Decision(
                action="CLOSE",
                symbol=symbol,
                confidence=0.8,
                reasoning=(
                    f"[FundingRate LONG EXIT] {symbol}: funding normalized "
                    f"({sig.funding_rate:.5f} >= {-self._fr_normalize_threshold:.5f})"
                ),
                strategy_type="funding_rate",
                order_type="MARKET",
            )
        return None

    def _check_short_exit(self, symbol: str, sig: FundingSignal) -> Decision | None:
        """Exit SHORT when funding normalizes (recovers toward zero)."""
        if sig.funding_rate is None:
            return None

        # SHORT exit: funding <= fr_normalize_threshold (no longer extremely positive)
        if sig.funding_rate <= self._fr_normalize_threshold:
            return Decision(
                action="CLOSE",
                symbol=symbol,
                confidence=0.8,
                reasoning=(
                    f"[FundingRate SHORT EXIT] {symbol}: funding normalized "
                    f"({sig.funding_rate:.5f} <= {self._fr_normalize_threshold:.5f})"
                ),
                strategy_type="funding_rate",
                order_type="MARKET",
            )
        return None

    async def _log_signal(self, decision: Decision, sig: FundingSignal) -> None:
        """Log a SIGNAL event to the database."""
        if not self._db or self._cycle_count <= 0:
            return
        try:
            await self._db.insert_event(
                cycle=self._cycle_count,
                symbol=decision.symbol or "",
                event_type="SIGNAL",
                source="funding_rate",
                action=decision.action,
                confidence=decision.confidence,
                reasoning=decision.reasoning,
                details={
                    "score": decision.confidence,
                    "funding_rate": sig.funding_rate,
                    "funding_extreme": sig.funding_extreme,
                    "rsi": sig.rsi,
                    "volume_ratio": sig.volume_ratio,
                    "trend_1h": sig.trend_1h,
                },
            )
        except Exception:
            logger.debug("Failed to log SIGNAL event", exc_info=True)

    # -- State (for snapshot / dashboard) --

    def get_state(self) -> dict[str, Any]:
        """Return signal map for the AI market snapshot."""
        signals: dict[str, Any] = {}
        extreme_negative: list[str] = []
        extreme_positive: list[str] = []

        for sym, sig in self._signals.items():
            signals[sym] = {
                "funding_rate": sig.funding_rate,
                "funding_extreme": sig.funding_extreme,
                "rsi": sig.rsi,
                "volume_ratio": sig.volume_ratio,
                "trend_1h": sig.trend_1h,
                "price": sig.price,
            }
            if sig.funding_extreme == "NEGATIVE":
                extreme_negative.append(sym)
            elif sig.funding_extreme == "POSITIVE":
                extreme_positive.append(sym)

        return {
            "strategy": "funding_rate",
            "interval": self._interval,
            "coins_scanned": len(self._signals),
            "extreme_negative": extreme_negative,
            "extreme_positive": extreme_positive,
            "thresholds": {
                "fr_extreme_negative": self._fr_extreme_negative,
                "fr_extreme_positive": self._fr_extreme_positive,
                "fr_normalize_threshold": self._fr_normalize_threshold,
            },
            "signals": signals,
            "cooldown": self._cooldown.get_state(),
            "trend_filter": self._trend.get_all_states(),
        }
