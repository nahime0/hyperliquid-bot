"""Position Tracker — tracks open positions with entry price, SL, TP.

Supports LONG and SHORT directions for Hyperliquid perpetuals.

Every BUY/SHORT opens a position. Every SELL/CLOSE or SL/TP hit closes it.
The tracker provides position data for AI snapshots and auto-closes
positions when stop loss or take profit are reached.

Phase 11 additions:
- Trailing stop: moves SL as price moves in favor (break-even → trailing → tight)
- Time stop: auto-closes stale positions with low PnL
- Direction-aware PnL, SL/TP checks, trailing stops
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from config.settings import RiskConfig
from data.db import Database
from utils.logger import get_logger

logger = get_logger(__name__)

# Hyperliquid fee: 0.045% taker per leg, 0.03% × 2 ≈ 0.06% round-trip
FEE_PER_LEG = 0.00045


class PositionTracker:
    """Tracks open positions with entry price, stop loss, take profit."""

    def __init__(self, db: Database, risk_config: RiskConfig | None = None) -> None:
        self._db = db
        self._rc = risk_config

    async def open_position(
        self,
        symbol: str,
        entry_price: float,
        quantity: float,
        stop_loss: float | None = None,
        take_profit: float | None = None,
        strategy: str = "ai",
        direction: str = "LONG",
        leverage: int = 1,
    ) -> int:
        """Record a new position after a BUY or SHORT is executed."""
        # For SHORT: track min_price_seen instead of max_price_seen
        initial_price_seen = entry_price

        cursor = await self._db.db.execute(
            """INSERT INTO positions
               (symbol, entry_price, quantity, stop_loss, take_profit, strategy,
                max_price_seen, min_price_seen, original_sl, direction, leverage)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (symbol, entry_price, quantity, stop_loss, take_profit, strategy,
             initial_price_seen, initial_price_seen, stop_loss, direction, leverage),
        )
        await self._db.db.commit()
        pos_id = cursor.lastrowid
        logger.info(
            "Position opened #%d: %s %s qty=%.6f @ %.4f SL=%.4f TP=%.4f lev=%dx",
            pos_id, direction, symbol, quantity, entry_price,
            stop_loss or 0, take_profit or 0, leverage,
        )
        return pos_id  # type: ignore[return-value]

    async def close_position(
        self,
        position_id: int,
        exit_price: float,
        reason: str = "",
    ) -> float:
        """Close a position and compute PnL. Returns the PnL."""
        pos = await self._get_by_id(position_id)
        if not pos:
            logger.warning("Position #%d not found", position_id)
            return 0.0

        direction = pos.get("direction", "LONG")

        # Direction-aware PnL
        if direction == "LONG":
            pnl = (exit_price - pos["entry_price"]) * pos["quantity"]
        else:  # SHORT
            pnl = (pos["entry_price"] - exit_price) * pos["quantity"]

        # Fee estimate (Hyperliquid taker: 0.045% per leg)
        fee_estimate = (pos["entry_price"] + exit_price) * pos["quantity"] * FEE_PER_LEG
        pnl_net = pnl - fee_estimate

        await self._db.db.execute(
            """UPDATE positions SET status='CLOSED', exit_price=?, pnl=?,
               close_reason=?, closed_at=strftime('%Y-%m-%dT%H:%M:%SZ','now')
               WHERE id=?""",
            (exit_price, pnl_net, reason, position_id),
        )
        await self._db.db.commit()

        logger.info(
            "Position closed #%d: %s %s @ %.4f PnL=%.4f (%s)",
            position_id, direction, pos["symbol"], exit_price, pnl_net, reason,
        )
        return pnl_net

    async def check_sl_tp(self, prices: dict[str, float]) -> list[dict[str, Any]]:
        """Check all open positions against current prices.

        Updates trailing stops first, then checks SL/TP/time stop.
        Direction-aware: LONG SL triggers on price drop, SHORT SL on price rise.
        Returns list of {"position": dict, "action": "CLOSE", "reason": str}
        """
        positions = await self.get_open_positions()
        to_close: list[dict[str, Any]] = []

        for pos in positions:
            symbol = pos["symbol"]
            price = prices.get(symbol)
            if price is None:
                continue

            direction = pos.get("direction", "LONG")

            # Update trailing stop
            await self._update_trailing_stop(pos, price)

            # Re-read position after trailing update
            pos = await self._get_by_id(pos["id"]) or pos

            # Check stop loss (includes trailing SL)
            effective_sl = pos.get("trailing_sl") or pos.get("stop_loss")
            if effective_sl:
                sl_triggered = False
                if direction == "LONG" and price <= effective_sl:
                    sl_triggered = True
                elif direction == "SHORT" and price >= effective_sl:
                    sl_triggered = True

                if sl_triggered:
                    reason = f"stop_loss ({price:.4f} {'<=' if direction == 'LONG' else '>='} {effective_sl:.4f})"
                    if pos.get("trailing_sl") and pos.get("original_sl"):
                        if direction == "LONG" and pos["trailing_sl"] > pos["original_sl"]:
                            reason = f"trailing_sl ({price:.4f} <= {pos['trailing_sl']:.4f})"
                        elif direction == "SHORT" and pos["trailing_sl"] < pos["original_sl"]:
                            reason = f"trailing_sl ({price:.4f} >= {pos['trailing_sl']:.4f})"
                    to_close.append({
                        "position": pos,
                        "action": "CLOSE",
                        "reason": reason,
                    })
                    continue

            # Check take profit (direction-aware)
            if pos["take_profit"]:
                tp_triggered = False
                if direction == "LONG" and price >= pos["take_profit"]:
                    tp_triggered = True
                elif direction == "SHORT" and price <= pos["take_profit"]:
                    tp_triggered = True

                if tp_triggered:
                    to_close.append({
                        "position": pos,
                        "action": "CLOSE",
                        "reason": f"take_profit ({price:.4f} {'<=' if direction == 'SHORT' else '>='} {pos['take_profit']:.4f})",
                    })
                    continue

            # Check time stop
            time_close = self._check_time_stop(pos, price)
            if time_close:
                to_close.append(time_close)

        return to_close

    async def _update_trailing_stop(self, pos: dict[str, Any], current_price: float) -> None:
        """Update trailing stop for a position based on current price.

        Direction-aware:
        - LONG: tracks max_price_seen, SL only moves UP
        - SHORT: tracks min_price_seen, SL only moves DOWN
        """
        if not self._rc:
            return

        direction = pos.get("direction", "LONG")
        entry = pos["entry_price"]

        if direction == "LONG":
            await self._update_trailing_long(pos, current_price, entry)
        else:
            await self._update_trailing_short(pos, current_price, entry)

    async def _update_trailing_long(self, pos: dict[str, Any], current_price: float, entry: float) -> None:
        """Trailing stop for LONG: SL moves up as price rises."""
        max_seen = pos.get("max_price_seen") or entry
        old_trailing = pos.get("trailing_sl") or pos.get("stop_loss") or 0.0

        new_max = max(max_seen, current_price)
        gain_pct = (new_max - entry) / entry * 100 if entry > 0 else 0

        new_trailing = old_trailing

        if gain_pct >= self._rc.trailing_tight_pct:
            new_trailing = new_max * (1 - self._rc.trailing_tight_distance_pct / 100)
        elif gain_pct >= self._rc.trailing_start_pct:
            new_trailing = new_max * (1 - self._rc.trailing_distance_pct / 100)
        elif gain_pct >= self._rc.trailing_breakeven_pct:
            new_trailing = entry

        # SL only moves up for LONG
        new_trailing = max(new_trailing, old_trailing)

        if new_max != max_seen or new_trailing != old_trailing:
            await self._db.db.execute(
                "UPDATE positions SET max_price_seen=?, trailing_sl=? WHERE id=?",
                (new_max, new_trailing, pos["id"]),
            )
            await self._db.db.commit()

            if new_trailing > old_trailing:
                logger.info(
                    "Trailing SL updated #%d %s LONG: %.4f → %.4f (gain=%.2f%%, max=%.4f)",
                    pos["id"], pos["symbol"], old_trailing, new_trailing, gain_pct, new_max,
                )

    async def _update_trailing_short(self, pos: dict[str, Any], current_price: float, entry: float) -> None:
        """Trailing stop for SHORT: SL moves down as price drops."""
        min_seen = pos.get("min_price_seen") or entry
        old_trailing = pos.get("trailing_sl") or pos.get("stop_loss") or float("inf")

        new_min = min(min_seen, current_price)
        gain_pct = (entry - new_min) / entry * 100 if entry > 0 else 0

        new_trailing = old_trailing

        if gain_pct >= self._rc.trailing_tight_pct:
            new_trailing = new_min * (1 + self._rc.trailing_tight_distance_pct / 100)
        elif gain_pct >= self._rc.trailing_start_pct:
            new_trailing = new_min * (1 + self._rc.trailing_distance_pct / 100)
        elif gain_pct >= self._rc.trailing_breakeven_pct:
            new_trailing = entry

        # SL only moves down for SHORT
        new_trailing = min(new_trailing, old_trailing)

        if new_min != min_seen or new_trailing != old_trailing:
            await self._db.db.execute(
                "UPDATE positions SET min_price_seen=?, trailing_sl=? WHERE id=?",
                (new_min, new_trailing, pos["id"]),
            )
            await self._db.db.commit()

            if new_trailing < old_trailing:
                logger.info(
                    "Trailing SL updated #%d %s SHORT: %.4f → %.4f (gain=%.2f%%, min=%.4f)",
                    pos["id"], pos["symbol"], old_trailing, new_trailing, gain_pct, new_min,
                )

    def _check_time_stop(self, pos: dict[str, Any], current_price: float) -> dict[str, Any] | None:
        """Check if position should be closed due to time stop."""
        if not self._rc or self._rc.time_stop_hours <= 0:
            return None

        opened_at = pos.get("opened_at")
        if not opened_at:
            return None

        opened = datetime.fromisoformat(opened_at.replace("Z", "+00:00"))
        age_hours = (datetime.now(timezone.utc) - opened).total_seconds() / 3600

        if age_hours < self._rc.time_stop_hours:
            return None

        # Direction-aware PnL
        entry = pos["entry_price"]
        direction = pos.get("direction", "LONG")
        if direction == "LONG":
            pnl_pct = (current_price - entry) / entry * 100 if entry > 0 else 0
        else:
            pnl_pct = (entry - current_price) / entry * 100 if entry > 0 else 0

        if pnl_pct < self._rc.time_stop_min_pnl_pct:
            return {
                "position": pos,
                "action": "CLOSE",
                "reason": f"time_stop ({age_hours:.1f}h, PnL={pnl_pct:.2f}% < {self._rc.time_stop_min_pnl_pct}%)",
            }
        return None

    async def get_open_positions(self) -> list[dict[str, Any]]:
        """Get all OPEN positions."""
        cursor = await self._db.db.execute(
            "SELECT * FROM positions WHERE status='OPEN' ORDER BY id"
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def get_position_for_symbol(self, symbol: str) -> dict[str, Any] | None:
        """Get the open position for a symbol (if any)."""
        cursor = await self._db.db.execute(
            "SELECT * FROM positions WHERE symbol=? AND status='OPEN' ORDER BY id DESC LIMIT 1",
            (symbol,),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def get_open_symbols(self) -> set[str]:
        """Get set of symbols with open positions."""
        cursor = await self._db.db.execute(
            "SELECT DISTINCT symbol FROM positions WHERE status='OPEN'"
        )
        rows = await cursor.fetchall()
        return {r["symbol"] for r in rows}

    async def _get_by_id(self, position_id: int) -> dict[str, Any] | None:
        cursor = await self._db.db.execute(
            "SELECT * FROM positions WHERE id=?", (position_id,)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None
