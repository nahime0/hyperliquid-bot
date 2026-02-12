"""Hyperliquid client — async wrapper around the synchronous SDK.

All SDK calls are wrapped with asyncio.to_thread() since the SDK is synchronous.
Includes exponential backoff retry and global rate limiting for transient errors.
"""
from __future__ import annotations

import asyncio
from typing import Any

from eth_account import Account
from hyperliquid.info import Info
from hyperliquid.exchange import Exchange
from hyperliquid.utils import constants

from config.settings import Settings
from utils.logger import get_logger

logger = get_logger(__name__)

# Retry config
MAX_RETRIES = 3
RETRY_BASE_DELAY = 1.0  # seconds

# Rate limiting — token bucket
# Mainnet: 1200 weight/min = 20/sec. We use 10/sec for safety headroom.
# Testnet has stricter (undocumented) limits.
_RATE_LIMIT_PER_SEC = 10
_RATE_LIMIT_BURST = 5


class _RateLimiter:
    """Async token-bucket rate limiter."""

    def __init__(self, rate: float, burst: int) -> None:
        self._rate = rate      # tokens refilled per second
        self._burst = burst    # max tokens (burst capacity)
        self._tokens = float(burst)
        self._last = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """Wait until a token is available, then consume it."""
        while True:
            async with self._lock:
                now = asyncio.get_event_loop().time()
                if self._last == 0.0:
                    self._last = now
                self._tokens = min(
                    self._burst,
                    self._tokens + (now - self._last) * self._rate,
                )
                self._last = now
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                wait = (1.0 - self._tokens) / self._rate
            await asyncio.sleep(wait)


class HyperliquidClient:
    """Async wrapper around Hyperliquid SDK (Info + Exchange)."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._info: Info | None = None
        self._exchange: Exchange | None = None
        self._meta: dict[str, Any] | None = None
        self._sz_decimals: dict[str, int] = {}  # coin → szDecimals
        self._address: str = ""  # derived Ethereum address
        self._limiter = _RateLimiter(_RATE_LIMIT_PER_SEC, _RATE_LIMIT_BURST)

    async def connect(self) -> None:
        """Initialize Info and Exchange clients."""
        cfg = self._settings.hyperliquid
        api_url = cfg.api_url

        # Derive Ethereum address from private key (needed for user_state queries)
        if cfg.account_address:
            self._address = cfg.account_address
        elif cfg.private_key:
            acct = Account.from_key(cfg.private_key)
            self._address = acct.address
            logger.info("Derived address from private key: %s", self._address)

        # Info client (read-only, no auth needed)
        self._info = await asyncio.to_thread(
            Info, api_url, True  # skip_ws=True (we manage WS separately)
        )

        # Exchange client (needs private key for trading)
        if cfg.private_key:
            wallet = Account.from_key(cfg.private_key)
            self._exchange = await asyncio.to_thread(
                Exchange,
                wallet,
                api_url,
                None,  # meta (auto-fetched)
                None,  # vault_address
                self._address if cfg.account_address else None,  # account_address
            )

        # Load asset metadata
        await self._load_meta()

        mode = "TESTNET" if cfg.testnet else "MAINNET"
        logger.info(
            "Hyperliquid client connected (%s) — %d assets",
            mode, len(self._sz_decimals),
        )

    async def _load_meta(self) -> None:
        """Load universe metadata (szDecimals, maxLeverage, etc.)."""
        await self._limiter.acquire()
        self._meta = await asyncio.to_thread(self.info.meta)
        for asset in self._meta.get("universe", []):
            self._sz_decimals[asset["name"]] = asset["szDecimals"]

    async def close(self) -> None:
        """Cleanup."""
        self._info = None
        self._exchange = None
        self._meta = None
        logger.info("Hyperliquid client disconnected")

    @property
    def info(self) -> Info:
        if self._info is None:
            raise RuntimeError("Client not connected. Call connect() first.")
        return self._info

    @property
    def exchange(self) -> Exchange:
        if self._exchange is None:
            raise RuntimeError("Exchange not available (no private key?).")
        return self._exchange

    # ── Retry helper ────────────────────────────────────────

    async def _retry(self, fn: Any, *args: Any, **kwargs: Any) -> Any:
        """Execute a sync SDK call via to_thread with rate limiting and exponential backoff."""
        last_exc: Exception | None = None
        for attempt in range(MAX_RETRIES):
            await self._limiter.acquire()
            try:
                return await asyncio.to_thread(fn, *args, **kwargs)
            except Exception as exc:
                last_exc = exc
                delay = RETRY_BASE_DELAY * (2 ** attempt)
                logger.warning(
                    "API error (attempt %d/%d): %s — retrying in %.1fs",
                    attempt + 1, MAX_RETRIES, str(exc), delay,
                )
                await asyncio.sleep(delay)
        raise last_exc  # type: ignore[misc]

    # ── Market data ─────────────────────────────────────────

    async def get_meta_and_asset_ctxs(self) -> tuple[dict, list]:
        """Get meta + asset contexts (includes dayNtlVlm = 24h notional volume)."""
        result = await self._retry(self.info.meta_and_asset_ctxs)
        return result[0], result[1]

    async def get_all_mids(self) -> dict[str, float]:
        """Get mid prices for all assets."""
        raw = await self._retry(self.info.all_mids)
        return {k: float(v) for k, v in raw.items()}

    async def get_price(self, coin: str) -> float:
        """Get mid price for a single coin."""
        mids = await self.get_all_mids()
        price = mids.get(coin)
        if price is None:
            raise ValueError(f"No mid price for {coin}")
        return price

    async def get_candles(
        self,
        coin: str,
        interval: str,
        start_ms: int,
        end_ms: int,
    ) -> list[dict[str, Any]]:
        """Get OHLCV candle snapshots.

        Returns list of dicts with keys: t, T, s, i, o, c, h, l, v, n
        """
        return await self._retry(
            self.info.candles_snapshot,
            coin, interval, start_ms, end_ms,
        )

    async def get_l2_snapshot(self, coin: str) -> dict[str, Any]:
        """Get L2 order book snapshot."""
        return await self._retry(self.info.l2_snapshot, coin)

    async def get_user_state(self) -> dict[str, Any]:
        """Get account state: margin summary + asset positions."""
        if not self._address:
            raise RuntimeError("No address available. Set HL_ACCOUNT_ADDRESS or HL_PRIVATE_KEY.")
        return await self._retry(self.info.user_state, self._address)

    async def get_spot_state(self) -> dict[str, Any]:
        """Get spot clearinghouse state (for unified account USDC balance)."""
        if not self._address:
            raise RuntimeError("No address available. Set HL_ACCOUNT_ADDRESS or HL_PRIVATE_KEY.")
        return await self._retry(
            self.info.post, "/info", {"type": "spotClearinghouseState", "user": self._address}
        )

    async def get_account_balance(self) -> float:
        """Get total account value from Hyperliquid perp marginSummary.

        accountValue = idle USDC + margin in use + unrealized PnL.
        This is the single source of truth for the account equity.
        """
        state = await self.get_user_state()
        return float(state.get("marginSummary", {}).get("accountValue", 0))

    async def get_open_positions(self) -> list[dict[str, Any]]:
        """Get open perpetual positions with size, entry, PnL, liquidation.

        Returns list of dicts with: coin, szi (signed size), entryPx,
        positionValue, unrealizedPnl, liquidationPx, leverage, etc.
        """
        state = await self.get_user_state()
        positions: list[dict[str, Any]] = []
        for ap in state.get("assetPositions", []):
            pos = ap.get("position", {})
            szi = float(pos.get("szi", 0))
            if szi == 0:
                continue
            positions.append({
                "coin": pos.get("coin", ""),
                "szi": szi,  # positive=long, negative=short
                "size": abs(szi),
                "direction": "LONG" if szi > 0 else "SHORT",
                "entryPx": float(pos.get("entryPx", 0)),
                "positionValue": float(pos.get("positionValue", 0)),
                "unrealizedPnl": float(pos.get("unrealizedPnl", 0)),
                "liquidationPx": float(pos.get("liquidationPx", 0)) if pos.get("liquidationPx") else None,
                "leverage": ap.get("position", {}).get("leverage", {}).get("value", 1),
                "marginUsed": float(pos.get("marginUsed", 0)),
            })
        return positions

    # ── Order management ────────────────────────────────────

    async def place_market_order(
        self,
        coin: str,
        is_buy: bool,
        size: float,
        reduce_only: bool = False,
    ) -> dict[str, Any]:
        """Place a market order (aggressive limit that crosses spread)."""
        sz = self.round_size(coin, size)
        logger.info(
            "Placing MARKET %s %s: size=%.6f reduce_only=%s",
            "BUY" if is_buy else "SELL", coin, sz, reduce_only,
        )
        result = await self._retry(
            self.exchange.market_open,
            coin, is_buy, sz,
        )
        logger.info("Order result: %s", result)
        return result

    async def place_limit_order(
        self,
        coin: str,
        is_buy: bool,
        size: float,
        price: float,
    ) -> dict[str, Any]:
        """Place a limit GTC order."""
        sz = self.round_size(coin, size)
        logger.info(
            "Placing LIMIT %s %s: size=%.6f price=%.4f",
            "BUY" if is_buy else "SELL", coin, sz, price,
        )
        order_type = {"limit": {"tif": "Gtc"}}
        result = await self._retry(
            self.exchange.order,
            coin, is_buy, sz, price, order_type,
        )
        logger.info("Order result: %s", result)
        return result

    async def close_position(self, coin: str) -> dict[str, Any]:
        """Close an entire position via market_close."""
        logger.info("Closing position: %s", coin)
        result = await self._retry(self.exchange.market_close, coin)
        logger.info("Close result: %s", result)
        return result

    async def cancel_order(self, coin: str, oid: int) -> dict[str, Any]:
        """Cancel a single order."""
        logger.info("Cancelling order %d on %s", oid, coin)
        return await self._retry(self.exchange.cancel, coin, oid)

    async def update_leverage(
        self,
        coin: str,
        leverage: int,
        is_cross: bool = True,
    ) -> dict[str, Any]:
        """Set leverage for a coin."""
        logger.info("Setting leverage %s: %dx %s", coin, leverage, "cross" if is_cross else "isolated")
        return await self._retry(
            self.exchange.update_leverage,
            leverage, coin, is_cross,
        )

    # ── Size helpers ────────────────────────────────────────

    def get_sz_decimals(self, coin: str) -> int:
        """Get size decimal precision for a coin."""
        return self._sz_decimals.get(coin, 2)

    def round_size(self, coin: str, size: float) -> float:
        """Round size to szDecimals precision."""
        decimals = self.get_sz_decimals(coin)
        factor = 10 ** decimals
        return int(size * factor) / factor  # truncate (floor)

    def get_meta(self) -> dict[str, Any]:
        """Return cached universe metadata."""
        return self._meta or {}
