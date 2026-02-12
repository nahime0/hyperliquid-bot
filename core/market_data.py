"""Market data cache with Hyperliquid WebSocket feeds and technical indicators.

The Hyperliquid SDK WebSocket runs in a background thread. We use threading.Lock
for thread-safe access to _candles and _mid_prices. Contention is minimal: writes
from WS thread, reads every 60s from the main asyncio loop.
"""
from __future__ import annotations

import asyncio
import threading
import time
from datetime import datetime, timezone
from typing import Any

import pandas as pd
import ta as ta_lib

import logging as _logging

from config.settings import Settings
from core.client import HyperliquidClient
from utils.logger import get_logger

logger = get_logger(__name__)

# Suppress noisy SDK websocket errors (testnet sends non-dict messages
# that crash ws_msg_to_identifier — SDK bug, not ours)
_logging.getLogger("websocket").setLevel(_logging.CRITICAL)

# Max concurrent REST calls for initial candle loading
# Keep low to avoid 429 on testnet (60 coins × 3 intervals = 180 calls)
_REST_SEMAPHORE_LIMIT = 5

# How many candles to request initially per interval
_INTERVAL_LIMITS: dict[str, int] = {
    "5m": 300,   # ~25 hours — RSI Divergence needs swing history
    "15m": 200,
    "1h": 300,   # need 200+ for EMA200
}

# Interval durations in milliseconds (for start_ms calculation)
_INTERVAL_MS: dict[str, int] = {
    "5m": 5 * 60 * 1000,
    "15m": 15 * 60 * 1000,
    "1h": 60 * 60 * 1000,
}


class MarketData:
    """In-memory market data cache with Hyperliquid WebSocket feeds."""

    def __init__(self, client: HyperliquidClient, settings: Settings) -> None:
        self._client = client
        self._settings = settings

        # Mid prices for all assets — updated via WS
        self._mid_prices: dict[str, float] = {}
        self._mid_updated_at: float = 0.0  # last WS update timestamp
        self._mid_lock = threading.Lock()

        # OHLCV candles per (coin, interval)
        self._candles: dict[tuple[str, str], pd.DataFrame] = {}
        self._candle_lock = threading.Lock()

        # Track what we're monitoring
        self._coins: list[str] = []
        self._intervals: list[str] = []
        self._running = False

        # WS subscription IDs for cleanup
        self._ws_info = None  # Info instance with WS enabled

    # ── Lifecycle ───────────────────────────────────────────

    async def start(self, coins: list[str], intervals: list[str] | None = None) -> None:
        """Start WebSocket feeds and load initial candle data."""
        if intervals is None:
            intervals = ["15m", "1h"]

        self._coins = coins
        self._intervals = intervals
        self._running = True

        # Pre-create empty DataFrames so WS updates aren't dropped if REST fails
        for coin in coins:
            for interval in intervals:
                key = (coin, interval)
                if key not in self._candles:
                    self._candles[key] = pd.DataFrame()

        # Load historical candles via REST (with concurrency limit)
        sem = asyncio.Semaphore(_REST_SEMAPHORE_LIMIT)

        async def _load_with_sem(coin: str, interval: str, limit: int) -> None:
            async with sem:
                await self._load_candles(coin, interval, limit=limit)

        load_tasks = []
        for coin in coins:
            for interval in intervals:
                limit = _INTERVAL_LIMITS.get(interval, 200)
                load_tasks.append(_load_with_sem(coin, interval, limit))
        await asyncio.gather(*load_tasks)

        loaded = sum(
            1 for k in self._candles
            if self._candles[k] is not None and len(self._candles[k]) > 0
        )
        logger.info("Loaded initial candles: %d/%d coin-interval combos", loaded, len(load_tasks))

        # Start WebSocket subscriptions via SDK (runs in background thread)
        await self._start_ws_subscriptions(coins, intervals)

        logger.info(
            "MarketData started: %d coins, %d intervals",
            len(coins), len(intervals),
        )

    async def _start_ws_subscriptions(self, coins: list[str], intervals: list[str]) -> None:
        """Subscribe to Hyperliquid WebSocket feeds."""
        api_url = self._settings.hyperliquid.api_url

        # Create a separate Info instance with WS enabled
        self._ws_info = await asyncio.to_thread(
            lambda: __import__('hyperliquid.info', fromlist=['Info']).Info(api_url, skip_ws=False)
        )

        # Subscribe to allMids (single stream for all mid prices)
        def _on_all_mids(ws_msg: dict[str, Any]) -> None:
            if not self._running:
                return
            # SDK passes full ws_msg: {"channel": "allMids", "data": {"mids": {...}}}
            inner = ws_msg.get("data", ws_msg)
            mids = inner.get("mids", {})
            with self._mid_lock:
                for coin, price_str in mids.items():
                    try:
                        self._mid_prices[coin] = float(price_str)
                    except (ValueError, TypeError):
                        pass
                if mids:
                    self._mid_updated_at = time.time()

        await asyncio.to_thread(
            self._ws_info.subscribe,
            {"type": "allMids"},
            _on_all_mids,
        )

        # Subscribe to candle streams for each coin/interval
        for coin in coins:
            for interval in intervals:
                cb = self._make_candle_callback(coin, interval)
                await asyncio.to_thread(
                    self._ws_info.subscribe,
                    {"type": "candle", "coin": coin, "interval": interval},
                    cb,
                )

    def _make_candle_callback(self, coin: str, interval: str):
        """Create a candle callback closure for a specific coin/interval."""
        def _on_candle(ws_msg: dict[str, Any]) -> None:
            if not self._running:
                return
            self._handle_candle_ws(coin, interval, ws_msg)
        return _on_candle

    def _handle_candle_ws(self, coin: str, interval: str, ws_msg: dict[str, Any]) -> None:
        """Process a candle WS update (runs in WS thread)."""
        key = (coin, interval)
        # SDK passes full ws_msg: {"channel": "candle", "data": {candle_obj}}
        inner = ws_msg.get("data", ws_msg)
        candle_data = inner if isinstance(inner, list) else [inner]

        for c in candle_data:
            if not isinstance(c, dict) or "t" not in c:
                continue
            ts = pd.Timestamp(c["t"], unit="ms", tz="UTC")
            row = {
                "open": float(c["o"]),
                "high": float(c["h"]),
                "low": float(c["l"]),
                "close": float(c["c"]),
                "volume": float(c["v"]),
                "quote_volume": 0.0,
                "trades": int(c.get("n", 0)),
                "close_time": pd.Timestamp(c["T"], unit="ms", tz="UTC"),
            }

            with self._candle_lock:
                if key not in self._candles:
                    return
                df = self._candles[key]
                if ts in df.index:
                    for col, val in row.items():
                        df.at[ts, col] = val
                else:
                    new_row = pd.DataFrame([row], index=pd.DatetimeIndex([ts], name="open_time"))
                    for col in df.columns:
                        if col not in new_row.columns:
                            new_row[col] = None
                    self._candles[key] = pd.concat([df, new_row]).tail(500)

    async def stop(self) -> None:
        """Stop all WebSocket feeds."""
        self._running = False
        # The SDK WS threads will stop when the Info object is garbage collected
        self._ws_info = None
        logger.info("MarketData stopped")

    # ── REST data loading ───────────────────────────────────

    async def _load_candles(self, coin: str, interval: str, limit: int = 200) -> None:
        """Load historical candles via REST API."""
        try:
            interval_ms = _INTERVAL_MS.get(interval, 15 * 60 * 1000)
            end_ms = int(time.time() * 1000)
            start_ms = end_ms - (limit * interval_ms)

            raw = await self._client.get_candles(coin, interval, start_ms, end_ms)
            df = self._candles_to_df(raw)

            with self._candle_lock:
                self._candles[(coin, interval)] = df

            logger.debug("Loaded %d candles for %s %s", len(df), coin, interval)
        except Exception as exc:
            logger.error("Failed to load candles %s %s: %s", coin, interval, exc)

    @staticmethod
    def _candles_to_df(candles: list[dict[str, Any]]) -> pd.DataFrame:
        """Convert Hyperliquid candle snapshots to a clean DataFrame."""
        if not candles:
            return pd.DataFrame()

        rows = []
        for c in candles:
            rows.append({
                "open_time": pd.Timestamp(c["t"], unit="ms", tz="UTC"),
                "open": float(c["o"]),
                "high": float(c["h"]),
                "low": float(c["l"]),
                "close": float(c["c"]),
                "volume": float(c["v"]),
                "quote_volume": 0.0,
                "trades": int(c.get("n", 0)),
                "close_time": pd.Timestamp(c["T"], unit="ms", tz="UTC"),
            })

        df = pd.DataFrame(rows)
        df = df.set_index("open_time")
        return df

    # ── Data access ─────────────────────────────────────────

    def get_mid_price(self, coin: str) -> float | None:
        """Get latest mid price for a coin (from WS stream)."""
        with self._mid_lock:
            return self._mid_prices.get(coin)

    def is_price_stale(self, max_age_seconds: float = 120.0) -> bool:
        """Check if WS mid prices haven't been updated recently."""
        with self._mid_lock:
            if self._mid_updated_at == 0:
                return True
            return (time.time() - self._mid_updated_at) > max_age_seconds

    def get_candles(self, coin: str, interval: str) -> pd.DataFrame | None:
        """Get candle DataFrame for coin/interval."""
        with self._candle_lock:
            df = self._candles.get((coin, interval))
            return df.copy() if df is not None else None

    # ── Technical indicators ────────────────────────────────

    def compute_indicators(self, coin: str, interval: str = "15m") -> dict[str, Any] | None:
        """Compute RSI, Bollinger Bands, MACD, and EMA for a coin."""
        df = self.get_candles(coin, interval)
        if df is None or len(df) < 30:
            return None

        close = df["close"]
        high = df["high"]
        low = df["low"]

        # RSI(14)
        rsi_series = ta_lib.momentum.RSIIndicator(close, window=14).rsi()
        rsi_val = float(rsi_series.iloc[-1]) if not rsi_series.empty else None

        # Bollinger Bands(20, 2)
        bb = ta_lib.volatility.BollingerBands(close, window=20, window_dev=2)
        bb_dict: dict[str, float | None] = {
            "upper": float(bb.bollinger_hband().iloc[-1]),
            "mid": float(bb.bollinger_mavg().iloc[-1]),
            "lower": float(bb.bollinger_lband().iloc[-1]),
        }

        # MACD(12, 26, 9)
        macd = ta_lib.trend.MACD(close, window_slow=26, window_fast=12, window_sign=9)
        macd_dict: dict[str, float | None] = {
            "macd": float(macd.macd().iloc[-1]),
            "signal": float(macd.macd_signal().iloc[-1]),
            "histogram": float(macd.macd_diff().iloc[-1]),
        }

        # EMA(9, 21, 50)
        ema9 = ta_lib.trend.EMAIndicator(close, window=9).ema_indicator()
        ema21 = ta_lib.trend.EMAIndicator(close, window=21).ema_indicator()
        ema50 = ta_lib.trend.EMAIndicator(close, window=50).ema_indicator()

        return {
            "rsi": rsi_val,
            "bollinger": bb_dict,
            "macd": macd_dict,
            "ema": {
                "ema9": float(ema9.iloc[-1]) if not ema9.empty else None,
                "ema21": float(ema21.iloc[-1]) if not ema21.empty else None,
                "ema50": float(ema50.iloc[-1]) if not ema50.empty else None,
            },
        }

    # ── Snapshot for AI ─────────────────────────────────────

    async def generate_snapshot(
        self,
        coins: list[str],
        intervals: list[str] | None = None,
        balances: dict[str, float] | None = None,
        open_positions: list[dict[str, Any]] | None = None,
        recent_trades: list[dict[str, Any]] | None = None,
        trade_stats: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Generate a complete market snapshot for the AI decision engine."""
        if intervals is None:
            intervals = ["15m", "1h"]

        now = datetime.now(timezone.utc)

        markets: dict[str, Any] = {}
        for coin in coins:
            sym_data: dict[str, Any] = {}

            # Current mid price
            mid = self.get_mid_price(coin)
            if mid:
                sym_data["mid_price"] = mid

            # Indicators per interval
            indicators: dict[str, Any] = {}
            for interval in intervals:
                ind = self.compute_indicators(coin, interval)
                if ind:
                    indicators[interval] = ind

                # Last few candles for context
                candles = self.get_candles(coin, interval)
                if candles is not None and not candles.empty:
                    last5 = candles.tail(5)
                    indicators.setdefault(interval, {})
                    indicators[interval]["recent_candles"] = [
                        {
                            "time": str(idx),
                            "open": float(row["open"]),
                            "high": float(row["high"]),
                            "low": float(row["low"]),
                            "close": float(row["close"]),
                            "volume": float(row["volume"]),
                        }
                        for idx, row in last5.iterrows()
                    ]

            sym_data["indicators"] = indicators
            markets[coin] = sym_data

        snapshot: dict[str, Any] = {
            "timestamp": now.isoformat(),
            "markets": markets,
            "portfolio": {
                "balances": balances or {},
                "open_positions": open_positions or [],
            },
            "trade_history": recent_trades or [],
            "trade_stats": trade_stats or {},
        }

        return snapshot
