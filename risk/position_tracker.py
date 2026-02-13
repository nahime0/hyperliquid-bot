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

import time
from datetime import datetime, timezone
from typing import Any

import pandas as pd
import ta as ta_lib

from config.settings import RiskConfig
from data.db import Database
from utils.logger import get_logger

logger = get_logger(__name__)

# Hyperliquid fee: 0.045% taker per leg, 0.03% × 2 ≈ 0.06% round-trip
FEE_PER_LEG = 0.00045


class PositionTracker:
    """Tracks open positions with entry price, stop loss, take profit."""

    def __init__(self, db: Database, risk_config: RiskConfig | None = None, market_data: Any = None) -> None:
        self._db = db
        self._rc = risk_config
        self._market_data = market_data
        self._atr_cache: dict[str, tuple[float, float, float]] = {}  # symbol → (timestamp, trail_pct, tight_pct)
        self._cycle_count: int = 0

    def set_cycle(self, cycle: int) -> None:
        self._cycle_count = cycle

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
        # Prevent duplicate open positions on same symbol
        existing = await self.get_position_for_symbol(symbol)
        if existing:
            raise ValueError(
                f"Cannot open {direction} {symbol}: already have open position #{existing['id']}"
            )

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
        tp_str = f"{take_profit:g}" if take_profit else "None"
        logger.info(
            "Position opened #%d: %s %s qty=%.6f @ %g SL=%g TP=%s lev=%dx",
            pos_id, direction, symbol, quantity, entry_price,
            stop_loss or 0, tp_str, leverage,
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
            "Position closed #%d: %s %s @ %g PnL=%.4f (%s)",
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

            # Grace period: skip recently opened positions
            grace = self._rc.sl_tp_grace_seconds if self._rc else 30
            if grace > 0:
                opened_at = pos.get("opened_at")
                if opened_at:
                    opened = datetime.fromisoformat(opened_at.replace("Z", "+00:00"))
                    age_seconds = (datetime.now(timezone.utc) - opened).total_seconds()
                    if age_seconds < grace:
                        continue

            # Partial take profit check (before trailing update)
            if self._rc and self._rc.partial_tp_enabled and not pos.get("partial_closed"):
                entry = pos["entry_price"]
                if direction == "LONG":
                    gain_pct = (price - entry) / entry * 100 if entry > 0 else 0
                else:
                    gain_pct = (entry - price) / entry * 100 if entry > 0 else 0
                if gain_pct >= self._rc.partial_tp_trigger_pct:
                    to_close.append({
                        "position": pos,
                        "action": "PARTIAL_CLOSE",
                        "reason": f"partial_tp ({gain_pct:.2f}% >= {self._rc.partial_tp_trigger_pct}%)",
                    })
                    continue  # skip full SL/TP check this cycle

            # Update trailing stop
            await self._update_trailing_stop(pos, price)

            # Re-read position after trailing update
            pos = await self._get_by_id(pos["id"]) or pos

            # Check stop loss (includes trailing SL)
            effective_sl = pos.get("trailing_sl") or pos.get("stop_loss")
            if effective_sl is not None:
                sl_triggered = False
                if direction == "LONG" and price <= effective_sl:
                    sl_triggered = True
                elif direction == "SHORT" and price >= effective_sl:
                    sl_triggered = True

                if sl_triggered:
                    reason = f"stop_loss ({price:g} {'<=' if direction == 'LONG' else '>='} {effective_sl:g})"
                    if pos.get("trailing_sl") and pos.get("original_sl"):
                        if direction == "LONG" and pos["trailing_sl"] > pos["original_sl"]:
                            reason = f"trailing_sl ({price:g} <= {pos['trailing_sl']:g})"
                        elif direction == "SHORT" and pos["trailing_sl"] < pos["original_sl"]:
                            reason = f"trailing_sl ({price:g} >= {pos['trailing_sl']:g})"
                    to_close.append({
                        "position": pos,
                        "action": "CLOSE",
                        "reason": reason,
                    })
                    continue

            # Check take profit (direction-aware)
            if pos["take_profit"] is not None and pos["take_profit"] > 0:
                tp_triggered = False
                if direction == "LONG" and price >= pos["take_profit"]:
                    tp_triggered = True
                elif direction == "SHORT" and price <= pos["take_profit"]:
                    tp_triggered = True

                if tp_triggered:
                    to_close.append({
                        "position": pos,
                        "action": "CLOSE",
                        "reason": f"take_profit ({price:g} {'<=' if direction == 'SHORT' else '>='} {pos['take_profit']:g})",
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

        # ATR-based trailing distances with fixed % fallback
        atr = self._get_atr_trailing_distance(pos["symbol"], current_price)
        trail_pct = atr[0] if atr else self._rc.trailing_distance_pct
        tight_pct = atr[1] if atr else self._rc.trailing_tight_distance_pct

        new_trailing = old_trailing

        if gain_pct >= self._rc.trailing_tight_pct:
            new_trailing = new_max * (1 - tight_pct / 100)
        elif gain_pct >= self._rc.trailing_start_pct:
            new_trailing = new_max * (1 - trail_pct / 100)
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
                    "Trailing SL updated #%d %s LONG: %g → %g (gain=%.2f%%, max=%g)",
                    pos["id"], pos["symbol"], old_trailing, new_trailing, gain_pct, new_max,
                )
                if self._cycle_count > 0:
                    try:
                        await self._db.insert_event(
                            cycle=self._cycle_count,
                            symbol=pos["symbol"],
                            event_type="TRAILING_UPDATE",
                            source="position_tracker",
                            details={
                                "direction": "LONG",
                                "old_sl": round(old_trailing, 6),
                                "new_sl": round(new_trailing, 6),
                                "gain_pct": round(gain_pct, 2),
                                "max_seen": round(new_max, 6),
                            },
                            position_id=pos["id"],
                        )
                    except Exception:
                        logger.debug("Failed to log TRAILING_UPDATE event", exc_info=True)

    async def _update_trailing_short(self, pos: dict[str, Any], current_price: float, entry: float) -> None:
        """Trailing stop for SHORT: SL moves down as price drops."""
        min_seen = pos.get("min_price_seen") or entry
        old_trailing = pos.get("trailing_sl") or pos.get("stop_loss") or float("inf")

        new_min = min(min_seen, current_price)
        gain_pct = (entry - new_min) / entry * 100 if entry > 0 else 0

        # ATR-based trailing distances with fixed % fallback
        atr = self._get_atr_trailing_distance(pos["symbol"], current_price)
        trail_pct = atr[0] if atr else self._rc.trailing_distance_pct
        tight_pct = atr[1] if atr else self._rc.trailing_tight_distance_pct

        new_trailing = old_trailing

        if gain_pct >= self._rc.trailing_tight_pct:
            new_trailing = new_min * (1 + tight_pct / 100)
        elif gain_pct >= self._rc.trailing_start_pct:
            new_trailing = new_min * (1 + trail_pct / 100)
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
                    "Trailing SL updated #%d %s SHORT: %g → %g (gain=%.2f%%, min=%g)",
                    pos["id"], pos["symbol"], old_trailing, new_trailing, gain_pct, new_min,
                )
                if self._cycle_count > 0:
                    try:
                        await self._db.insert_event(
                            cycle=self._cycle_count,
                            symbol=pos["symbol"],
                            event_type="TRAILING_UPDATE",
                            source="position_tracker",
                            details={
                                "direction": "SHORT",
                                "old_sl": round(old_trailing, 6),
                                "new_sl": round(new_trailing, 6),
                                "gain_pct": round(gain_pct, 2),
                                "min_seen": round(new_min, 6),
                            },
                            position_id=pos["id"],
                        )
                    except Exception:
                        logger.debug("Failed to log TRAILING_UPDATE event", exc_info=True)

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

    async def scale_position(
        self,
        position_id: int,
        additional_qty: float,
        fill_price: float,
    ) -> None:
        """Scale up an existing position: VWAP entry price, sum quantity."""
        pos = await self._get_by_id(position_id)
        if not pos:
            raise ValueError(f"Position #{position_id} not found")
        if pos["status"] != "OPEN":
            raise ValueError(f"Position #{position_id} is not OPEN")

        old_qty = pos["quantity"]
        old_entry = pos["entry_price"]

        # VWAP entry price
        new_qty = old_qty + additional_qty
        new_entry = (old_entry * old_qty + fill_price * additional_qty) / new_qty

        await self._db.db.execute(
            "UPDATE positions SET entry_price=?, quantity=? WHERE id=?",
            (new_entry, new_qty, position_id),
        )
        await self._db.db.commit()

        logger.info(
            "Position scaled #%d %s: qty %.6f→%.6f, entry %g→%g (added %.6f @ %g)",
            position_id, pos["symbol"], old_qty, new_qty, old_entry, new_entry,
            additional_qty, fill_price,
        )

    async def partial_close(
        self,
        position_id: int,
        close_pct: float,
        exit_price: float,
        reason: str = "partial_tp",
    ) -> tuple[float, float, int]:
        """Partially close a position.

        1. Close original position with PnL on closed portion
        2. Open new position for remaining quantity (breakeven SL)

        Returns (pnl_net, closed_qty, new_position_id).
        """
        pos = await self._get_by_id(position_id)
        if not pos:
            raise ValueError(f"Position #{position_id} not found")
        if pos["status"] != "OPEN":
            raise ValueError(f"Position #{position_id} is not OPEN")

        direction = pos.get("direction", "LONG")
        entry = pos["entry_price"]
        total_qty = pos["quantity"]
        closed_qty = round(total_qty * close_pct / 100, 8)
        remaining_qty = round(total_qty - closed_qty, 8)

        if remaining_qty <= 0 or closed_qty <= 0:
            raise ValueError(f"Invalid partial close: closed={closed_qty}, remaining={remaining_qty}")

        # PnL on closed portion (direction-aware)
        if direction == "LONG":
            pnl = (exit_price - entry) * closed_qty
        else:
            pnl = (entry - exit_price) * closed_qty
        fee_estimate = (entry + exit_price) * closed_qty * FEE_PER_LEG
        pnl_net = pnl - fee_estimate

        # 1. Close original position
        await self._db.db.execute(
            """UPDATE positions SET status='CLOSED', exit_price=?, pnl=?,
               close_reason=?, closed_at=strftime('%Y-%m-%dT%H:%M:%SZ','now')
               WHERE id=?""",
            (exit_price, pnl_net, reason, position_id),
        )
        await self._db.db.commit()

        # 2. Open new position for remaining (raw INSERT — original is already CLOSED)
        cursor = await self._db.db.execute(
            """INSERT INTO positions
               (symbol, entry_price, quantity, stop_loss, take_profit, strategy,
                max_price_seen, min_price_seen, original_sl, trailing_sl,
                direction, leverage, partial_closed)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)""",
            (
                pos["symbol"], entry, remaining_qty,
                entry,  # breakeven SL
                None,   # no TP — let trailing handle it
                pos.get("strategy", "ai"),
                pos.get("max_price_seen") or entry,
                pos.get("min_price_seen") or entry,
                entry,  # original_sl = breakeven
                entry,  # trailing_sl = breakeven
                direction,
                pos.get("leverage", 1),
            ),
        )
        await self._db.db.commit()
        new_id = cursor.lastrowid

        logger.info(
            "Partial close #%d %s %s: closed %.6f @ %g PnL=%.4f, remaining %.6f → #%d (breakeven SL)",
            position_id, direction, pos["symbol"], closed_qty, exit_price,
            pnl_net, remaining_qty, new_id,
        )
        return (pnl_net, closed_qty, new_id)

    def _get_atr_trailing_distance(self, symbol: str, price: float) -> tuple[float, float] | None:
        """Compute ATR-based trailing distances for a symbol.

        Returns (trail_pct, tight_pct) or None if unavailable.
        Uses ATR(14) on 15m candles, cached for 5 minutes.
        """
        if not self._rc or not self._rc.use_atr_trailing or not self._market_data:
            return None

        # Check cache (5 min TTL)
        now = time.time()
        cached = self._atr_cache.get(symbol)
        if cached and (now - cached[0]) < 300:
            return (cached[1], cached[2])

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
            return None

        # Normal trail
        trail_dist = self._rc.atr_trailing_multiplier * atr_val
        trail_pct = trail_dist / price * 100 if price > 0 else 0
        trail_pct = max(self._rc.atr_trailing_min_pct, min(trail_pct, self._rc.atr_trailing_max_pct))

        # Tight trail
        tight_dist = self._rc.atr_trailing_tight_multiplier * atr_val
        tight_pct = tight_dist / price * 100 if price > 0 else 0
        tight_pct = max(self._rc.atr_trailing_min_pct, min(tight_pct, self._rc.atr_trailing_max_pct))

        self._atr_cache[symbol] = (now, trail_pct, tight_pct)
        return (trail_pct, tight_pct)

    async def update_sl_tp(
        self,
        position_id: int,
        stop_loss: float | None = None,
        take_profit: float | None = None,
    ) -> None:
        """Update SL/TP for an open position (AI adjustment)."""
        updates: list[str] = []
        params: list[Any] = []
        if stop_loss is not None:
            updates.append("stop_loss=?")
            params.append(stop_loss)
        if take_profit is not None:
            updates.append("take_profit=?")
            params.append(take_profit)
        if not updates:
            return
        params.append(position_id)
        await self._db.db.execute(
            f"UPDATE positions SET {', '.join(updates)} WHERE id=? AND status='OPEN'",
            params,
        )
        await self._db.db.commit()
        logger.info("AI adjusted position #%d: SL=%s TP=%s", position_id, stop_loss, take_profit)
        if self._cycle_count > 0:
            pos = await self._get_by_id(position_id)
            if pos:
                try:
                    await self._db.insert_event(
                        cycle=self._cycle_count,
                        symbol=pos["symbol"],
                        event_type="POSITION_ADJUSTED",
                        source="ai_advisor",
                        details={
                            "stop_loss": stop_loss,
                            "take_profit": take_profit,
                        },
                        position_id=position_id,
                    )
                except Exception:
                    logger.debug("Failed to log POSITION_ADJUSTED event", exc_info=True)

    async def update_leverage(self, position_id: int, leverage: int) -> None:
        """Update leverage for an open position (AI adjustment)."""
        await self._db.db.execute(
            "UPDATE positions SET leverage=? WHERE id=? AND status='OPEN'",
            (leverage, position_id),
        )
        await self._db.db.commit()
        logger.info("AI adjusted position #%d: leverage=%dx", position_id, leverage)

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
