"""Trading coin configuration for Hyperliquid perpetuals.

Uses coin names (ETH, BTC) instead of Binance pairs (ETHUSDC, BTCUSDC).
Supports dynamic discovery via Hyperliquid meta() API.
"""
from __future__ import annotations

from typing import Any

from utils.logger import get_logger

logger = get_logger(__name__)

# Core coins — always included
CORE_COINS: list[str] = ["BTC", "ETH", "SOL"]

# Top 50 coins by 24h volume — used for backtesting and fallback
ALL_COINS: list[str] = [
    "BTC", "ETH", "SOL", "HYPE", "ZRO", "XRP", "UNI", "ZEC",
    "ASTER", "PAXG", "FARTCOIN", "SUI", "MON", "BNB", "DOGE",
    "PUMP", "BERA", "RESOLV", "XPL", "LIT", "AVAX", "AAVE",
    "XMR", "AXS", "LINK", "kPEPE", "BCH", "LTC", "WLFI", "ADA",
    "TRUMP", "CRV", "ENA", "TAO", "STABLE", "ARB", "POL",
    "VIRTUAL", "WIF", "PENGU", "STBL", "NEAR", "kBONK", "SKR",
    "kSHIB", "APT", "JUP", "WLD", "CC", "NIL",
]

# Default intervals for candle data
DEFAULT_INTERVALS: list[str] = ["5m", "15m", "1h"]


async def discover_perp_coins(
    client: Any,
    *,
    min_volume_24h: float = 50_000.0,
    max_coins: int = 60,
) -> list[str]:
    """Discover perpetual coins from Hyperliquid meta().

    Calls info.meta() to get universe of assets, then filters
    by those with active mid prices.

    Args:
        client: HyperliquidClient instance (already connected).
        min_volume_24h: Minimum 24h volume in USDC (not used directly,
            but we filter by active mid prices as a proxy).
        max_coins: Maximum number of coins to return.

    Returns:
        Sorted list of coin names (e.g. ["BTC", "ETH", "SOL", ...]).
    """
    try:
        meta = client.get_meta()
        universe = meta.get("universe", [])
        if not universe:
            logger.warning("No assets in meta() universe — using fallback")
            return ALL_COINS

        # Get all mid prices to filter active assets
        all_mids = await client.get_all_mids()

        # Filter: only coins with an active mid price
        active_coins: list[str] = []
        for asset in universe:
            coin = asset["name"]
            if coin in all_mids and float(all_mids[coin]) > 0:
                active_coins.append(coin)

        if not active_coins:
            logger.warning("No active coins found — using fallback")
            return ALL_COINS

        logger.info("Meta universe: %d assets, %d with active mid prices", len(universe), len(active_coins))

        # Take top N (universe is already ordered by Hyperliquid internally)
        result = active_coins[:max_coins]

        # Ensure CORE_COINS are always included
        for core in CORE_COINS:
            if core in all_mids and core not in result:
                result.append(core)

        result.sort()

        logger.info(
            "Discovered %d perpetual coins (from %d active, max=%d)",
            len(result), len(active_coins), max_coins,
        )

        if len(result) < 5:
            logger.warning("Very few coins discovered (%d) — check filters", len(result))

        return result

    except Exception:
        logger.exception("Coin discovery failed — using static fallback (%d coins)", len(ALL_COINS))
        return ALL_COINS
