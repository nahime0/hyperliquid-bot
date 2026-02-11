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

from config.settings import RiskConfig
from core.ai_engine.types import Decision
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
    ) -> None:
        self._config = config
        self._client = client
        self._db = db
        self._sizer = PositionSizer(config)
        self._position_tracker = position_tracker

        # Runtime state
        self._peak_balance: float = 0.0
        self._current_balance: float = 0.0
        self._open_position_count: int = 0
        self._kill_switch: bool = False
        self._kill_reason: str = ""
        self._daily_paused: bool = False
        self._daily_pause_reason: str = ""
        self._daily = _DailyState()
        self._active_coins: list[str] = []
        self._last_refresh: float = 0.0

    def set_active_pairs(self, coins: list[str]) -> None:
        """Update the list of actively traded coins."""
        self._active_coins = coins

    # ── Lifecycle ────────────────────────────────────────────

    async def start(self) -> None:
        """Initialize: load peak balance from DB, refresh current balance."""
        snapshot = await self._db.get_latest_balance()
        if snapshot:
            self._peak_balance = snapshot.get("peak_balance", 0) or snapshot["total_usdc"]
        await self.refresh()
        logger.info(
            "RiskManager started — balance=%.2f peak=%.2f kill=%s",
            self._current_balance, self._peak_balance, self._kill_switch,
        )

    async def refresh(self) -> None:
        """Refresh balance and open positions from Hyperliquid."""
        try:
            # Get account balance (accountValue from marginSummary)
            balance = await self._client.get_account_balance()
            self._current_balance = balance

            # Update peak
            if balance > self._peak_balance:
                self._peak_balance = balance

            # Count open positions from Hyperliquid
            hl_positions = await self._client.get_open_positions()
            self._open_position_count = len(hl_positions)

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
            self._check_kill_switch()
            self._check_daily_pause()

            self._last_refresh = time.time()
        except Exception:
            logger.exception("RiskManager.refresh() failed")

    # ── Validation ───────────────────────────────────────────

    async def validate_decision(self, decision: Decision) -> ValidationResult:
        """Validate a decision. May reduce size or block entirely.

        Accepts BUY, SHORT, SELL, CLOSE, HOLD actions.
        """
        # HOLD is always allowed
        if decision.action == "HOLD":
            return ValidationResult(approved=True, decision=decision, reason="pass-through")

        # CLOSE/SELL: enforce minimum holding period
        if decision.action in ("CLOSE", "SELL") and decision.symbol:
            block = await self._check_holding_period(decision)
            if block:
                return block

        # ── Kill switch ──
        if self._kill_switch:
            return self._block(decision, f"KILL SWITCH: {self._kill_reason}")

        # ── Daily pause (blocks new entries) ──
        if self._daily_paused and decision.action in ("BUY", "SHORT"):
            return self._block(decision, f"DAILY PAUSE: {self._daily_pause_reason}")

        # ── Symbol required for entry/exit ──
        if not decision.symbol:
            return self._block(decision, "No symbol specified")

        # ── Max open positions ──
        if decision.action in ("BUY", "SHORT") and self._open_position_count >= self._config.max_open_positions:
            return self._block(
                decision,
                f"Max open positions reached ({self._open_position_count}/{self._config.max_open_positions})",
            )

        # ── No duplicate positions on same symbol ──
        if decision.action in ("BUY", "SHORT") and decision.symbol and self._position_tracker:
            existing = await self._position_tracker.get_position_for_symbol(decision.symbol)
            if existing:
                return self._block(
                    decision,
                    f"Already holding {decision.symbol} (position #{existing['id']})",
                )

        # ── Minimum balance ──
        if self._current_balance < self._config.min_balance_usdc:
            return self._block(
                decision,
                f"Balance {self._current_balance:.2f} below minimum {self._config.min_balance_usdc}",
            )

        # ── Minimum confidence ──
        if decision.confidence < 0.5:
            return self._block(decision, f"Confidence {decision.confidence:.2f} too low")

        # ── Position sizing (Kelly) ──
        trade_stats = await self._db.get_trade_stats()
        size = self._sizer.compute(
            bankroll=self._current_balance,
            trade_stats=trade_stats,
            ai_confidence=decision.confidence,
            ai_size_pct=decision.size_pct,
        )

        if size.size_usdc <= 0:
            return self._block(decision, f"Position sizer: {size.reason}")

        # Update decision with computed size
        decision.size_pct = size.size_pct

        # ── Stop loss / take profit for entries ──
        if decision.action in ("BUY", "SHORT") and decision.stop_loss is None:
            price = decision.limit_price
            if price is None:
                try:
                    price = await self._client.get_price(decision.symbol)
                except Exception:
                    return self._block(decision, "Cannot determine price for stop loss")

            if decision.action == "BUY":
                # LONG: SL below, TP above
                decision.stop_loss = round(price * (1 - self._config.stop_loss_pct / 100), 8)
                decision.take_profit = round(price * (1 + self._config.take_profit_pct / 100), 8)
            else:
                # SHORT: SL above, TP below
                decision.stop_loss = round(price * (1 + self._config.stop_loss_pct / 100), 8)
                decision.take_profit = round(price * (1 - self._config.take_profit_pct / 100), 8)

            logger.info(
                "Auto SL/TP applied for %s: SL=%.4f TP=%.4f",
                decision.action, decision.stop_loss, decision.take_profit,
            )

        logger.info(
            "Decision APPROVED: %s %s size=%.2f%% (%.2f USDC) conf=%.2f",
            decision.action, decision.symbol, size.size_pct, size.size_usdc, decision.confidence,
        )
        return ValidationResult(
            approved=True,
            decision=decision,
            size=size,
            reason="approved",
        )

    # ── Risk metrics (for AI snapshot) ───────────────────────

    def get_risk_metrics(self) -> dict[str, Any]:
        """Produce risk metrics for the AI market snapshot."""
        drawdown_pct = self._drawdown_pct()
        daily_dd = self._daily_drawdown_pct()
        return {
            "current_balance": round(self._current_balance, 2),
            "peak_balance": round(self._peak_balance, 2),
            "drawdown_pct": round(drawdown_pct, 4),
            "daily_drawdown_pct": round(daily_dd, 4),
            "open_positions": self._open_position_count,
            "max_open_positions": self._config.max_open_positions,
            "kill_switch": self._kill_switch,
            "kill_reason": self._kill_reason,
            "daily_paused": self._daily_paused,
            "daily_pause_reason": self._daily_pause_reason,
            "max_trade_pct": self._config.max_trade_pct,
            "stop_loss_pct": self._config.stop_loss_pct,
            "take_profit_pct": self._config.take_profit_pct,
            "min_balance_usdc": self._config.min_balance_usdc,
        }

    # ── Kill switch & daily pause (internal) ─────────────────

    def _check_kill_switch(self) -> None:
        """Activate kill switch if hard limits are breached."""
        if self._kill_switch:
            return

        dd = self._drawdown_pct()
        if dd >= self._config.max_total_drawdown_pct:
            self._trigger_kill(f"Total drawdown {dd:.2f}% >= {self._config.max_total_drawdown_pct}%")
            return

        if self._current_balance < self._config.min_balance_usdc:
            self._trigger_kill(
                f"Balance {self._current_balance:.2f} < min {self._config.min_balance_usdc}"
            )
            return

    async def check_consecutive_losses(self) -> None:
        """Check for 5 consecutive losses.  Call after each trade."""
        stats = await self._db.get_trade_stats()
        consec = stats.get("consecutive_losses", 0)
        if consec >= 5 and not self._kill_switch:
            self._trigger_kill(f"{consec} consecutive losses")

    def _trigger_kill(self, reason: str) -> None:
        self._kill_switch = True
        self._kill_reason = reason
        logger.critical("KILL SWITCH ACTIVATED: %s", reason)

    def reset_kill_switch(self) -> None:
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

    # ── Helpers ──────────────────────────────────────────────

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
            return self._block(
                decision,
                f"Holding period: {decision.symbol} opened {age_minutes:.0f}m ago "
                f"(min {min_minutes}m). SL/TP will still auto-trigger.",
            )
        return None

    @staticmethod
    def _block(decision: Decision, reason: str) -> ValidationResult:
        logger.warning("Decision BLOCKED: %s %s — %s", decision.action, decision.symbol, reason)
        return ValidationResult(approved=False, decision=decision, reason=reason)

    async def snapshot_balance(self) -> None:
        """Persist current balance to DB for historical tracking."""
        if self._current_balance > 0:
            await self._db.insert_balance_snapshot(
                total_usdc=self._current_balance,
                peak_balance=self._peak_balance,
            )
