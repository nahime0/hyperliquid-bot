"""Download historical candles from Hyperliquid and save as CSV.

Usage:
    # Download core coins (default 6 months, 15m + 1h intervals)
    .venv/bin/python -m scripts.download_history

    # Custom settings
    .venv/bin/python -m scripts.download_history --months 3 --intervals 5m 15m 1h --coins ETH BTC SOL

    # All static coins
    .venv/bin/python -m scripts.download_history --coins ALL

Data is saved to  data/history/<COIN>_<interval>.csv
"""
from __future__ import annotations

import argparse
import asyncio
import csv
from datetime import datetime, timezone, timedelta
from pathlib import Path

from hyperliquid.info import Info

from config.pairs import CORE_COINS, ALL_COINS
from utils.logger import setup_logging, get_logger

logger = get_logger(__name__)

HISTORY_DIR = Path(__file__).resolve().parent.parent / "data" / "history"

# CSV columns (compatible with load_candles)
COLUMNS = [
    "open_time", "open", "high", "low", "close", "volume",
    "close_time", "quote_volume", "trades",
    "taker_buy_base", "taker_buy_quote", "ignore",
]

# Chunk size per API call (Hyperliquid returns at most ~5000 candles per request)
INTERVAL_MS: dict[str, int] = {
    "1m": 60_000,
    "5m": 5 * 60_000,
    "15m": 15 * 60_000,
    "1h": 60 * 60_000,
    "4h": 4 * 60 * 60_000,
    "1d": 24 * 60 * 60_000,
}

# Max candles per request (stay well under API limits)
CHUNK_CANDLES = 5000


def _parse_candle(raw: dict) -> list:
    """Convert Hyperliquid candle dict to CSV row compatible with load_candles."""
    open_time = int(raw["t"])
    close_time = int(raw["T"])
    o = raw["o"]
    h = raw["h"]
    l_ = raw["l"]
    c = raw["c"]
    v = raw["v"]
    # Hyperliquid doesn't provide quote_volume, trades, taker fields — fill with 0
    quote_vol = float(o) * float(v)  # approximate: open * volume
    return [open_time, o, h, l_, c, v, close_time, quote_vol, int(raw.get("n", 0)), 0, 0, 0]


async def download_candles(
    info: Info,
    coin: str,
    interval: str,
    start_ms: int,
    end_ms: int,
) -> list[list]:
    """Download candles for one coin+interval, paginating forward.

    Hyperliquid data retention varies by interval:
      - 1h: ~6 months   - 15m: ~52 days   - 5m: ~17 days
    The API returns up to ~5000 candles per request from the available range.
    We request the full range first, then paginate forward from the last candle.
    """
    interval_ms = INTERVAL_MS.get(interval)
    if interval_ms is None:
        raise ValueError(f"Unknown interval: {interval}. Supported: {list(INTERVAL_MS)}")

    all_rows: list[list] = []
    cursor = start_ms

    while cursor < end_ms:
        # Always request up to end_ms — the API returns what's available
        raw = await asyncio.to_thread(
            info.candles_snapshot, coin, interval, cursor, end_ms,
        )

        if not raw:
            break

        for candle in raw:
            row = _parse_candle(candle)
            all_rows.append(row)

        # Move cursor past the last candle we received
        last_t = int(raw[-1]["t"])
        next_cursor = last_t + interval_ms
        if next_cursor <= cursor:
            break
        cursor = next_cursor

        # If we got fewer candles than the API cap, we have all available data
        if len(raw) < CHUNK_CANDLES:
            break

        await asyncio.sleep(0.1)

    # Deduplicate by open_time (in case of overlapping chunks)
    seen: set[int] = set()
    deduped: list[list] = []
    for row in all_rows:
        ot = row[0]
        if ot not in seen:
            seen.add(ot)
            deduped.append(row)

    deduped.sort(key=lambda r: r[0])
    return deduped


async def download_coin(
    info: Info,
    coin: str,
    interval: str,
    months: int,
) -> Path:
    """Download candles for one coin+interval and save to CSV."""
    now_ms = int(datetime.now(tz=timezone.utc).timestamp() * 1000)
    start_ms = int((datetime.now(tz=timezone.utc) - timedelta(days=months * 30)).timestamp() * 1000)

    out_path = HISTORY_DIR / f"{coin}_{interval}.csv"
    logger.info("Downloading %s %s (last %d months)...", coin, interval, months)

    rows = await download_candles(info, coin, interval, start_ms, now_ms)

    if not rows:
        logger.warning("No data returned for %s %s", coin, interval)
        return out_path

    # Write CSV
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(COLUMNS)
        writer.writerows(rows)

    first_ts = datetime.fromtimestamp(rows[0][0] / 1000, tz=timezone.utc)
    last_ts = datetime.fromtimestamp(rows[-1][0] / 1000, tz=timezone.utc)
    logger.info(
        "  %s %s: %d candles, %s -> %s  -> %s",
        coin, interval, len(rows),
        first_ts.strftime("%Y-%m-%d"),
        last_ts.strftime("%Y-%m-%d"),
        out_path.name,
    )
    return out_path


async def download_all(
    coins: list[str],
    intervals: list[str],
    months: int,
    api_url: str,
) -> None:
    """Download candles for multiple coins and intervals."""
    info = Info(api_url, skip_ws=True)

    total = len(coins) * len(intervals)
    logger.info(
        "Starting download: %d coins x %d intervals = %d jobs, months=%d",
        len(coins), len(intervals), total, months,
    )

    for coin in coins:
        for interval in intervals:
            try:
                await download_coin(info, coin, interval, months)
            except Exception:
                logger.exception("Failed to download %s %s", coin, interval)
    logger.info("Download complete.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Download historical candles from Hyperliquid")
    parser.add_argument(
        "--coins", nargs="+", default=CORE_COINS,
        help=f"Coins to download (default: {CORE_COINS}). Use ALL for static list.",
    )
    parser.add_argument(
        "--intervals", nargs="+", default=["15m", "1h"],
        help="Candle intervals (default: 15m 1h). Options: 1m, 5m, 15m, 1h, 4h, 1d",
    )
    parser.add_argument(
        "--months", type=int, default=6,
        help="How many months of history to download (default: 6)",
    )
    parser.add_argument(
        "--testnet", action="store_true", default=False,
        help="Use testnet API (default: mainnet for historical data)",
    )
    args = parser.parse_args()

    setup_logging("INFO")

    # Resolve coin list
    coins = args.coins
    if len(coins) == 1 and coins[0].upper() == "ALL":
        coins = ALL_COINS

    api_url = (
        "https://api.hyperliquid-testnet.xyz" if args.testnet
        else "https://api.hyperliquid.xyz"
    )

    asyncio.run(download_all(coins, args.intervals, args.months, api_url))


if __name__ == "__main__":
    main()
