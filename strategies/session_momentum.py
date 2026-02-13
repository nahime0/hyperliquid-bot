"""Session Momentum — scoring-based decision generator.

Generates BUY/SHORT/CLOSE decisions based on trading session transitions.
At the start of each new trading session (Asia/EU/US), carries momentum
from the prior session to capture continuation moves.

Sessions (UTC):
  - Asia: 00:00-08:00
  - EU:   08:00-16:00
  - US:   16:00-24:00

BUY Entry scoring (positive momentum from prior session):
  - Prior session change > sm_min_session_change  -> +0.30 (session_momentum)
  - Change in same direction as entry              -> +0.20 (overnight_change)
  - Volume ratio >= 1.0                            -> +0.15
  - 1h trend alignment (BULLISH)                   -> +0.15
  - |funding| < max_funding_rate                   -> +0.10
  - No per-symbol cooldown                         -> +0.10
  Score >= 0.50 -> generate signal (confidence = score)

SHORT Entry scoring (negative momentum from prior session):
  - Prior session change < -sm_min_session_change  -> +0.30 (session_momentum)
  - Change in same direction as entry              -> +0.20 (overnight_change)
  - Volume ratio >= 1.0                            -> +0.15
  - 1h trend alignment (BEARISH)                   -> +0.15
  - |funding| < max_funding_rate                   -> +0.10
  - No per-symbol cooldown                         -> +0.10

Hard blocks (always reject, bypass scoring):
  - |funding| >= 0.001 (extreme funding)
  - Global cooldown active (3+ consecutive losses)
  - Fresh per-symbol cooldown (loss < 10 min ago)

Entry window: first sm_entry_window_hours hours of each session only.

Exit:
  - Position held for >= sm_exit_hours
  - Momentum reversal: price reversed by > sm_min_session_change from entry direction
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import datetime, timezone
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
W_SESSION_MOMENTUM = 0.30   # prior session change meets threshold
W_OVERNIGHT_CHANGE = 0.20   # RSI confirms direction (not extreme opposite)
W_VOLUME = 0.15              # volume ratio >= threshold
W_TREND = 0.15               # 1h trend alignment
W_FUNDING = 0.10             # funding rate acceptable
W_COOLDOWN = 0.10            # no per-symbol cooldown
SCORE_THRESHOLD = 0.50       # minimum score to generate signal

# -- Thresholds --
MIN_VOLUME_RATIO = 1.0       # volume ratio threshold
RSI_PERIOD = 14
RSI_CONFIRM_LOW = 45.0       # RSI below this confirms bearish momentum
RSI_CONFIRM_HIGH = 55.0      # RSI above this confirms bullish momentum

# -- Session candle count (8 x 1h = one session) --
SESSION_BARS = 8


@dataclass
class SessionSignal:
    """Signal for one coin at a point in time."""
    symbol: str
    current_session: str         # "ASIA", "EU", "US"
    in_entry_window: bool
    session_change: float | None  # % change over prior session
    volume_ratio: float | None
    trend_1h: str
    rsi: float | None
    price: float | None
    updated_at: float


class SessionMomentumStrategy(Strategy):
    """Session momentum strategy: carry momentum from prior trading session."""

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
        self._sc = strategy_config or StrategyConfig()
        self._coins = coins or []
        self._interval = interval
        self._min_candle_volume_usdc = min_candle_volume_usdc
        self._signals: dict[str, SessionSignal] = {}
        self._funding_rates: dict[str, float] = {}
        self._max_funding_rate: float = 0.0005
        self._db = db
        self._cycle_count: int = 0
        # Session state (computed once per update)
        self._current_session: str = ""
        self._in_entry_window: bool = False

    def set_coins(self, coins: list[str]) -> None:
        self._coins = coins
        logger.info("SessionMomentum coins updated: %d", len(coins))

    def set_funding_rates(self, rates: dict[str, float]) -> None:
        self._funding_rates = rates

    def set_max_funding_rate(self, rate: float) -> None:
        self._max_funding_rate = rate

    def set_cycle(self, cycle: int) -> None:
        self._cycle_count = cycle

    # -- Lifecycle --

    async def start(self) -> None:
        logger.info(
            "SessionMomentumStrategy started — scanning %d coins on %s",
            len(self._coins), self._interval,
        )

    async def stop(self) -> None:
        self._signals.clear()
        logger.info("SessionMomentumStrategy stopped")

    # -- Session detection --

    @staticmethod
    def _detect_session() -> tuple[str, bool, int]:
        """Detect current trading session and whether we are in the entry window.

        Returns:
            (session_name, in_entry_window, hour_in_session)
        """
        now = datetime.now(timezone.utc)
        hour = now.hour

        if 0 <= hour < 8:
            session = "ASIA"
        elif 8 <= hour < 16:
            session = "EU"
        else:
            session = "US"

        hour_in_session = hour % 8
        return session, hour_in_session, hour

    # -- Update (called every tick) --

    async def update(self) -> None:
        """Scan all coins and update session signals."""
        # Compute session info once per cycle
        session, hour_in_session, _hour = self._detect_session()
        self._current_session = session
        self._in_entry_window = hour_in_session < self._sc.sm_entry_window_hours

        tasks = [self._scan_coin(sym) for sym in self._coins]
        await asyncio.gather(*tasks)

    async def _scan_coin(self, symbol: str) -> None:
        """Compute session momentum signal for one coin using 1h candles."""
        try:
            df_1h = self._md.get_candles(symbol, self._interval)
            if df_1h is None or len(df_1h) < SESSION_BARS + RSI_PERIOD + 1:
                return

            close = df_1h["close"]
            volume = df_1h["volume"]
            price = float(close.iloc[-1])

            # Volume floor: skip illiquid coins
            if self._min_candle_volume_usdc > 0:
                volume_usdc = float(price * volume.iloc[-1])
                if volume_usdc < self._min_candle_volume_usdc:
                    return

            # Prior session change: change over last 8 x 1h candles
            session_change = self._compute_session_change(close)

            # Volume ratio
            vol_sma = float(volume.rolling(20).mean().iloc[-1]) if len(volume) >= 20 else None
            vol_ratio = float(volume.iloc[-1]) / vol_sma if vol_sma and vol_sma > 0 else None

            # 1h trend from TrendFilter
            trend_state = self._trend.get_state(symbol)
            trend_1h = trend_state.get("trend", "NEUTRAL")

            # RSI on 1h
            rsi = None
            rsi_series = ta_lib.momentum.RSIIndicator(close, window=RSI_PERIOD).rsi()
            if not rsi_series.empty and pd.notna(rsi_series.iloc[-1]):
                rsi = float(rsi_series.iloc[-1])

            self._signals[symbol] = SessionSignal(
                symbol=symbol,
                current_session=self._current_session,
                in_entry_window=self._in_entry_window,
                session_change=round(session_change, 3) if session_change is not None else None,
                volume_ratio=round(vol_ratio, 2) if vol_ratio else None,
                trend_1h=trend_1h,
                rsi=rsi,
                price=price,
                updated_at=time.time(),
            )
        except Exception:
            logger.exception("Error scanning %s for session momentum", symbol)

    @staticmethod
    def _compute_session_change(close: pd.Series) -> float | None:
        """Compute % change over last 8 hours (one session) from 1h candles.

        Uses close[-1] vs close[-9]: the change spanning 8 hourly bars.
        """
        if len(close) < SESSION_BARS + 1:
            return None
        price_now = float(close.iloc[-1])
        price_session_ago = float(close.iloc[-(SESSION_BARS + 1)])
        if price_session_ago <= 0:
            return None
        return (price_now - price_session_ago) / price_session_ago * 100

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

            if pos.get("strategy") != "session_momentum":
                continue

            exit_d = self._check_exit(symbol, sig, pos)
            if exit_d:
                decisions.append(exit_d)

        # ENTRY checks (only during entry window)
        if not self._in_entry_window:
            logger.debug(
                "[SM] Not in entry window (session=%s), skipping entries",
                self._current_session,
            )
            return decisions

        scanned = 0
        for symbol in self._coins:
            if symbol in open_symbols:
                continue

            sig = self._signals.get(symbol)
            if not sig:
                continue

            scanned += 1

            # Check LONG entry (positive prior session)
            entry = self._check_long_entry(symbol, sig)
            if entry:
                decisions.append(entry)
                await self._log_signal(entry, sig)
                continue  # one direction per coin per cycle

            # Check SHORT entry (negative prior session)
            entry = self._check_short_entry(symbol, sig)
            if entry:
                decisions.append(entry)
                await self._log_signal(entry, sig)

        entries = sum(1 for d in decisions if d.action in ("BUY", "SHORT"))
        logger.info(
            "[SM SCAN] session=%s, in_window=%s, coins=%d, scanned=%d, signals=%d",
            self._current_session, self._in_entry_window,
            len(self._coins), scanned, entries,
        )
        return decisions

    def _check_long_entry(self, symbol: str, sig: SessionSignal) -> Decision | None:
        """Score-based LONG entry: positive momentum from prior session."""
        if sig.price is None or sig.session_change is None:
            return None

        # Must have positive momentum to go long
        if sig.session_change <= 0:
            return None

        # Hard blocks
        blocked, can_buy, abs_funding = check_hard_blocks(
            symbol,
            funding_rates=self._funding_rates,
            cooldown=self._cooldown,
            risk_config=self._rc,
        )
        if blocked:
            logger.debug("[SM LONG] %s -> HARD BLOCK", symbol)
            return None

        min_change = self._sc.sm_min_session_change

        # Scoring
        score = 0.0
        components: list[str] = []

        # Session momentum: graduated — stronger change scores higher
        if sig.session_change >= min_change:
            intensity = min(1.0, abs(sig.session_change) / (min_change * 4))
            score += W_SESSION_MOMENTUM * intensity
            components.append(f"sess_chg=+{sig.session_change:.2f}%(i={intensity:.2f})")

        # Overnight/direction confirmation via RSI (not extreme opposite)
        if sig.rsi is not None and sig.rsi > RSI_CONFIRM_HIGH:
            score += W_OVERNIGHT_CHANGE
            components.append(f"RSI={sig.rsi:.1f}>={RSI_CONFIRM_HIGH:.0f}")

        # Volume
        if sig.volume_ratio is not None and sig.volume_ratio >= MIN_VOLUME_RATIO:
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
                "[SM LONG] %s: score=%.3f < %.2f — sess_chg=+%.2f%%, RSI=%s, vol=%s",
                symbol, score, SCORE_THRESHOLD, sig.session_change,
                f"{sig.rsi:.1f}" if sig.rsi else "N/A",
                f"{sig.volume_ratio:.1f}" if sig.volume_ratio else "N/A",
            )
            return None

        vol_str = f"{sig.volume_ratio:.1f}" if sig.volume_ratio is not None else "N/A"
        rsi_str = f"{sig.rsi:.1f}" if sig.rsi is not None else "N/A"

        logger.debug("[SM LONG] %s: score=%.3f — %s", symbol, score, ", ".join(components))
        return Decision(
            action="BUY",
            symbol=symbol,
            confidence=score,
            reasoning=(
                f"[SessionMom LONG] {symbol}: score={score:.3f} — "
                f"session={sig.current_session}, sess_chg=+{sig.session_change:.2f}%, "
                f"RSI={rsi_str}, vol={vol_str}, trend={sig.trend_1h}"
            ),
            strategy_type="session_momentum",
            order_type="MARKET",
        )

    def _check_short_entry(self, symbol: str, sig: SessionSignal) -> Decision | None:
        """Score-based SHORT entry: negative momentum from prior session."""
        if sig.price is None or sig.session_change is None:
            return None

        # Must have negative momentum to go short
        if sig.session_change >= 0:
            return None

        # Hard blocks
        blocked, can_buy, abs_funding = check_hard_blocks(
            symbol,
            funding_rates=self._funding_rates,
            cooldown=self._cooldown,
            risk_config=self._rc,
        )
        if blocked:
            logger.debug("[SM SHORT] %s -> HARD BLOCK", symbol)
            return None

        min_change = self._sc.sm_min_session_change

        # Scoring
        score = 0.0
        components: list[str] = []

        # Session momentum: graduated — stronger drop scores higher
        if abs(sig.session_change) >= min_change:
            intensity = min(1.0, abs(sig.session_change) / (min_change * 4))
            score += W_SESSION_MOMENTUM * intensity
            components.append(f"sess_chg={sig.session_change:.2f}%(i={intensity:.2f})")

        # Overnight/direction confirmation via RSI (not extreme opposite)
        if sig.rsi is not None and sig.rsi < RSI_CONFIRM_LOW:
            score += W_OVERNIGHT_CHANGE
            components.append(f"RSI={sig.rsi:.1f}<={RSI_CONFIRM_LOW:.0f}")

        # Volume
        if sig.volume_ratio is not None and sig.volume_ratio >= MIN_VOLUME_RATIO:
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
                "[SM SHORT] %s: score=%.3f < %.2f — sess_chg=%.2f%%, RSI=%s, vol=%s",
                symbol, score, SCORE_THRESHOLD, sig.session_change,
                f"{sig.rsi:.1f}" if sig.rsi else "N/A",
                f"{sig.volume_ratio:.1f}" if sig.volume_ratio else "N/A",
            )
            return None

        vol_str = f"{sig.volume_ratio:.1f}" if sig.volume_ratio is not None else "N/A"
        rsi_str = f"{sig.rsi:.1f}" if sig.rsi is not None else "N/A"

        logger.debug("[SM SHORT] %s: score=%.3f — %s", symbol, score, ", ".join(components))
        return Decision(
            action="SHORT",
            symbol=symbol,
            confidence=score,
            reasoning=(
                f"[SessionMom SHORT] {symbol}: score={score:.3f} — "
                f"session={sig.current_session}, sess_chg={sig.session_change:.2f}%, "
                f"RSI={rsi_str}, vol={vol_str}, trend={sig.trend_1h}"
            ),
            strategy_type="session_momentum",
            order_type="MARKET",
        )

    def _check_exit(self, symbol: str, sig: SessionSignal, pos: dict) -> Decision | None:
        """Check if a session momentum position should be closed.

        Exit triggers:
          1. Position held for >= sm_exit_hours
          2. Momentum reversal: price reversed by > sm_min_session_change from entry
        """
        reasons: list[str] = []
        direction = pos.get("direction", "LONG")
        entry_price = pos.get("entry_price")
        opened_at = pos.get("opened_at")

        # Time-based exit
        if opened_at:
            age_hours = (time.time() - opened_at) / 3600.0
            if age_hours >= self._sc.sm_exit_hours:
                reasons.append(f"age={age_hours:.1f}h>={self._sc.sm_exit_hours}h")

        # Momentum reversal exit
        if entry_price and sig.price and entry_price > 0:
            pnl_pct = (sig.price - entry_price) / entry_price * 100
            if direction == "SHORT":
                pnl_pct = -pnl_pct

            # If price moved against us by more than the min session change
            if pnl_pct < -self._sc.sm_min_session_change:
                reasons.append(
                    f"momentum_reversal(pnl={pnl_pct:.2f}%<-{self._sc.sm_min_session_change}%)"
                )

        if not reasons:
            return None

        return Decision(
            action="CLOSE",
            symbol=symbol,
            confidence=0.8,
            reasoning=f"[SessionMom {direction} EXIT] {symbol}: {', '.join(reasons)}",
            strategy_type="session_momentum",
            order_type="MARKET",
        )

    async def _log_signal(self, decision: Decision, sig: SessionSignal) -> None:
        if not self._db or self._cycle_count <= 0:
            return
        try:
            await self._db.insert_event(
                cycle=self._cycle_count,
                symbol=decision.symbol or "",
                event_type="SIGNAL",
                source="session_momentum",
                action=decision.action,
                confidence=decision.confidence,
                reasoning=decision.reasoning,
                details={
                    "score": decision.confidence,
                    "session": sig.current_session,
                    "in_entry_window": sig.in_entry_window,
                    "session_change": sig.session_change,
                    "rsi": round(sig.rsi, 2) if sig.rsi is not None else None,
                    "trend_1h": sig.trend_1h,
                    "volume_ratio": sig.volume_ratio,
                },
            )
        except Exception:
            logger.debug("Failed to log SIGNAL event", exc_info=True)

    # -- State (for snapshot / dashboard) --

    def get_state(self) -> dict[str, Any]:
        signals: dict[str, Any] = {}
        bullish_momentum: list[str] = []
        bearish_momentum: list[str] = []

        min_change = self._sc.sm_min_session_change

        for sym, sig in self._signals.items():
            signals[sym] = {
                "current_session": sig.current_session,
                "in_entry_window": sig.in_entry_window,
                "session_change": sig.session_change,
                "volume_ratio": sig.volume_ratio,
                "trend_1h": sig.trend_1h,
                "rsi": round(sig.rsi, 2) if sig.rsi is not None else None,
                "price": sig.price,
            }
            if sig.session_change is not None:
                if sig.session_change >= min_change:
                    bullish_momentum.append(sym)
                elif sig.session_change <= -min_change:
                    bearish_momentum.append(sym)

        return {
            "strategy": "session_momentum",
            "interval": self._interval,
            "current_session": self._current_session,
            "in_entry_window": self._in_entry_window,
            "coins_scanned": len(self._signals),
            "bullish_momentum": bullish_momentum,
            "bearish_momentum": bearish_momentum,
            "signals": signals,
            "cooldown": self._cooldown.get_state(),
            "trend_filter": self._trend.get_all_states(),
        }
