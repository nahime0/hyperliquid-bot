"""BB Squeeze — scoring-based decision generator.

Detects Bollinger Band squeezes (low bandwidth) followed by breakouts.
When bandwidth contracts to the lowest percentile of recent history (squeeze),
the strategy watches for price to break above/below the bands (breakout).

LONG Entry scoring (breakout UP after squeeze):
  - Squeeze tightness (tight bandwidth)    -> +0.25
  - Breakout strength (price > upper BB)   -> +0.25
  - Volume ratio >= min_volume_spike       -> +0.20
  - 1h trend BULLISH (TrendFilter)         -> +0.10
  - |funding| < max_funding_rate           -> +0.10
  - No per-symbol cooldown                 -> +0.10
  Score >= 0.50 -> generate signal (confidence = score)

SHORT Entry scoring (breakout DOWN after squeeze):
  - Squeeze tightness (tight bandwidth)    -> +0.25
  - Breakout strength (price < lower BB)   -> +0.25
  - Volume ratio >= min_volume_spike       -> +0.20
  - 1h trend BEARISH (TrendFilter)         -> +0.10
  - |funding| < max_funding_rate           -> +0.10
  - No per-symbol cooldown                 -> +0.10

Hard blocks (always reject, bypass scoring):
  - |funding| >= 0.001 (extreme funding)
  - Global cooldown active (3+ consecutive losses)
  - Fresh per-symbol cooldown (loss < 10 min ago)

Exit:
  - LONG exit: price < BB midline
  - SHORT exit: price > BB midline
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
W_SQUEEZE = 0.25       # squeeze tightness (tighter = higher)
W_BREAKOUT = 0.25      # breakout strength (farther past band = higher)
W_VOLUME = 0.20        # volume spike on breakout
W_TREND = 0.10         # 1h trend alignment
W_FUNDING = 0.10       # funding rate acceptable
W_COOLDOWN = 0.10      # no per-symbol cooldown
SCORE_THRESHOLD = 0.60  # minimum score to generate signal

# -- BB params --
BB_PERIOD = 20
BB_STD = 2.0


@dataclass
class BbsSignal:
    """Signal for one coin at a point in time."""
    symbol: str
    in_squeeze: bool
    was_in_squeeze: bool       # was in squeeze recently (last check)
    breakout_dir: str | None   # "UP", "DOWN", None
    squeeze_tightness: float | None  # 0-1, lower bandwidth = tighter
    breakout_strength: float | None  # how far past the band (%)
    volume_ratio: float | None
    trend_1h: str
    price: float | None
    bb_upper: float | None
    bb_lower: float | None
    bb_mid: float | None
    updated_at: float


class BbSqueezeStrategy(Strategy):
    """BB Squeeze breakout strategy: detects squeezes then trades the breakout."""

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
        self._signals: dict[str, BbsSignal] = {}
        self._funding_rates: dict[str, float] = {}
        self._max_funding_rate: float = 0.0005
        self._db = db
        self._cycle_count: int = 0
        # Stateful: track which coins were in squeeze on the previous scan
        self._squeeze_history: dict[str, bool] = {}

    @property
    def strategy_type(self) -> str:
        return "bb_squeeze"

    def set_coins(self, coins: list[str]) -> None:
        self._coins = coins
        logger.info("BbSqueeze coins updated: %d", len(coins))

    def set_funding_rates(self, rates: dict[str, float]) -> None:
        self._funding_rates = rates

    def set_max_funding_rate(self, rate: float) -> None:
        self._max_funding_rate = rate

    def set_cycle(self, cycle: int) -> None:
        self._cycle_count = cycle

    # -- Lifecycle --

    async def start(self) -> None:
        logger.info(
            "BbSqueezeStrategy started — scanning %d coins on %s",
            len(self._coins), self._interval,
        )

    async def stop(self) -> None:
        self._signals.clear()
        self._squeeze_history.clear()
        logger.info("BbSqueezeStrategy stopped")

    # -- Update (called every tick) --

    async def update(self) -> None:
        """Scan all coins and update signals."""
        tasks = [self._scan_coin(sym) for sym in self._coins]
        await asyncio.gather(*tasks)

    async def _scan_coin(self, symbol: str) -> None:
        """Compute BB squeeze/breakout indicators for one coin."""
        try:
            df = self._md.get_candles(symbol, self._interval)
            if df is None or len(df) < self._sc.bbs_lookback_bars:
                return

            close = df["close"]
            volume = df["volume"]
            price = float(close.iloc[-1])

            # Volume floor: skip illiquid coins
            if self._sc.min_candle_volume_usdc > 0:
                volume_usdc = float(price * volume.iloc[-1])
                if volume_usdc < self._sc.min_candle_volume_usdc:
                    return

            # Bollinger Bands
            bb = ta_lib.volatility.BollingerBands(close, window=BB_PERIOD, window_dev=BB_STD)
            bb_lower_series = bb.bollinger_lband()
            bb_upper_series = bb.bollinger_hband()
            bb_mid_series = bb.bollinger_mavg()

            bb_lower = float(bb_lower_series.iloc[-1])
            bb_upper = float(bb_upper_series.iloc[-1])
            bb_mid = float(bb_mid_series.iloc[-1])

            if bb_mid <= 0:
                return

            # Bandwidth for all bars in lookback window
            lookback = self._sc.bbs_lookback_bars
            bandwidth_series = (bb_upper_series - bb_lower_series) / bb_mid_series
            # Take the last N bars of bandwidth
            bw_window = bandwidth_series.iloc[-lookback:]
            bw_window = bw_window.dropna()
            if len(bw_window) < lookback * 0.5:
                return  # not enough data

            current_bw = float(bandwidth_series.iloc[-1])
            if pd.isna(current_bw):
                return

            # Percentile of current bandwidth within the lookback window
            bw_percentile = float((bw_window < current_bw).sum() / len(bw_window) * 100)
            in_squeeze = bw_percentile <= self._sc.bbs_squeeze_percentile

            # squeeze_tightness: 1.0 = extremely tight, 0.0 = wide
            squeeze_tightness = 1.0 - (bw_percentile / 100)

            # Was this coin in squeeze on the previous scan?
            was_in_squeeze = self._squeeze_history.get(symbol, False)

            # Detect breakout direction
            breakout_dir: str | None = None
            breakout_strength: float | None = None

            if was_in_squeeze:
                if price > bb_upper and bb_upper > 0:
                    breakout_dir = "UP"
                    breakout_strength = (price - bb_upper) / bb_upper * 100
                elif price < bb_lower and bb_lower > 0:
                    breakout_dir = "DOWN"
                    breakout_strength = (bb_lower - price) / bb_lower * 100

            # Update squeeze history for next scan
            self._squeeze_history[symbol] = in_squeeze

            # Volume ratio vs 20-bar SMA
            vol_sma = float(volume.rolling(20).mean().iloc[-1]) if len(volume) >= 20 else None
            vol_ratio = float(volume.iloc[-1]) / vol_sma if vol_sma and vol_sma > 0 else None

            # 1h trend from TrendFilter
            trend_state = self._trend.get_state(symbol)
            trend_1h = trend_state.get("trend", "NEUTRAL")

            self._signals[symbol] = BbsSignal(
                symbol=symbol,
                in_squeeze=in_squeeze,
                was_in_squeeze=was_in_squeeze,
                breakout_dir=breakout_dir,
                squeeze_tightness=round(squeeze_tightness, 4),
                breakout_strength=round(breakout_strength, 4) if breakout_strength is not None else None,
                volume_ratio=round(vol_ratio, 2) if vol_ratio else None,
                trend_1h=trend_1h,
                price=price,
                bb_upper=bb_upper,
                bb_lower=bb_lower,
                bb_mid=bb_mid,
                updated_at=time.time(),
            )
        except Exception:
            logger.exception("Error scanning %s for BB squeeze", symbol)

    # -- Decision generation --

    async def generate_decisions(self) -> list[Decision]:
        """Generate BUY/SHORT/CLOSE decisions based on squeeze breakouts."""
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

            if pos.get("strategy") != "bb_squeeze":
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

            # Check LONG entry (breakout UP)
            entry = self._check_long_entry(symbol, sig)
            if entry:
                decisions.append(entry)
                await self._log_signal(entry, sig)
                continue  # one direction per coin per cycle

            # Check SHORT entry (breakout DOWN)
            entry = self._check_short_entry(symbol, sig)
            if entry:
                decisions.append(entry)
                await self._log_signal(entry, sig)

        entries = sum(1 for d in decisions if d.action in ("BUY", "SHORT"))
        logger.info(
            "[BBS SCAN] coins=%d, scanned=%d, signals_generated=%d",
            len(self._coins), scanned, entries,
        )
        return decisions

    def _check_long_entry(self, symbol: str, sig: BbsSignal) -> Decision | None:
        """Score-based LONG entry: breakout UP after squeeze."""
        if sig.price is None or sig.breakout_dir != "UP":
            return None

        # Hard blocks
        blocked, can_buy, abs_funding = check_hard_blocks(
            symbol,
            funding_rates=self._funding_rates,
            cooldown=self._cooldown,
            risk_config=self._rc,
        )
        if blocked:
            logger.debug("[BBS LONG] %s -> HARD BLOCK", symbol)
            return None

        # Trend hard block: BEARISH blocks LONG
        if sig.trend_1h == "BEARISH":
            logger.debug("[BBS LONG] %s -> HARD BLOCK: trend is BEARISH", symbol)
            return None

        # Scoring
        score = 0.0
        components: list[str] = []

        # Squeeze tightness (graduated: tighter = higher)
        if sig.squeeze_tightness is not None and sig.squeeze_tightness > 0:
            intensity = min(1.0, sig.squeeze_tightness)
            score += W_SQUEEZE * intensity
            components.append(f"squeeze={sig.squeeze_tightness:.2f}(i={intensity:.2f})")

        # Breakout strength (graduated: farther past band = higher, cap at 2%)
        if sig.breakout_strength is not None and sig.breakout_strength > 0:
            intensity = min(1.0, sig.breakout_strength / 2.0)
            score += W_BREAKOUT * intensity
            components.append(f"breakout={sig.breakout_strength:.3f}%(i={intensity:.2f})")

        # Volume spike
        if sig.volume_ratio is not None and sig.volume_ratio >= self._sc.bbs_min_volume_spike:
            score += W_VOLUME
            components.append(f"vol={sig.volume_ratio:.1f}")

        # Trend alignment
        if sig.trend_1h == "BULLISH":
            score += W_TREND
            components.append("trend=BULL")

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
                "[BBS LONG] %s: score=%.3f < %.2f — squeeze=%.2f, breakout=%.3f%%, vol=%s",
                symbol, score, SCORE_THRESHOLD,
                sig.squeeze_tightness or 0,
                sig.breakout_strength or 0,
                f"{sig.volume_ratio:.1f}" if sig.volume_ratio else "N/A",
            )
            return None

        vol_str = f"{sig.volume_ratio:.1f}" if sig.volume_ratio is not None else "N/A"

        logger.debug("[BBS LONG] %s: score=%.3f — %s", symbol, score, ", ".join(components))
        return Decision(
            action="BUY",
            symbol=symbol,
            confidence=score,
            reasoning=(
                f"[BbSqueeze LONG] {symbol}: score={score:.3f} — "
                f"squeeze={sig.squeeze_tightness:.2f}, breakout={sig.breakout_strength:.3f}%, "
                f"vol={vol_str}, trend={sig.trend_1h}"
            ),
            strategy_type="bb_squeeze",
            order_type="MARKET",
        )

    def _check_short_entry(self, symbol: str, sig: BbsSignal) -> Decision | None:
        """Score-based SHORT entry: breakout DOWN after squeeze."""
        if sig.price is None or sig.breakout_dir != "DOWN":
            return None

        # Hard blocks
        blocked, can_buy, abs_funding = check_hard_blocks(
            symbol,
            funding_rates=self._funding_rates,
            cooldown=self._cooldown,
            risk_config=self._rc,
        )
        if blocked:
            logger.debug("[BBS SHORT] %s -> HARD BLOCK", symbol)
            return None

        # Trend hard block: BULLISH blocks SHORT
        if sig.trend_1h == "BULLISH":
            logger.debug("[BBS SHORT] %s -> HARD BLOCK: trend is BULLISH", symbol)
            return None

        # Scoring
        score = 0.0
        components: list[str] = []

        # Squeeze tightness (graduated: tighter = higher)
        if sig.squeeze_tightness is not None and sig.squeeze_tightness > 0:
            intensity = min(1.0, sig.squeeze_tightness)
            score += W_SQUEEZE * intensity
            components.append(f"squeeze={sig.squeeze_tightness:.2f}(i={intensity:.2f})")

        # Breakout strength (graduated: farther past band = higher, cap at 2%)
        if sig.breakout_strength is not None and sig.breakout_strength > 0:
            intensity = min(1.0, sig.breakout_strength / 2.0)
            score += W_BREAKOUT * intensity
            components.append(f"breakout={sig.breakout_strength:.3f}%(i={intensity:.2f})")

        # Volume spike
        if sig.volume_ratio is not None and sig.volume_ratio >= self._sc.bbs_min_volume_spike:
            score += W_VOLUME
            components.append(f"vol={sig.volume_ratio:.1f}")

        # Trend alignment
        if sig.trend_1h == "BEARISH":
            score += W_TREND
            components.append("trend=BEAR")

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
                "[BBS SHORT] %s: score=%.3f < %.2f — squeeze=%.2f, breakout=%.3f%%, vol=%s",
                symbol, score, SCORE_THRESHOLD,
                sig.squeeze_tightness or 0,
                sig.breakout_strength or 0,
                f"{sig.volume_ratio:.1f}" if sig.volume_ratio else "N/A",
            )
            return None

        vol_str = f"{sig.volume_ratio:.1f}" if sig.volume_ratio is not None else "N/A"

        logger.debug("[BBS SHORT] %s: score=%.3f — %s", symbol, score, ", ".join(components))
        return Decision(
            action="SHORT",
            symbol=symbol,
            confidence=score,
            reasoning=(
                f"[BbSqueeze SHORT] {symbol}: score={score:.3f} — "
                f"squeeze={sig.squeeze_tightness:.2f}, breakout={sig.breakout_strength:.3f}%, "
                f"vol={vol_str}, trend={sig.trend_1h}"
            ),
            strategy_type="bb_squeeze",
            order_type="MARKET",
        )

    def _check_long_exit(self, symbol: str, sig: BbsSignal) -> Decision | None:
        """Exit LONG if price drops below BB midline."""
        if sig.price is None or sig.bb_mid is None:
            return None

        if sig.price < sig.bb_mid:
            return Decision(
                action="CLOSE",
                symbol=symbol,
                confidence=0.8,
                reasoning=f"[BbSqueeze LONG EXIT] {symbol}: price={sig.price:.4f} < bb_mid={sig.bb_mid:.4f}",
                strategy_type="bb_squeeze",
                order_type="MARKET",
            )
        return None

    def _check_short_exit(self, symbol: str, sig: BbsSignal) -> Decision | None:
        """Exit SHORT if price rises above BB midline."""
        if sig.price is None or sig.bb_mid is None:
            return None

        if sig.price > sig.bb_mid:
            return Decision(
                action="CLOSE",
                symbol=symbol,
                confidence=0.8,
                reasoning=f"[BbSqueeze SHORT EXIT] {symbol}: price={sig.price:.4f} > bb_mid={sig.bb_mid:.4f}",
                strategy_type="bb_squeeze",
                order_type="MARKET",
            )
        return None

    async def _log_signal(self, decision: Decision, sig: BbsSignal) -> None:
        """Log a SIGNAL event to the database."""
        if not self._db or self._cycle_count <= 0:
            return
        try:
            await self._db.insert_event(
                cycle=self._cycle_count,
                symbol=decision.symbol or "",
                event_type="SIGNAL",
                source="bb_squeeze",
                action=decision.action,
                confidence=decision.confidence,
                reasoning=decision.reasoning,
                details={
                    "score": decision.confidence,
                    "squeeze_tightness": sig.squeeze_tightness,
                    "breakout_dir": sig.breakout_dir,
                    "breakout_strength": sig.breakout_strength,
                    "volume_ratio": sig.volume_ratio,
                    "trend_1h": sig.trend_1h,
                    "bb_upper": sig.bb_upper,
                    "bb_lower": sig.bb_lower,
                    "bb_mid": sig.bb_mid,
                },
            )
        except Exception:
            logger.debug("Failed to log SIGNAL event", exc_info=True)

    # -- State (for snapshot / dashboard) --

    def get_state(self) -> dict[str, Any]:
        """Return signal map for the AI market snapshot."""
        signals: dict[str, Any] = {}
        squeeze_coins: list[str] = []
        breakout_up: list[str] = []
        breakout_down: list[str] = []

        for sym, sig in self._signals.items():
            signals[sym] = {
                "in_squeeze": sig.in_squeeze,
                "was_in_squeeze": sig.was_in_squeeze,
                "breakout_dir": sig.breakout_dir,
                "squeeze_tightness": sig.squeeze_tightness,
                "breakout_strength": sig.breakout_strength,
                "volume_ratio": sig.volume_ratio,
                "trend_1h": sig.trend_1h,
                "price": sig.price,
                "bb_upper": sig.bb_upper,
                "bb_lower": sig.bb_lower,
                "bb_mid": sig.bb_mid,
            }
            if sig.in_squeeze:
                squeeze_coins.append(sym)
            if sig.breakout_dir == "UP":
                breakout_up.append(sym)
            elif sig.breakout_dir == "DOWN":
                breakout_down.append(sym)

        return {
            "strategy": "bb_squeeze",
            "interval": self._interval,
            "coins_scanned": len(self._signals),
            "in_squeeze": squeeze_coins,
            "breakout_up": breakout_up,
            "breakout_down": breakout_down,
            "signals": signals,
            "cooldown": self._cooldown.get_state(),
            "trend_filter": self._trend.get_all_states(),
        }
