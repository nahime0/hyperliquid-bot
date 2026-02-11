"""Grid Trading Strategy.

Places a grid of LIMIT BUY orders below the current price.  When a BUY fills,
a corresponding SELL is placed one spacing level above.  When that SELL fills,
the cycle completes and a new BUY is placed back at the original level.

The AI engine decides *when* to activate/deactivate grids and with *which*
parameters.  Once active, the grid operates mechanically.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_DOWN
from typing import Any

from binance import BinanceAPIException

from config.pairs import GRID_PAIRS
from core.client import BinanceClient
from data.db import Database
from strategies.base import Strategy
from utils.logger import get_logger

logger = get_logger(__name__)

# Fee with BNB discount (maker)
FEE_RATE = Decimal("0.00075")


# ── Data classes ─────────────────────────────────────────────


@dataclass
class GridLevel:
    """Single price level in the grid."""
    price: Decimal
    side: str  # "BUY" or "SELL"
    order_id: int | None = None
    status: str = "empty"  # empty / placed / filled


@dataclass
class GridConfig:
    """Immutable configuration for one grid."""
    symbol: str
    center_price: Decimal
    range_pct: Decimal
    spacing_pct: Decimal
    num_levels: int
    quantity_usdt: Decimal


@dataclass
class ActiveGrid:
    """Runtime state for an active grid."""
    config: GridConfig
    levels: list[GridLevel] = field(default_factory=list)
    total_cycles: int = 0
    total_pnl: Decimal = Decimal("0")
    activated_at: float = field(default_factory=time.time)


# ── Symbol info cache ────────────────────────────────────────


@dataclass
class SymbolRules:
    """Cached exchange filters for a symbol."""
    tick_size: Decimal     # PRICE_FILTER stepSize
    step_size: Decimal     # LOT_SIZE stepSize
    min_notional: Decimal  # NOTIONAL / MIN_NOTIONAL
    min_qty: Decimal       # LOT_SIZE minQty


# ── GridStrategy ─────────────────────────────────────────────


class GridStrategy(Strategy):
    """Grid trading strategy that places layered limit orders."""

    def __init__(self, client: BinanceClient, db: Database) -> None:
        self._client = client
        self._db = db
        self._symbol_rules: dict[str, SymbolRules] = {}
        self._active_grids: dict[str, ActiveGrid] = {}

    # ── Lifecycle ────────────────────────────────────────────

    async def start(self) -> None:
        """Load exchange info (tick_size, step_size, min_notional) for grid pairs."""
        for symbol in GRID_PAIRS:
            try:
                info = await self._client.get_symbol_info(symbol)
                if info is None:
                    logger.warning("Symbol %s not found on exchange", symbol)
                    continue
                rules = self._parse_symbol_info(info)
                self._symbol_rules[symbol] = rules
                logger.info(
                    "Loaded rules for %s: tick=%s step=%s min_notional=%s",
                    symbol, rules.tick_size, rules.step_size, rules.min_notional,
                )
            except Exception:
                logger.exception("Failed to load symbol info for %s", symbol)
        logger.info("GridStrategy started — %d symbols ready", len(self._symbol_rules))

    async def stop(self) -> None:
        """Deactivate all grids and cancel open orders."""
        symbols = list(self._active_grids.keys())
        for symbol in symbols:
            await self.deactivate(symbol)
        logger.info("GridStrategy stopped")

    # ── Activate / Deactivate ────────────────────────────────

    async def activate(
        self,
        symbol: str,
        range_pct: float,
        spacing_pct: float,
        quantity_usdt: float,
    ) -> None:
        """Activate a grid for *symbol*.

        - Fetches current price as center
        - Computes buy levels below center
        - Places LIMIT BUY orders on Binance
        """
        if symbol in self._active_grids:
            logger.warning("Grid already active for %s — deactivate first", symbol)
            return

        if symbol not in self._symbol_rules:
            logger.error("No symbol rules for %s — call start() first", symbol)
            return

        rules = self._symbol_rules[symbol]
        center = Decimal(str(await self._client.get_price(symbol)))
        range_d = Decimal(str(range_pct))
        spacing_d = Decimal(str(spacing_pct))
        qty_usdt = Decimal(str(quantity_usdt))

        config = GridConfig(
            symbol=symbol,
            center_price=center,
            range_pct=range_d,
            spacing_pct=spacing_d,
            num_levels=0,  # filled below
            quantity_usdt=qty_usdt,
        )

        # Calculate buy levels below center
        buy_prices = self._calculate_buy_levels(center, range_d, spacing_d, rules.tick_size)
        if not buy_prices:
            logger.error("No valid buy levels for %s", symbol)
            return

        config = GridConfig(
            symbol=symbol,
            center_price=center,
            range_pct=range_d,
            spacing_pct=spacing_d,
            num_levels=len(buy_prices),
            quantity_usdt=qty_usdt,
        )

        usdt_per_level = qty_usdt / len(buy_prices)

        grid = ActiveGrid(config=config)

        # Place BUY orders
        for price in buy_prices:
            qty = self._round_qty(usdt_per_level / price, rules.step_size)
            if qty < rules.min_qty:
                logger.debug("Skipping level %s — qty %s < min %s", price, qty, rules.min_qty)
                continue
            notional = price * qty
            if notional < rules.min_notional:
                logger.debug("Skipping level %s — notional %s < min %s", price, notional, rules.min_notional)
                continue

            level = GridLevel(price=price, side="BUY")
            try:
                order = await self._client.place_limit_order(
                    symbol=symbol,
                    side="BUY",
                    quantity=float(qty),
                    price=float(price),
                )
                level.order_id = int(order["orderId"])
                level.status = "placed"
                # Track in DB
                await self._db.upsert_order(
                    order_id=str(level.order_id),
                    symbol=symbol,
                    side="BUY",
                    order_type="LIMIT",
                    quantity=float(qty),
                    price=float(price),
                    status="NEW",
                    strategy="grid",
                )
            except Exception:
                logger.exception("Failed to place BUY at %s for %s", price, symbol)
                level.status = "empty"

            grid.levels.append(level)

        self._active_grids[symbol] = grid
        placed = sum(1 for lv in grid.levels if lv.status == "placed")
        logger.info(
            "Grid activated for %s — center=%s levels=%d placed=%d",
            symbol, center, len(grid.levels), placed,
        )

    async def deactivate(self, symbol: str) -> None:
        """Cancel all open orders for a grid and remove it."""
        grid = self._active_grids.pop(symbol, None)
        if grid is None:
            logger.warning("No active grid for %s", symbol)
            return

        cancelled = 0
        for level in grid.levels:
            if level.order_id and level.status == "placed":
                try:
                    await self._client.cancel_order(symbol, level.order_id)
                    await self._db.update_order_status(str(level.order_id), "CANCELED")
                    cancelled += 1
                except BinanceAPIException as exc:
                    if exc.code == -2011:  # Unknown order — already filled/cancelled
                        logger.debug("Order %s already gone", level.order_id)
                    else:
                        logger.warning("Failed to cancel order %s: %s", level.order_id, exc.message)
                except Exception:
                    logger.exception("Error cancelling order %s", level.order_id)

        logger.info(
            "Grid deactivated for %s — cancelled=%d cycles=%d pnl=%.4f",
            symbol, cancelled, grid.total_cycles, grid.total_pnl,
        )

    # ── Update (called every tick) ───────────────────────────

    async def update(self) -> None:
        """Check for filled orders and rotate the grid.

        For each active grid:
        1. Fetch open orders from Binance
        2. Detect filled BUYs → place SELL one spacing above
        3. Detect filled SELLs → log cycle, place BUY one spacing below
        4. Trailing: if price drifts outside range, re-center the grid
        """
        for symbol in list(self._active_grids.keys()):
            try:
                await self._update_grid(symbol)
            except Exception:
                logger.exception("Error updating grid for %s", symbol)

    async def _update_grid(self, symbol: str) -> None:
        grid = self._active_grids[symbol]
        rules = self._symbol_rules[symbol]

        # Build set of currently open order IDs on Binance
        exchange_orders = await self._client.get_open_orders(symbol)
        open_ids: set[int] = {int(o["orderId"]) for o in exchange_orders}

        for level in grid.levels:
            if level.order_id is None or level.status != "placed":
                continue

            if level.order_id in open_ids:
                continue  # still open — nothing to do

            # Order no longer open → filled (or cancelled externally)
            level.status = "filled"
            await self._db.update_order_status(str(level.order_id), "FILLED")

            if level.side == "BUY":
                # BUY filled → place SELL one spacing above
                sell_price = self._round_price(
                    level.price * (1 + grid.config.spacing_pct / 100),
                    rules.tick_size,
                )
                qty = self._round_qty(
                    grid.config.quantity_usdt / grid.config.num_levels / level.price,
                    rules.step_size,
                )
                if qty < rules.min_qty:
                    logger.debug("SELL qty too small at %s", sell_price)
                    continue
                try:
                    order = await self._client.place_limit_order(
                        symbol=symbol,
                        side="SELL",
                        quantity=float(qty),
                        price=float(sell_price),
                    )
                    level.side = "SELL"
                    level.order_id = int(order["orderId"])
                    level.status = "placed"
                    level.price = sell_price
                    await self._db.upsert_order(
                        order_id=str(level.order_id),
                        symbol=symbol,
                        side="SELL",
                        order_type="LIMIT",
                        quantity=float(qty),
                        price=float(sell_price),
                        status="NEW",
                        strategy="grid",
                    )
                    logger.info("BUY filled → SELL placed at %s for %s", sell_price, symbol)
                except Exception:
                    logger.exception("Failed to place SELL at %s for %s", sell_price, symbol)

            elif level.side == "SELL":
                # SELL filled → cycle complete.  Log PnL, place BUY back below.
                buy_price = self._round_price(
                    level.price / (1 + grid.config.spacing_pct / 100),
                    rules.tick_size,
                )
                qty = self._round_qty(
                    grid.config.quantity_usdt / grid.config.num_levels / buy_price,
                    rules.step_size,
                )
                # Log the completed cycle
                await self._log_cycle(symbol, buy_price, level.price, qty, rules)
                grid.total_cycles += 1

                if qty < rules.min_qty:
                    logger.debug("BUY qty too small at %s", buy_price)
                    continue
                try:
                    order = await self._client.place_limit_order(
                        symbol=symbol,
                        side="BUY",
                        quantity=float(qty),
                        price=float(buy_price),
                    )
                    level.side = "BUY"
                    level.order_id = int(order["orderId"])
                    level.status = "placed"
                    level.price = buy_price
                    await self._db.upsert_order(
                        order_id=str(level.order_id),
                        symbol=symbol,
                        side="BUY",
                        order_type="LIMIT",
                        quantity=float(qty),
                        price=float(buy_price),
                        status="NEW",
                        strategy="grid",
                    )
                    logger.info("SELL filled → cycle done → BUY placed at %s for %s", buy_price, symbol)
                except Exception:
                    logger.exception("Failed to place BUY at %s for %s", buy_price, symbol)

        # ── Trailing check: re-center if price left the range ──
        await self._trailing_check(symbol)

    async def _trailing_check(self, symbol: str) -> None:
        """If price moved outside the grid range, cancel everything and re-center."""
        grid = self._active_grids.get(symbol)
        if grid is None:
            return

        current = Decimal(str(await self._client.get_price(symbol)))
        upper = grid.config.center_price * (1 + grid.config.range_pct / 100)
        lower = grid.config.center_price * (1 - grid.config.range_pct / 100)

        if lower <= current <= upper:
            return  # still in range

        logger.info(
            "Price %s outside grid range [%s, %s] for %s — re-centering",
            current, lower, upper, symbol,
        )

        # Preserve config, re-activate with new center
        cfg = grid.config
        await self.deactivate(symbol)
        await self.activate(
            symbol=cfg.symbol,
            range_pct=float(cfg.range_pct),
            spacing_pct=float(cfg.spacing_pct),
            quantity_usdt=float(cfg.quantity_usdt),
        )

    # ── State (for AI snapshot) ──────────────────────────────

    def get_state(self) -> dict[str, Any]:
        """Return current grid state for the AI market snapshot."""
        grids: dict[str, Any] = {}
        for symbol, grid in self._active_grids.items():
            levels = []
            for lv in grid.levels:
                levels.append({
                    "price": float(lv.price),
                    "side": lv.side,
                    "order_id": lv.order_id,
                    "status": lv.status,
                })
            grids[symbol] = {
                "center_price": float(grid.config.center_price),
                "range_pct": float(grid.config.range_pct),
                "spacing_pct": float(grid.config.spacing_pct),
                "num_levels": grid.config.num_levels,
                "quantity_usdt": float(grid.config.quantity_usdt),
                "total_cycles": grid.total_cycles,
                "total_pnl": float(grid.total_pnl),
                "activated_at": grid.activated_at,
                "levels": levels,
            }
        return {
            "strategy": "grid",
            "active_symbols": list(self._active_grids.keys()),
            "grids": grids,
        }

    # ── Helpers (private) ────────────────────────────────────

    @staticmethod
    def _parse_symbol_info(info: dict[str, Any]) -> SymbolRules:
        """Extract tick_size, step_size, min_notional from exchange info."""
        tick_size = Decimal("0.01")
        step_size = Decimal("0.001")
        min_notional = Decimal("10")
        min_qty = Decimal("0.001")

        for f in info.get("filters", []):
            ft = f["filterType"]
            if ft == "PRICE_FILTER":
                tick_size = Decimal(f["tickSize"])
            elif ft == "LOT_SIZE":
                step_size = Decimal(f["stepSize"])
                min_qty = Decimal(f["minQty"])
            elif ft in ("NOTIONAL", "MIN_NOTIONAL"):
                min_notional = Decimal(f.get("minNotional", f.get("notional", "10")))

        return SymbolRules(
            tick_size=tick_size,
            step_size=step_size,
            min_notional=min_notional,
            min_qty=min_qty,
        )

    @staticmethod
    def _round_price(price: Decimal, tick_size: Decimal) -> Decimal:
        """Round price down to nearest tick_size."""
        return (price / tick_size).to_integral_value(rounding=ROUND_DOWN) * tick_size

    @staticmethod
    def _round_qty(qty: Decimal, step_size: Decimal) -> Decimal:
        """Round quantity down to nearest step_size."""
        return (qty / step_size).to_integral_value(rounding=ROUND_DOWN) * step_size

    @staticmethod
    def _calculate_buy_levels(
        center: Decimal,
        range_pct: Decimal,
        spacing_pct: Decimal,
        tick_size: Decimal,
    ) -> list[Decimal]:
        """Compute buy-level prices below center, spaced by spacing_pct."""
        levels: list[Decimal] = []
        lower_bound = center * (1 - range_pct / 100)
        spacing_mult = 1 - spacing_pct / 100
        price = center * spacing_mult  # first level one spacing below center
        while price >= lower_bound:
            rounded = (price / tick_size).to_integral_value(rounding=ROUND_DOWN) * tick_size
            if rounded > 0:
                levels.append(rounded)
            price *= spacing_mult
        return levels

    async def _log_cycle(
        self,
        symbol: str,
        buy_price: Decimal,
        sell_price: Decimal,
        qty: Decimal,
        rules: SymbolRules,
    ) -> None:
        """Log a completed grid cycle to the DB and update running PnL."""
        gross = qty * (sell_price - buy_price)
        fee_buy = qty * buy_price * FEE_RATE
        fee_sell = qty * sell_price * FEE_RATE
        net_pnl = gross - fee_buy - fee_sell

        grid = self._active_grids.get(symbol)
        if grid:
            grid.total_pnl += net_pnl

        await self._db.insert_trade(
            symbol=symbol,
            side="SELL",
            price=float(sell_price),
            quantity=float(qty),
            fee=float(fee_buy + fee_sell),
            fee_asset="USDC",
            pnl=float(net_pnl),
            strategy="grid",
            notes=f"grid cycle buy@{buy_price} sell@{sell_price}",
        )
        logger.info(
            "Grid cycle %s: buy=%s sell=%s qty=%s pnl=%.4f (fee=%.4f)",
            symbol, buy_price, sell_price, qty, net_pnl, fee_buy + fee_sell,
        )
