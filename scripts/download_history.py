"""Download historical klines from Binance and save as CSV.

Usage:
    # Download all pairs (default 6 months, 15m interval)
    .venv/bin/python -m scripts.download_history

    # Custom settings
    .venv/bin/python -m scripts.download_history --months 12 --interval 1h --symbols ETHUSDC BTCUSDC

Data is saved to  data/history/<symbol>_<interval>.csv
"""
from __future__ import annotations

import argparse
import asyncio
import csv
from datetime import datetime, timezone
from pathlib import Path

from binance import AsyncClient

from config.pairs import ALL_PAIRS
from utils.logger import setup_logging, get_logger

logger = get_logger(__name__)

HISTORY_DIR = Path(__file__).resolve().parent.parent / "data" / "history"

# Kline column names
COLUMNS = [
    "open_time", "open", "high", "low", "close", "volume",
    "close_time", "quote_volume", "trades",
    "taker_buy_base", "taker_buy_quote", "ignore",
]


async def download_klines(
    symbol: str,
    interval: str,
    months: int,
) -> Path:
    """Download klines for one symbol and save to CSV.

    Uses the public API (no auth needed) so it works without keys.
    """
    start_str = f"{months} months ago UTC"
    out_path = HISTORY_DIR / f"{symbol}_{interval}.csv"

    logger.info("Downloading %s %s (last %d months)...", symbol, interval, months)

    client = await AsyncClient.create()
    try:
        klines = await client.get_historical_klines(
            symbol=symbol,
            interval=interval,
            start_str=start_str,
        )
    finally:
        await client.close_connection()

    if not klines:
        logger.warning("No data returned for %s", symbol)
        return out_path

    # Write CSV
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(COLUMNS)
        writer.writerows(klines)

    # Summary
    first_ts = datetime.fromtimestamp(klines[0][0] / 1000, tz=timezone.utc)
    last_ts = datetime.fromtimestamp(klines[-1][0] / 1000, tz=timezone.utc)
    logger.info(
        "  %s: %d candles, %s → %s  → %s",
        symbol, len(klines),
        first_ts.strftime("%Y-%m-%d"),
        last_ts.strftime("%Y-%m-%d"),
        out_path.name,
    )
    return out_path


async def download_all(
    symbols: list[str],
    interval: str,
    months: int,
) -> None:
    """Download klines for multiple symbols sequentially.

    Sequential to respect rate limits (each download already does
    multiple paginated requests internally).
    """
    logger.info(
        "Starting download: %d symbols, interval=%s, months=%d",
        len(symbols), interval, months,
    )
    for symbol in symbols:
        try:
            await download_klines(symbol, interval, months)
        except Exception:
            logger.exception("Failed to download %s", symbol)
    logger.info("Download complete.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Download historical klines from Binance")
    parser.add_argument(
        "--symbols", nargs="+", default=ALL_PAIRS,
        help="Trading pairs to download (default: all pairs from config)",
    )
    parser.add_argument(
        "--interval", default="15m",
        help="Kline interval (default: 15m). Options: 1m, 5m, 15m, 1h, 4h, 1d",
    )
    parser.add_argument(
        "--months", type=int, default=6,
        help="How many months of history to download (default: 6)",
    )
    args = parser.parse_args()

    setup_logging("INFO")
    asyncio.run(download_all(args.symbols, args.interval, args.months))


if __name__ == "__main__":
    main()
