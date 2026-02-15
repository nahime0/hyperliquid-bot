"""BTC Correlation Lag — scoring-based decision generator.

Generates BUY/SHORT decisions when BTC makes a significant 1h move and an
altcoin lags behind, betting on the altcoin catching up to BTC's move.

BUY Entry (BTC up, alt lagging):
  - BTC 1h change >= btc_min_move_pct (default 1%)
  - Lag (btc_change - alt_change) >= btc_min_lag_pct (default 0.5%)

SHORT Entry (BTC down, alt lagging):
  - BTC 1h change <= -btc_min_move_pct
  - Lag <= -btc_min_lag_pct (alt hasn't dropped as much as BTC yet)

Entry scoring:
  - abs(btc_change) normalized          -> +0.30
  - abs(lag) normalized                 -> +0.25
  - Volume ratio >= 1.0                -> +0.15
  - |funding| < max_funding_rate       -> +0.10
  - 1h trend alignment                 -> +0.10
  - No per-symbol cooldown             -> +0.10
  Score >= 0.50 -> generate signal (confidence = score)

Hard blocks (always reject, bypass scoring):
  - |funding| >= 0.001 (extreme funding)
  - Global cooldown active (3+ consecutive losses)
  - Fresh per-symbol cooldown (loss < 10 min ago)

Exit (correlation gap closes):
  - LONG exit: lag < btc_catch_up_pct (alt caught up)
  - SHORT exit: lag > -btc_catch_up_pct (alt caught down)

BTC itself is never traded by this strategy.
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
W_BTC_MOVE = 0.30        # BTC 1h move magnitude
W_ALT_LAG = 0.25         # altcoin lag behind BTC
W_VOLUME = 0.15          # volume ratio >= threshold
W_FUNDING = 0.10         # funding rate acceptable
W_TREND = 0.10           # 1h trend alignment
W_COOLDOWN = 0.10        # no per-symbol cooldown
SCORE_THRESHOLD = 0.60   # minimum score to generate signal

# -- Thresholds (defaults, overridden from StrategyConfig) --
BTC_MIN_MOVE_PCT = 1.0   # BTC must move at least 1% in 1h
BTC_MIN_LAG_PCT = 0.5    # altcoin must lag at least 0.5%
BTC_CATCH_UP_PCT = 0.2   # exit when lag narrows to this

MIN_VOLUME_RATIO = 1.0   # volume ratio threshold
MIN_CANDLES = 3           # minimum 1h candles needed


@dataclass
class BtcCorrSignal:
    """Signal for one coin at a point in time."""
    symbol: str
    btc_change_1h: float | None
    alt_change_1h: float | None
    lag: float | None           # btc_change - alt_change
    volume_ratio: float | None
    trend_1h: str
    price: float | None
    updated_at: float


class BtcCorrelationStrategy(Strategy):
    """BTC Correlation Lag strategy: trades altcoin catch-up after BTC moves."""

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
        btc_min_move_pct: float = BTC_MIN_MOVE_PCT,
        btc_min_lag_pct: float = BTC_MIN_LAG_PCT,
        btc_catch_up_pct: float = BTC_CATCH_UP_PCT,
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
        self._btc_min_move_pct = btc_min_move_pct
        self._btc_min_lag_pct = btc_min_lag_pct
        self._btc_catch_up_pct = btc_catch_up_pct
        self._signals: dict[str, BtcCorrSignal] = {}
        self._funding_rates: dict[str, float] = {}
        self._max_funding_rate: float = 0.0005
        self._db = db
        self._cycle_count: int = 0
        self._btc_change_1h: float | None = None  # cached per cycle

    def set_coins(self, coins: list[str]) -> None:
        self._coins = coins
        logger.info("BtcCorrelation coins updated: %d", len(coins))

    def set_funding_rates(self, rates: dict[str, float]) -> None:
        self._funding_rates = rates

    def set_max_funding_rate(self, rate: float) -> None:
        self._max_funding_rate = rate

    def set_cycle(self, cycle: int) -> None:
        self._cycle_count = cycle

    # -- Lifecycle --

    async def start(self) -> None:
        logger.info(
            "BtcCorrelationStrategy started — scanning %d coins on %s "
            "(btc_min_move=%.1f%%, min_lag=%.1f%%, catch_up=%.1f%%)",
            len(self._coins), self._interval,
            self._btc_min_move_pct, self._btc_min_lag_pct, self._btc_catch_up_pct,
        )

    async def stop(self) -> None:
        self._signals.clear()
        self._btc_change_1h = None
        logger.info("BtcCorrelationStrategy stopped")

    # -- Update (called every tick) --

    async def update(self) -> None:
        # Compute BTC 1h change first
        self._btc_change_1h = self._compute_btc_change()
        if self._btc_change_1h is None:
            return
        tasks = [self._scan_coin(sym) for sym in self._coins if sym != "BTC"]
        await asyncio.gather(*tasks)

    def _compute_btc_change(self) -> float | None:
        """Compute BTC 1h percent change from its 1h candles."""
        df = self._md.get_candles("BTC", self._interval)
        if df is None or len(df) < MIN_CANDLES:
            logger.debug("[BTC_CORR] Not enough BTC candles (%s)", self._interval)
            return None
        close = df["close"]
        btc_prev = float(close.iloc[-2])
        btc_now = float(close.iloc[-1])
        if btc_prev <= 0:
            return None
        return (btc_now - btc_prev) / btc_prev * 100

    async def _scan_coin(self, symbol: str) -> None:
        """Compute BTC correlation lag signal for one altcoin."""
        try:
            df = self._md.get_candles(symbol, self._interval)
            if df is None or len(df) < MIN_CANDLES:
                return

            close = df["close"]
            volume = df["volume"]
            price = float(close.iloc[-1])

            # Volume floor
            if self._min_candle_volume_usdc > 0:
                volume_usdc = float(price * volume.iloc[-1])
                if volume_usdc < self._min_candle_volume_usdc:
                    return

            # Alt 1h change: close[-1] vs close[-2]
            alt_prev = float(close.iloc[-2])
            if alt_prev <= 0:
                return
            alt_change = (price - alt_prev) / alt_prev * 100

            # Lag = btc_change - alt_change
            lag = self._btc_change_1h - alt_change if self._btc_change_1h is not None else None

            # 1h trend from TrendFilter
            trend_state = self._trend.get_state(symbol)
            trend_1h = trend_state.get("trend", "NEUTRAL")

            # Volume ratio
            vol_sma = float(volume.rolling(20).mean().iloc[-1]) if len(volume) >= 20 else None
            vol_ratio = float(volume.iloc[-1]) / vol_sma if vol_sma and vol_sma > 0 else None

            self._signals[symbol] = BtcCorrSignal(
                symbol=symbol,
                btc_change_1h=round(self._btc_change_1h, 3) if self._btc_change_1h is not None else None,
                alt_change_1h=round(alt_change, 3),
                lag=round(lag, 3) if lag is not None else None,
                volume_ratio=round(vol_ratio, 2) if vol_ratio else None,
                trend_1h=trend_1h,
                price=price,
                updated_at=time.time(),
            )
        except Exception:
            logger.exception("Error scanning %s for BTC correlation", symbol)

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

            if pos.get("strategy") != "btc_correlation":
                continue

            direction = pos.get("direction", "LONG")
            if direction == "LONG":
                exit_d = self._check_long_exit(symbol, sig)
            else:
                exit_d = self._check_short_exit(symbol, sig)

            if exit_d:
                decisions.append(exit_d)

        # ENTRY checks (skip BTC itself)
        scanned = 0
        for symbol in self._coins:
            if symbol == "BTC":
                continue
            if symbol in open_symbols:
                continue

            sig = self._signals.get(symbol)
            if not sig:
                continue

            scanned += 1

            # Check BUY first (BTC up, alt lagging)
            entry = self._check_long_entry(symbol, sig)
            if entry:
                decisions.append(entry)
                await self._log_signal(entry, sig)
                continue

            # Check SHORT (BTC down, alt hasn't dropped yet)
            entry = self._check_short_entry(symbol, sig)
            if entry:
                decisions.append(entry)
                await self._log_signal(entry, sig)

        entries = sum(1 for d in decisions if d.action in ("BUY", "SHORT"))
        logger.info(
            "[BTC_CORR SCAN] coins=%d, scanned=%d, signals_generated=%d, btc_chg=%.2f%%",
            len(self._coins), scanned, entries,
            self._btc_change_1h if self._btc_change_1h is not None else 0.0,
        )
        return decisions

    def _check_long_entry(self, symbol: str, sig: BtcCorrSignal) -> Decision | None:
        """Score-based LONG entry: BTC up, altcoin lagging behind."""
        if sig.price is None or sig.lag is None or sig.btc_change_1h is None:
            return None

        # Direction check: BTC must be up and alt must lag
        if sig.btc_change_1h < self._btc_min_move_pct:
            return None
        if sig.lag < self._btc_min_lag_pct:
            return None

        # Hard blocks
        blocked, can_buy, abs_funding = check_hard_blocks(
            symbol,
            funding_rates=self._funding_rates,
            cooldown=self._cooldown,
            risk_config=self._rc,
        )
        if blocked:
            return None

        # Scoring
        score = 0.0
        components: list[str] = []

        # BTC move magnitude: normalize abs(btc_change) / (min_move * 3), cap 1.0
        btc_intensity = min(1.0, abs(sig.btc_change_1h) / (self._btc_min_move_pct * 3))
        score += W_BTC_MOVE * btc_intensity
        components.append(f"btc={sig.btc_change_1h:+.2f}%(i={btc_intensity:.2f})")

        # Alt lag magnitude: normalize abs(lag) / (min_lag * 3), cap 1.0
        lag_intensity = min(1.0, abs(sig.lag) / (self._btc_min_lag_pct * 3))
        score += W_ALT_LAG * lag_intensity
        components.append(f"lag={sig.lag:.2f}%(i={lag_intensity:.2f})")

        if sig.volume_ratio is not None and sig.volume_ratio >= MIN_VOLUME_RATIO:
            score += W_VOLUME
            components.append(f"vol={sig.volume_ratio:.1f}")

        if abs_funding < self._max_funding_rate:
            score += W_FUNDING
            components.append("funding_ok")

        # Trend alignment: BULLISH trend supports LONG catch-up
        if sig.trend_1h == "BULLISH":
            score += W_TREND
            components.append("trend=BULL")

        if can_buy:
            score += W_COOLDOWN
            components.append("no_cd")

        score = round(score, 4)

        if score < SCORE_THRESHOLD:
            return None

        vol_str = f"{sig.volume_ratio:.1f}" if sig.volume_ratio is not None else "N/A"

        logger.debug("[BTC_CORR LONG] %s: score=%.3f — %s", symbol, score, ", ".join(components))
        expected_move = abs(sig.lag)
        return Decision(
            action="BUY",
            symbol=symbol,
            confidence=score,
            reasoning=(
                f"[BtcCorr LONG] {symbol}: score={score:.3f} — "
                f"btc_chg={sig.btc_change_1h:+.2f}%, alt_chg={sig.alt_change_1h:+.2f}%, "
                f"lag={sig.lag:.2f}%, vol={vol_str}, trend={sig.trend_1h}"
            ),
            strategy_type="btc_correlation",
            order_type="MARKET",
            expected_move_pct=round(expected_move, 2),
        )

    def _check_short_entry(self, symbol: str, sig: BtcCorrSignal) -> Decision | None:
        """Score-based SHORT entry: BTC down, altcoin hasn't dropped yet."""
        if sig.price is None or sig.lag is None or sig.btc_change_1h is None:
            return None

        # Direction check: BTC must be down and alt must lag (lag is negative)
        if sig.btc_change_1h > -self._btc_min_move_pct:
            return None
        if sig.lag > -self._btc_min_lag_pct:
            return None

        # Hard blocks
        blocked, can_buy, abs_funding = check_hard_blocks(
            symbol,
            funding_rates=self._funding_rates,
            cooldown=self._cooldown,
            risk_config=self._rc,
        )
        if blocked:
            return None

        # Scoring
        score = 0.0
        components: list[str] = []

        # BTC move magnitude
        btc_intensity = min(1.0, abs(sig.btc_change_1h) / (self._btc_min_move_pct * 3))
        score += W_BTC_MOVE * btc_intensity
        components.append(f"btc={sig.btc_change_1h:+.2f}%(i={btc_intensity:.2f})")

        # Alt lag magnitude
        lag_intensity = min(1.0, abs(sig.lag) / (self._btc_min_lag_pct * 3))
        score += W_ALT_LAG * lag_intensity
        components.append(f"lag={sig.lag:.2f}%(i={lag_intensity:.2f})")

        if sig.volume_ratio is not None and sig.volume_ratio >= MIN_VOLUME_RATIO:
            score += W_VOLUME
            components.append(f"vol={sig.volume_ratio:.1f}")

        if abs_funding < self._max_funding_rate:
            score += W_FUNDING
            components.append("funding_ok")

        # Trend alignment: BEARISH trend supports SHORT catch-down
        if sig.trend_1h == "BEARISH":
            score += W_TREND
            components.append("trend=BEAR")

        if can_buy:
            score += W_COOLDOWN
            components.append("no_cd")

        score = round(score, 4)

        if score < SCORE_THRESHOLD:
            return None

        vol_str = f"{sig.volume_ratio:.1f}" if sig.volume_ratio is not None else "N/A"

        logger.debug("[BTC_CORR SHORT] %s: score=%.3f — %s", symbol, score, ", ".join(components))
        expected_move = abs(sig.lag)
        return Decision(
            action="SHORT",
            symbol=symbol,
            confidence=score,
            reasoning=(
                f"[BtcCorr SHORT] {symbol}: score={score:.3f} — "
                f"btc_chg={sig.btc_change_1h:+.2f}%, alt_chg={sig.alt_change_1h:+.2f}%, "
                f"lag={sig.lag:.2f}%, vol={vol_str}, trend={sig.trend_1h}"
            ),
            strategy_type="btc_correlation",
            order_type="MARKET",
            expected_move_pct=round(expected_move, 2),
        )

    def _check_long_exit(self, symbol: str, sig: BtcCorrSignal) -> Decision | None:
        """Exit LONG when the correlation gap closes (alt caught up to BTC)."""
        if sig.lag is None:
            return None

        if sig.lag >= self._btc_catch_up_pct:
            return None  # still lagging, hold

        return Decision(
            action="CLOSE",
            symbol=symbol,
            confidence=0.8,
            reasoning=(
                f"[BtcCorr LONG EXIT] {symbol}: lag={sig.lag:.2f}% < "
                f"catch_up={self._btc_catch_up_pct:.1f}% — alt caught up"
            ),
            strategy_type="btc_correlation",
            order_type="MARKET",
        )

    def _check_short_exit(self, symbol: str, sig: BtcCorrSignal) -> Decision | None:
        """Exit SHORT when the correlation gap closes (alt caught down to BTC)."""
        if sig.lag is None:
            return None

        if sig.lag <= -self._btc_catch_up_pct:
            return None  # still lagging downward, hold

        return Decision(
            action="CLOSE",
            symbol=symbol,
            confidence=0.8,
            reasoning=(
                f"[BtcCorr SHORT EXIT] {symbol}: lag={sig.lag:.2f}% > "
                f"-{self._btc_catch_up_pct:.1f}% — alt caught down"
            ),
            strategy_type="btc_correlation",
            order_type="MARKET",
        )

    async def _log_signal(self, decision: Decision, sig: BtcCorrSignal) -> None:
        if not self._db or self._cycle_count <= 0:
            return
        try:
            await self._db.insert_event(
                cycle=self._cycle_count,
                symbol=decision.symbol or "",
                event_type="SIGNAL",
                source="btc_correlation",
                action=decision.action,
                confidence=decision.confidence,
                reasoning=decision.reasoning,
                details={
                    "score": decision.confidence,
                    "btc_change_1h": sig.btc_change_1h,
                    "alt_change_1h": sig.alt_change_1h,
                    "lag": sig.lag,
                    "volume_ratio": sig.volume_ratio,
                    "trend_1h": sig.trend_1h,
                    "expected_move_pct": round(abs(sig.lag), 2) if sig.lag is not None else None,
                },
            )
        except Exception:
            logger.debug("Failed to log SIGNAL event", exc_info=True)

    # -- State (for snapshot / dashboard) --

    def get_state(self) -> dict[str, Any]:
        signals: dict[str, Any] = {}
        lagging_long: list[str] = []
        lagging_short: list[str] = []

        for sym, sig in self._signals.items():
            signals[sym] = {
                "btc_change_1h": sig.btc_change_1h,
                "alt_change_1h": sig.alt_change_1h,
                "lag": sig.lag,
                "volume_ratio": sig.volume_ratio,
                "trend_1h": sig.trend_1h,
                "price": sig.price,
            }
            if sig.lag is not None and sig.btc_change_1h is not None:
                if sig.btc_change_1h >= self._btc_min_move_pct and sig.lag >= self._btc_min_lag_pct:
                    lagging_long.append(sym)
                elif sig.btc_change_1h <= -self._btc_min_move_pct and sig.lag <= -self._btc_min_lag_pct:
                    lagging_short.append(sym)

        return {
            "strategy": "btc_correlation",
            "interval": self._interval,
            "coins_scanned": len(self._signals),
            "btc_change_1h": self._btc_change_1h,
            "lagging_long": lagging_long,
            "lagging_short": lagging_short,
            "signals": signals,
            "cooldown": self._cooldown.get_state(),
            "trend_filter": self._trend.get_all_states(),
        }
