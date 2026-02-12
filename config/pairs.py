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
    """Discover perpetual coins from Hyperliquid, filtered by 24h volume.

    Uses meta_and_asset_ctxs() to get real 24h notional volume (dayNtlVlm)
    for each asset. Coins below min_volume_24h are excluded.

    Args:
        client: HyperliquidClient instance (already connected).
        min_volume_24h: Minimum 24h notional volume in USDC.
        max_coins: Maximum number of coins to return.

    Returns:
        List of coin names sorted by 24h volume (descending).
    """
    try:
        meta, asset_ctxs = await client.get_meta_and_asset_ctxs()
        universe = meta.get("universe", [])
        if not universe:
            logger.warning("No assets in meta() universe — using fallback")
            return ALL_COINS

        if len(universe) != len(asset_ctxs):
            logger.warning(
                "Universe/asset_ctxs length mismatch (%d vs %d) — using fallback",
                len(universe), len(asset_ctxs),
            )
            return ALL_COINS

        # Zip universe with asset contexts and filter by 24h volume
        coins_with_volume: list[tuple[str, float]] = []
        for asset, ctx in zip(universe, asset_ctxs):
            coin = asset["name"]
            try:
                day_volume = float(ctx.get("dayNtlVlm", 0))
            except (ValueError, TypeError):
                day_volume = 0.0
            if day_volume >= min_volume_24h:
                coins_with_volume.append((coin, day_volume))

        total_active = len(coins_with_volume)

        if not coins_with_volume:
            logger.warning(
                "No coins passed volume filter (min $%.0f) — using fallback",
                min_volume_24h,
            )
            return ALL_COINS

        # Sort by volume descending (most liquid first)
        coins_with_volume.sort(key=lambda x: x[1], reverse=True)

        # Take top N
        result = [coin for coin, _ in coins_with_volume[:max_coins]]

        # CORE_COINS included only if they passed the volume filter
        volume_set = {coin for coin, _ in coins_with_volume}
        for core in CORE_COINS:
            if core in volume_set and core not in result:
                result.append(core)

        logger.info(
            "Filtered %d coins by volume (min $%.0f), kept %d (max=%d)",
            len(universe), min_volume_24h, len(result), max_coins,
        )

        if len(result) < 5:
            logger.warning("Very few coins passed volume filter (%d) — check min_volume_24h", len(result))

        return result

    except Exception:
        logger.exception("Coin discovery failed — using static fallback (%d coins)", len(ALL_COINS))
        return ALL_COINS
