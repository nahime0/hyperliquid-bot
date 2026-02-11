"""Minimal async Telegram notification helper.

Sends messages via the Bot API.  Silently no-ops when not configured.

Usage:
    tg = TelegramNotifier(settings.telegram)
    await tg.send("Trade executed: BUY 0.05 ETH @ 2000")
"""
from __future__ import annotations

import asyncio
from typing import Any

import aiohttp

from config.settings import TelegramConfig
from utils.logger import get_logger

logger = get_logger(__name__)

_API = "https://api.telegram.org/bot{token}/sendMessage"


class TelegramNotifier:
    """Fire-and-forget Telegram message sender."""

    def __init__(self, config: TelegramConfig) -> None:
        self._config = config
        self._session: aiohttp.ClientSession | None = None

    @property
    def enabled(self) -> bool:
        return self._config.enabled

    async def start(self) -> None:
        if self.enabled:
            self._session = aiohttp.ClientSession()
            logger.info("Telegram notifier started")

    async def close(self) -> None:
        if self._session:
            await self._session.close()
            self._session = None

    async def send(self, text: str, silent: bool = False) -> None:
        """Send a message.  No-ops if Telegram is not configured."""
        if not self.enabled or not self._session:
            return
        url = _API.format(token=self._config.bot_token)
        payload: dict[str, Any] = {
            "chat_id": self._config.chat_id,
            "text": text,
            "parse_mode": "Markdown",
            "disable_notification": silent,
        }
        try:
            async with self._session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    logger.warning("Telegram API %d: %s", resp.status, body[:200])
        except Exception:
            logger.debug("Telegram send failed (non-critical)", exc_info=True)

    # ── Convenience formatters ───────────────────────────────

    async def notify_trade(
        self,
        action: str,
        symbol: str,
        qty: float,
        price: float,
        pnl: float | None = None,
    ) -> None:
        parts = [f"*{action}* `{symbol}`", f"Qty: `{qty:.6f}`", f"Price: `{price:.4f}`"]
        if pnl is not None:
            emoji = "+" if pnl >= 0 else ""
            parts.append(f"PnL: `{emoji}{pnl:.4f}` USDC")
        await self.send("\n".join(parts))

    async def notify_alert(self, title: str, detail: str) -> None:
        await self.send(f"*{title}*\n{detail}")
