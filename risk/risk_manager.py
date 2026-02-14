"""Risk Manager — the final gatekeeper before any order hits the exchange.

Adapted for Hyperliquid perpetuals with LONG/SHORT support.

Responsibilities:
- Real-time drawdown tracking (from peak balance)
- Kill switch: halts ALL trading when hard limits are breached
- Daily drawdown pause: pauses new entries for the rest of the day
- validate_decision(): inspects every decision, can reduce size or block
- get_risk_metrics(): produces risk state for the AI market snapshot
- Liquidation proximity monitoring
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import pandas as pd
import ta as ta_lib

from config.settings import MarketConfig, RiskConfig
from core.types import Decision
from core.client import HyperliquidClient
from data.db import Database
from risk.position_sizer import PositionSizer, SizeResult
from utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class ValidationResult:
    """Outcome of validate_decision()."""
    approved: bool
    decision: Decision               # possibly modified (size reduced)
    size: SizeResult | None = None   # computed position size
    reason: str = ""                 # why blocked / modified


@dataclass
class _DailyState:
    """Tracks intra-day drawdown.  Resets at midnight UTC."""
    date: str = ""              # YYYY-MM-DD
    start_balance: float = 0.0  # balance at start of day
    low_balance: float = 0.0    # lowest balance seen today


class RiskManager:
    """Central risk authority.  Has VETO power over every decision."""

    def __init__(
        self,
        config: RiskConfig,
        client: HyperliquidClient,
        db: Database,
        position_tracker: Any = None,
        market_config: MarketConfig | None = None,
        market_data: Any = None,
        ai_min_confidence: float = 0.5,
    ) -> None:
        self._config = config
        self._client = client
        self._db = db
        self._sizer = PositionSizer(config)
        self._position_tracker = position_tracker
        self._market_config = market_config
        self._market_data = market_data
        self._ai_min_confidence = ai_min_confidence

        # Runtime state
        self._peak_balance: float = 0.0
        self._current_balance: float = 0.0
        self._total_margin_used: float = 0.0
        self._open_position_count: int = 0
        self._kill_switch: bool = False
        self._kill_reason: str = ""
        self._daily_paused: bool = False
        self._daily_pause_reason: str = ""
        self._daily = _DailyState()
        self._active_coins: list[str] = []
        self._last_refresh: float = 0.0
        self._cycle_count: int = 0

    def set_active_pairs(self, coins: list[str]) -> None:
        """Update the list of actively traded coins."""
        self._active_coins = coins

    def set_cycle(self, cycle: int) -> None:
        self._cycle_count = cycle

    # ── Lifecycle ────────────────────────────────────────────

    async def start(self) -> None:
        """Initialize from Hyperliquid (source of truth), then reconcile with DB.

        Flow:
        0. Restore kill switch from DB (survives restarts)
        1. Fetch live balance + open positions from Hyperliquid
        2. Use live balance as baseline for peak
        3. Only use DB peak if it's from a consistent session (same ballpark)
        This handles: fresh wallets, wallet changes, manual web trades, crashes.
        """
        # Step 1: Live state from Hyperliquid
        await self.refresh()

        # Step 2: Reconcile peak with DB history (for drawdown tracking across restarts)
        if self._current_balance > 0:
            db_snapshot = await self._db.get_latest_balance()
            if db_snapshot:
                db_peak = db_snapshot.get("peak_balance", 0) or 0
                db_balance = db_snapshot.get("total_usdc", 0) or 0
                # Only trust DB peak if last recorded balance is in the same ballpark
                # (protects against stale data from different wallet/network)
                if db_peak > self._peak_balance and db_balance > 0:
                    ratio = self._current_balance / db_balance
                    if 0.5 < ratio < 2.0:
                        self._peak_balance = db_peak
                        logger.info("Restored peak from DB: %.2f (last balance: %.2f)", db_peak, db_balance)
                    else:
                        logger.info(
                            "Ignoring stale DB peak %.2f (DB balance=%.2f vs live=%.2f)",
                            db_peak, db_balance, self._current_balance,
                        )

        logger.info(
            "RiskManager started — balance=%.2f peak=%.2f positions=%d kill=%s",
            self._current_balance, self._peak_balance, self._open_position_count, self._kill_switch,
        )

    async def refresh(self) -> None:
        """Refresh balance and open positions from Hyperliquid."""
        try:
            # Get account balance (perp + spot USDC)
            balance = await self._client.get_account_balance()
            self._current_balance = balance

            # Update peak
            if balance > self._peak_balance:
                self._peak_balance = balance

            # Count open positions and sum margin used from Hyperliquid
            hl_positions = await self._client.get_open_positions()
            self._open_position_count = len(hl_positions)
            self._total_margin_used = sum(
                float(p.get("marginUsed", 0) or 0) for p in hl_positions
            )

            # Check liquidation proximity
            for pos in hl_positions:
                liq_px = pos.get("liquidationPx")
                if liq_px and liq_px > 0:
                    entry_px = pos["entryPx"]
                    if entry_px > 0:
                        distance_pct = abs(entry_px - liq_px) / entry_px * 100
                        if distance_pct < self._config.liquidation_buffer_pct:
                            logger.warning(
                                "LIQUIDATION WARNING: %s %s is %.1f%% from liquidation "
                                "(entry=%.2f, liq=%.2f)",
                                pos["direction"], pos["coin"], distance_pct,
                                entry_px, liq_px,
                            )

            # Daily tracking
            self._update_daily(balance)

            # Check kill conditions
            await self._check_kill_switch()
            self._check_daily_pause()

            self._last_refresh = time.time()
        except Exception:
            logger.exception("RiskManager.refresh() failed")

    # ── Validation ───────────────────────────────────────────

    async def validate_decision(self, decision: Decision) -> ValidationResult:
        """Validate a decision. May reduce size or block entirely.

        Accepts BUY, SHORT, SELL, CLOSE, HOLD, SCALE_UP actions.

        For entries (BUY/SHORT), the flow is:
        1. Compute SL (ATR or fixed) → get sl_distance_pct
        2. Auto TP if enabled
        3. R:R gate (if TP set)
        4. Position sizing with sl_distance_pct (risk-based)
        """
        # HOLD is always allowed
        if decision.action == "HOLD":
            return ValidationResult(approved=True, decision=decision, reason="pass-through")

        # ── Coin blacklist ──
        if (decision.action in ("BUY", "SHORT", "SCALE_UP", "FLIP")
                and decision.symbol
                and self._market_config
                and decision.symbol in self._market_config.coin_blacklist):
            return await self._block(decision, f"Blacklisted coin: {decision.symbol}")

        # ── Kill switch ──
        if self._kill_switch:
            return await self._block(decision, f"KILL SWITCH: {self._kill_reason}")

        # ── Daily pause (blocks new entries and scale-ups) ──
        if self._daily_paused and decision.action in ("BUY", "SHORT", "SCALE_UP"):
            return await self._block(decision, f"DAILY PAUSE: {self._daily_pause_reason}")

        # ── Symbol required for entry/exit ──
        if not decision.symbol:
            return await self._block(decision, "No symbol specified")

        # ── CLOSE/SELL: holding period check, then approve ──
        if decision.action in ("CLOSE", "SELL"):
            holding_block = await self._check_holding_period(decision)
            if holding_block:
                return holding_block
            return ValidationResult(approved=True, decision=decision, reason="approved_close")

        # ── SCALE_UP: separate validation path ──
        if decision.action == "SCALE_UP":
            return await self._validate_scale_up(decision)

        # ── FLIP: separate validation path ──
        if decision.action == "FLIP":
            return await self._validate_flip(decision)

        # ── Max open positions (dynamic or static) ──
        max_pos = self._effective_max_positions()
        if decision.action in ("BUY", "SHORT") and self._open_position_count >= max_pos:
            return await self._block(
                decision,
                f"Max open positions reached ({self._open_position_count}/{max_pos})",
            )

        # ── No duplicate positions on same symbol ──
        if decision.action in ("BUY", "SHORT") and decision.symbol and self._position_tracker:
            existing = await self._position_tracker.get_position_for_symbol(decision.symbol)
            if existing:
                return await self._block(
                    decision,
                    f"Already holding {decision.symbol} (position #{existing['id']})",
                )

        # ── Spread check (pre-entry) ──
        if decision.action in ("BUY", "SHORT"):
            spread_block = await self._check_spread(decision)
            if spread_block:
                return spread_block

        # ── Minimum balance ──
        if self._current_balance < self._config.min_balance_usdc:
            return await self._block(
                decision,
                f"Balance {self._current_balance:.2f} below minimum {self._config.min_balance_usdc}",
            )

        # ── Minimum confidence ──
        if decision.confidence < self._ai_min_confidence:
            return await self._block(decision, f"Confidence {decision.confidence:.2f} below minimum {self._ai_min_confidence}")

        # ── Step 1: Compute SL (before sizing) ──
        sl_distance_pct: float | None = None
        if decision.action in ("BUY", "SHORT") and decision.stop_loss is None:
            price = decision.limit_price
            if price is None:
                try:
                    price = await self._client.get_price(decision.symbol)
                except Exception:
                    return await self._block(decision, "Cannot determine price for stop loss")

            is_long = decision.action == "BUY"

            # Try ATR-based SL first, fall back to fixed %
            atr_sl = self._compute_atr_sl(decision.symbol, price, is_long)
            if atr_sl is not None:
                decision.stop_loss = atr_sl
            elif is_long:
                decision.stop_loss = round(price * (1 - self._config.stop_loss_pct / 100), 8)
            else:
                decision.stop_loss = round(price * (1 + self._config.stop_loss_pct / 100), 8)

            # Auto take profit (if enabled)
            if self._config.auto_take_profit and decision.take_profit is None:
                if is_long:
                    decision.take_profit = round(price * (1 + self._config.take_profit_pct / 100), 8)
                else:
                    decision.take_profit = round(price * (1 - self._config.take_profit_pct / 100), 8)

            sl_type = "ATR" if atr_sl is not None else "fixed"
            logger.info(
                "Auto SL/TP applied for %s (%s): SL=%g TP=%s",
                decision.action, sl_type, decision.stop_loss, decision.take_profit,
            )

        # Compute SL distance % for risk-based sizing
        if decision.stop_loss is not None and decision.action in ("BUY", "SHORT"):
            price = decision.limit_price
            if price is None:
                try:
                    price = await self._client.get_price(decision.symbol)
                except Exception:
                    price = None
            if price and price > 0:
                sl_distance_pct = abs(price - decision.stop_loss) / price * 100

        # ── Step 2: R:R gate (only when TP is explicitly set) ──
        if (decision.stop_loss and decision.take_profit
                and self._config.min_rr_ratio > 0
                and decision.action in ("BUY", "SHORT")):
            price = decision.limit_price
            if price is None:
                try:
                    price = await self._client.get_price(decision.symbol)
                except Exception:
                    price = None
            if price and price > 0:
                risk = abs(price - decision.stop_loss)
                reward = abs(decision.take_profit - price)
                if risk > 0 and reward / risk < self._config.min_rr_ratio:
                    return await self._block(
                        decision,
                        f"R:R {reward / risk:.2f} < {self._config.min_rr_ratio} "
                        f"(reward={reward:.2f}, risk={risk:.2f})",
                    )

        # ── Step 3: Position sizing (Kelly + utilization boost + risk cap) ──
        trade_stats = await self._db.get_trade_stats()
        utilization_boost = self._compute_utilization_boost()
        size = self._sizer.compute(
            bankroll=self._current_balance,
            trade_stats=trade_stats,
            ai_confidence=decision.confidence,
            ai_size_pct=decision.size_pct,
            utilization_boost=utilization_boost,
            sl_distance_pct=sl_distance_pct,
        )

        if size.size_usdc <= 0:
            return await self._block(decision, f"Position sizer: {size.reason}")

        # Update decision with computed size
        decision.size_pct = size.size_pct

        logger.info(
            "Decision APPROVED: %s %s size=%.2f%% (%.2f USDC) conf=%.2f boost=%.2f sl_dist=%.2f%%",
            decision.action, decision.symbol, size.size_pct, size.size_usdc,
            decision.confidence, utilization_boost, sl_distance_pct or 0,
        )
        if self._cycle_count > 0 and decision.symbol:
            try:
                await self._db.insert_event(
                    cycle=self._cycle_count,
                    symbol=decision.symbol,
                    event_type="RISK_APPROVED",
                    source="risk_manager",
                    action=decision.action,
                    confidence=decision.confidence,
                    details={
                        "size_pct": size.size_pct,
                        "size_usdc": round(size.size_usdc, 2),
                        "utilization_boost": round(utilization_boost, 2),
                        "sl_distance_pct": round(sl_distance_pct, 2) if sl_distance_pct else None,
                    },
                )
            except Exception:
                logger.debug("Failed to log RISK_APPROVED event", exc_info=True)
        return ValidationResult(
            approved=True,
            decision=decision,
            size=size,
            reason="approved",
        )

    async def _validate_scale_up(self, decision: Decision) -> ValidationResult:
        """Validate a SCALE_UP decision: requires existing position in profit."""
        if not self._position_tracker:
            return await self._block(decision, "No position tracker")

        pos = await self._position_tracker.get_position_for_symbol(decision.symbol)
        if not pos:
            return await self._block(decision, f"SCALE_UP: no open position for {decision.symbol}")

        # Must be in profit
        pnl_pct = pos.get("pnl_pct")
        if pnl_pct is None:
            # Compute from current price
            mid = None
            try:
                mid = await self._client.get_price(decision.symbol)
            except Exception:
                pass
            if mid and mid > 0:
                entry = pos["entry_price"]
                direction = pos.get("direction", "LONG")
                if direction == "LONG":
                    pnl_pct = (mid - entry) / entry * 100
                else:
                    pnl_pct = (entry - mid) / entry * 100
            else:
                return await self._block(decision, "SCALE_UP: cannot determine current PnL")

        if pnl_pct <= 0:
            return await self._block(decision, f"SCALE_UP: position is not in profit (PnL={pnl_pct:.2f}%)")

        # Spread check
        spread_block = await self._check_spread(decision)
        if spread_block:
            return spread_block

        # Minimum balance
        if self._current_balance < self._config.min_balance_usdc:
            return await self._block(
                decision,
                f"Balance {self._current_balance:.2f} below minimum {self._config.min_balance_usdc}",
            )

        # Sizing (with utilization boost)
        trade_stats = await self._db.get_trade_stats()
        utilization_boost = self._compute_utilization_boost()
        size = self._sizer.compute(
            bankroll=self._current_balance,
            trade_stats=trade_stats,
            ai_confidence=decision.confidence,
            ai_size_pct=decision.size_pct,
            utilization_boost=utilization_boost,
        )

        if size.size_usdc <= 0:
            return await self._block(decision, f"SCALE_UP sizer: {size.reason}")

        decision.size_pct = size.size_pct

        logger.info(
            "Decision APPROVED: SCALE_UP %s size=%.2f%% (%.2f USDC) conf=%.2f PnL=%.2f%%",
            decision.symbol, size.size_pct, size.size_usdc, decision.confidence, pnl_pct,
        )
        if self._cycle_count > 0 and decision.symbol:
            try:
                await self._db.insert_event(
                    cycle=self._cycle_count,
                    symbol=decision.symbol,
                    event_type="RISK_APPROVED",
                    source="risk_manager",
                    action="SCALE_UP",
                    confidence=decision.confidence,
                    details={
                        "size_pct": size.size_pct,
                        "size_usdc": round(size.size_usdc, 2),
                        "pnl_pct": round(pnl_pct, 2),
                    },
                )
            except Exception:
                logger.debug("Failed to log RISK_APPROVED event", exc_info=True)
        return ValidationResult(
            approved=True,
            decision=decision,
            size=size,
            reason="approved_scale_up",
        )

    async def _validate_flip(self, decision: Decision) -> ValidationResult:
        """Validate a FLIP decision: close losing position + open opposite direction."""
        if not self._position_tracker:
            return await self._block(decision, "No position tracker")

        pos = await self._position_tracker.get_position_for_symbol(decision.symbol)
        if not pos:
            return await self._block(decision, f"FLIP: no open position for {decision.symbol}")

        # Must be in loss (if in profit, use CLOSE instead)
        mid = None
        try:
            mid = await self._client.get_price(decision.symbol)
        except Exception:
            pass
        if mid and mid > 0:
            entry = pos["entry_price"]
            direction = pos.get("direction", "LONG")
            if direction == "LONG":
                pnl_pct = (mid - entry) / entry * 100
            else:
                pnl_pct = (entry - mid) / entry * 100
        else:
            return await self._block(decision, "FLIP: cannot determine current PnL")

        if pnl_pct > 0:
            return await self._block(decision, f"FLIP: position is in profit (PnL={pnl_pct:.2f}%) — use CLOSE instead")

        # Daily pause check
        if self._daily_paused:
            return await self._block(decision, f"DAILY PAUSE: {self._daily_pause_reason}")

        # Spread check for new entry
        spread_block = await self._check_spread(decision)
        if spread_block:
            return spread_block

        # Minimum balance
        if self._current_balance < self._config.min_balance_usdc:
            return await self._block(
                decision,
                f"Balance {self._current_balance:.2f} below minimum {self._config.min_balance_usdc}",
            )

        # Sizing for the new position (net count doesn't change: -1 +1)
        trade_stats = await self._db.get_trade_stats()
        utilization_boost = self._compute_utilization_boost()
        size = self._sizer.compute(
            bankroll=self._current_balance,
            trade_stats=trade_stats,
            ai_confidence=decision.confidence,
            ai_size_pct=decision.size_pct,
            utilization_boost=utilization_boost,
        )

        if size.size_usdc <= 0:
            return await self._block(decision, f"FLIP sizer: {size.reason}")

        decision.size_pct = size.size_pct

        logger.info(
            "Decision APPROVED: FLIP %s size=%.2f%% (%.2f USDC) conf=%.2f PnL=%.2f%%",
            decision.symbol, size.size_pct, size.size_usdc, decision.confidence, pnl_pct,
        )
        if self._cycle_count > 0 and decision.symbol:
            try:
                await self._db.insert_event(
                    cycle=self._cycle_count,
                    symbol=decision.symbol,
                    event_type="RISK_APPROVED",
                    source="risk_manager",
                    action="FLIP",
                    confidence=decision.confidence,
                    details={
                        "size_pct": size.size_pct,
                        "size_usdc": round(size.size_usdc, 2),
                        "pnl_pct": round(pnl_pct, 2),
                    },
                )
            except Exception:
                logger.debug("Failed to log RISK_APPROVED event", exc_info=True)
        return ValidationResult(
            approved=True,
            decision=decision,
            size=size,
            reason="approved_flip",
        )

    # ── Risk metrics (for AI snapshot) ───────────────────────

    def get_risk_metrics(self) -> dict[str, Any]:
        """Produce risk metrics for the AI market snapshot."""
        drawdown_pct = self._drawdown_pct()
        daily_dd = self._daily_drawdown_pct()
        utilization = self._compute_utilization()
        available_margin = max(0, self._current_balance - self._total_margin_used)
        return {
            "current_balance": round(self._current_balance, 2),
            "peak_balance": round(self._peak_balance, 2),
            "drawdown_pct": round(drawdown_pct, 4),
            "daily_drawdown_pct": round(daily_dd, 4),
            "open_positions": self._open_position_count,
            "max_open_positions": self._effective_max_positions(),
            "kill_switch": self._kill_switch,
            "kill_reason": self._kill_reason,
            "daily_paused": self._daily_paused,
            "daily_pause_reason": self._daily_pause_reason,
            "max_trade_pct": self._config.max_trade_pct,
            "stop_loss_pct": self._config.stop_loss_pct,
            "take_profit_pct": self._config.take_profit_pct,
            "min_balance_usdc": self._config.min_balance_usdc,
            "capital_utilization": round(utilization, 4),
            "total_margin_used": round(self._total_margin_used, 2),
            "available_margin": round(available_margin, 2),
            "target_utilization": self._config.target_utilization,
        }

    # ── Kill switch & daily pause (internal) ─────────────────

    async def _check_kill_switch(self) -> None:
        """Activate kill switch if hard limits are breached."""
        if self._kill_switch:
            return

        dd = self._drawdown_pct()
        if dd >= self._config.max_total_drawdown_pct:
            await self._trigger_kill(f"Total drawdown {dd:.2f}% >= {self._config.max_total_drawdown_pct}%")
            return

        if self._current_balance < self._config.min_balance_usdc:
            await self._trigger_kill(
                f"Balance {self._current_balance:.2f} < min {self._config.min_balance_usdc}"
            )
            return

    async def check_consecutive_losses(self) -> None:
        """Check for consecutive losses — handled by cooldown system, not kill switch."""
        pass

    async def _trigger_kill(self, reason: str) -> None:
        self._kill_switch = True
        self._kill_reason = reason
        logger.critical("KILL SWITCH ACTIVATED: %s", reason)

    async def reset_kill_switch(self) -> None:
        """Manual reset (operator override)."""
        if self._kill_switch:
            logger.warning("Kill switch RESET manually (was: %s)", self._kill_reason)
        self._kill_switch = False
        self._kill_reason = ""

    def _check_daily_pause(self) -> None:
        dd = self._daily_drawdown_pct()
        if dd >= self._config.max_daily_drawdown_pct and not self._daily_paused:
            self._daily_paused = True
            self._daily_pause_reason = f"Daily drawdown {dd:.2f}% >= {self._config.max_daily_drawdown_pct}%"
            logger.warning("DAILY PAUSE: %s", self._daily_pause_reason)

    def _update_daily(self, balance: float) -> None:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self._daily.date != today:
            self._daily = _DailyState(
                date=today,
                start_balance=balance,
                low_balance=balance,
            )
            self._daily_paused = False
            self._daily_pause_reason = ""
            logger.info("Daily reset: start_balance=%.2f", balance)
        else:
            if balance < self._daily.low_balance:
                self._daily.low_balance = balance

    # ── Drawdown helpers ─────────────────────────────────────

    def _drawdown_pct(self) -> float:
        if self._peak_balance <= 0:
            return 0.0
        return ((self._peak_balance - self._current_balance) / self._peak_balance) * 100

    def _daily_drawdown_pct(self) -> float:
        if self._daily.start_balance <= 0:
            return 0.0
        return ((self._daily.start_balance - self._current_balance) / self._daily.start_balance) * 100

    # ── Capital utilization ─────────────────────────────────

    def _compute_utilization(self) -> float:
        """Current capital utilization: margin_used / balance."""
        if self._current_balance <= 0:
            return 0.0
        return self._total_margin_used / self._current_balance

    def _compute_utilization_boost(self) -> float:
        """Compute sizing boost based on capital utilization vs target.

        When utilization is below target, boost sizing to deploy more capital.
        Boost = min(target / max(utilization, 0.05), max_size_boost).
        When utilization >= target, boost = 1.0 (no amplification).
        """
        utilization = self._compute_utilization()
        target = self._config.target_utilization
        if utilization >= target:
            return 1.0
        # Floor utilization at 5% to avoid extreme boosts with 0 margin used
        effective_util = max(utilization, 0.05)
        boost = target / effective_util
        return min(boost, self._config.max_size_boost)

    # ── ATR-based stop loss ─────────────────────────────────

    def _compute_atr_sl(self, symbol: str, price: float, is_long: bool) -> float | None:
        """Compute ATR-based stop loss price.

        Uses ATR(14) on 15m candles. Returns the SL price, or None if candles
        are unavailable (caller should fall back to fixed %).
        """
        if not self._market_data or not self._config.use_atr_sl:
            return None

        df = self._market_data.get_candles(symbol, "15m")
        if df is None or len(df) < 15:
            return None

        try:
            atr = ta_lib.volatility.AverageTrueRange(
                high=df["high"], low=df["low"], close=df["close"], window=14,
            ).average_true_range()
            atr_val = float(atr.iloc[-1])
            if pd.isna(atr_val) or atr_val <= 0:
                return None
        except Exception:
            logger.debug("ATR computation failed for %s", symbol, exc_info=True)
            return None

        # SL distance = multiplier * ATR, clamped to [min_pct, max_pct] of price
        sl_distance = self._config.atr_sl_multiplier * atr_val
        min_distance = price * self._config.atr_sl_min_pct / 100
        max_distance = price * self._config.atr_sl_max_pct / 100
        sl_distance = max(min_distance, min(sl_distance, max_distance))

        if is_long:
            sl_price = price - sl_distance
        else:
            sl_price = price + sl_distance

        sl_pct = sl_distance / price * 100
        logger.info(
            "ATR-based SL for %s %s: ATR=%.6f, SL_dist=%.6f (%.2f%%), SL=%g",
            "LONG" if is_long else "SHORT", symbol, atr_val, sl_distance, sl_pct, sl_price,
        )
        return round(sl_price, 8)

    # ── Spread check ─────────────────────────────────────────

    async def _check_spread(self, decision: Decision) -> ValidationResult | None:
        """Check bid-ask spread via L2 snapshot. Returns block result or None."""
        if not self._market_config or self._market_config.max_spread_pct <= 0:
            return None
        max_spread = self._market_config.max_spread_pct
        try:
            l2 = await self._client.get_l2_snapshot(decision.symbol)
            levels = l2.get("levels", [])
            if len(levels) < 2 or not levels[0] or not levels[1]:
                return None  # graceful: no L2 data
            best_bid = float(levels[0][0]["px"])
            best_ask = float(levels[1][0]["px"])
            if best_bid <= 0 or best_ask <= 0:
                return None
            mid = (best_ask + best_bid) / 2
            spread_pct = (best_ask - best_bid) / mid * 100
            if spread_pct > max_spread:
                return await self._block(
                    decision,
                    f"Spread {spread_pct:.2f}% > max {max_spread}% for {decision.symbol}",
                )
            logger.debug(
                "Spread OK for %s: %.3f%% (max %.1f%%)",
                decision.symbol, spread_pct, max_spread,
            )
        except Exception:
            logger.warning("L2 spread check failed for %s — allowing entry (graceful)", decision.symbol)
        return None

    # ── Helpers ──────────────────────────────────────────────

    def _effective_max_positions(self) -> int:
        """Compute max open positions — dynamic or static."""
        if not self._config.dynamic_positions:
            return self._config.max_open_positions
        if self._current_balance <= 0 or self._config.usdc_per_position <= 0:
            return 1
        dynamic = int(self._current_balance // self._config.usdc_per_position)
        return max(1, min(dynamic, self._config.max_open_positions))

    async def _check_holding_period(self, decision: Decision) -> ValidationResult | None:
        min_minutes = self._config.min_holding_minutes
        if min_minutes <= 0 or not self._position_tracker:
            return None

        pos = await self._position_tracker.get_position_for_symbol(decision.symbol)
        if not pos or not pos.get("opened_at"):
            return None

        opened = datetime.fromisoformat(pos["opened_at"].replace("Z", "+00:00"))
        age_minutes = (datetime.now(timezone.utc) - opened).total_seconds() / 60

        if age_minutes < min_minutes:
            return await self._block(
                decision,
                f"Holding period: {decision.symbol} opened {age_minutes:.0f}m ago "
                f"(min {min_minutes}m). SL/TP will still auto-trigger.",
            )
        return None

    async def _block(self, decision: Decision, reason: str) -> ValidationResult:
        logger.warning("Decision BLOCKED: %s %s — %s", decision.action, decision.symbol, reason)
        if self._cycle_count > 0 and decision.symbol:
            try:
                await self._db.insert_event(
                    cycle=self._cycle_count,
                    symbol=decision.symbol,
                    event_type="RISK_BLOCKED",
                    source="risk_manager",
                    action=decision.action,
                    confidence=decision.confidence,
                    reasoning=reason,
                )
            except Exception:
                logger.debug("Failed to log RISK_BLOCKED event", exc_info=True)
        return ValidationResult(approved=False, decision=decision, reason=reason)

    async def snapshot_balance(self) -> None:
        """Persist current balance to DB for historical tracking."""
        if self._current_balance > 0:
            await self._db.insert_balance_snapshot(
                total_usdc=self._current_balance,
                peak_balance=self._peak_balance,
            )
