"""Integration test for GridStrategy on Binance testnet.

Usage:
    .venv/bin/python -m scripts.test_grid
"""
from __future__ import annotations

import asyncio

from config.settings import load_settings
from core.client import BinanceClient
from data.db import Database
from strategies.grid import GridStrategy
from utils.logger import setup_logging, get_logger

logger = get_logger(__name__)

SYMBOL = "ETHUSDC"
RANGE_PCT = 2.0
SPACING_PCT = 0.5
QTY_USDC = 100.0


async def main() -> None:
    settings = load_settings()
    setup_logging(settings.log_level)

    if not settings.binance.testnet:
        logger.error("This test must run on TESTNET. Set BINANCE_TESTNET=true")
        return

    client = BinanceClient(settings)
    db = Database(settings.db_path)

    await client.connect()
    await db.connect()

    grid = GridStrategy(client, db)

    try:
        # 1. Start — load symbol rules
        print("\n=== 1. start() — loading symbol rules ===")
        await grid.start()
        print(f"Symbol rules loaded: {list(grid._symbol_rules.keys())}")

        # 2. Get current price
        price = await client.get_price(SYMBOL)
        print(f"\n{SYMBOL} current price: {price}")

        # 3. Activate grid
        print(f"\n=== 2. activate({SYMBOL}, range={RANGE_PCT}%, spacing={SPACING_PCT}%, qty={QTY_USDC} USDC) ===")
        await grid.activate(SYMBOL, RANGE_PCT, SPACING_PCT, QTY_USDC)

        # 4. Show state
        print("\n=== 3. get_state() ===")
        state = grid.get_state()
        grid_state = state["grids"].get(SYMBOL, {})
        print(f"Active symbols: {state['active_symbols']}")
        print(f"Center price: {grid_state.get('center_price')}")
        print(f"Levels: {grid_state.get('num_levels')}")
        print(f"Cycles: {grid_state.get('total_cycles')}")
        for lv in grid_state.get("levels", []):
            print(f"  {lv['side']:4s} @ {lv['price']:<12} status={lv['status']}  order_id={lv['order_id']}")

        # 5. Verify orders on exchange
        print("\n=== 4. Verifying orders on exchange ===")
        open_orders = await client.get_open_orders(SYMBOL)
        grid_order_ids = {lv["order_id"] for lv in grid_state.get("levels", []) if lv["order_id"]}
        exchange_ids = {int(o["orderId"]) for o in open_orders}
        matched = grid_order_ids & exchange_ids
        print(f"Grid orders: {len(grid_order_ids)}")
        print(f"Exchange open orders for {SYMBOL}: {len(exchange_ids)}")
        print(f"Matched: {len(matched)}")
        assert matched == grid_order_ids, f"Mismatch! Grid IDs not on exchange: {grid_order_ids - exchange_ids}"
        print("All grid orders confirmed on exchange.")

        # 6. Run update cycle
        print("\n=== 5. update() — checking fills ===")
        await grid.update()
        print("Update completed (no fills expected on fresh grid)")

        # 7. Deactivate
        print(f"\n=== 6. deactivate({SYMBOL}) ===")
        await grid.deactivate(SYMBOL)

        # 8. Verify orders cancelled
        print("\n=== 7. Verifying orders cancelled ===")
        open_orders_after = await client.get_open_orders(SYMBOL)
        remaining = {int(o["orderId"]) for o in open_orders_after} & grid_order_ids
        print(f"Remaining grid orders on exchange: {len(remaining)}")
        assert len(remaining) == 0, f"Orders not cancelled: {remaining}"
        print("All grid orders cancelled successfully.")

        # 9. Final state
        print("\n=== 8. Final state ===")
        print(grid.get_state())
        print("\nAll tests passed!")

    except Exception:
        logger.exception("Test failed")
        # Cleanup: try to deactivate
        if SYMBOL in grid._active_grids:
            await grid.deactivate(SYMBOL)
    finally:
        await grid.stop()
        await client.close()
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
