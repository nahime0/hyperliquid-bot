"""Test connection to Binance testnet.

Usage:
    python -m scripts.test_connection
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import load_settings
from core.client import BinanceClient
from utils.logger import setup_logging, get_logger

logger = get_logger(__name__)


async def main() -> None:
    settings = load_settings()
    setup_logging(settings.log_level)

    mode = "TESTNET" if settings.binance.testnet else "LIVE"
    logger.info("=== Binance Connection Test (%s) ===", mode)

    client = BinanceClient(settings)
    try:
        await client.connect()

        # 1. Server time
        server_time = await client.get_server_time()
        logger.info("Server time: %s", server_time)

        # 2. Price check
        price = await client.get_price("BTCUSDC")
        logger.info("BTC/USDC price: %.2f", price)

        price_eth = await client.get_price("ETHUSDC")
        logger.info("ETH/USDC price: %.2f", price_eth)

        # 3. Klines
        klines = await client.get_klines("BTCUSDC", "1h", limit=5)
        logger.info("Got %d klines for BTCUSDC 1h", len(klines))

        # 4. Order book
        depth = await client.get_order_book("BTCUSDC", limit=5)
        logger.info(
            "Order book BTCUSDC — top bid: %s, top ask: %s",
            depth["bids"][0] if depth["bids"] else "N/A",
            depth["asks"][0] if depth["asks"] else "N/A",
        )

        # 5. Account balance (requires valid API keys)
        try:
            balances = await client.get_account_balance()
            logger.info("Account balances: %s", balances)
        except Exception as exc:
            logger.warning("Could not fetch account balance (API keys may be invalid): %s", exc)

        # 6. Symbol info
        info = await client.get_symbol_info("BTCUSDC")
        if info:
            logger.info("BTCUSDC status: %s", info.get("status"))

        logger.info("=== All tests passed! ===")

    except Exception as exc:
        logger.error("Connection test failed: %s", exc)
        raise
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
