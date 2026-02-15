"""Cooldown Tracker — prevents re-entry after losses.

Per-symbol cooldown: blocks BUY on a symbol for N seconds after a loss.
Global cooldown: blocks ALL BUYs for M seconds after K consecutive losses.
"""
from __future__ import annotations

import time

from config.settings import RiskConfig
from utils.logger import get_logger

logger = get_logger(__name__)


class CooldownTracker:
    """Tracks per-symbol and global cooldowns based on trade results."""

    def __init__(self, config: RiskConfig) -> None:
        self._config = config
        # symbol -> timestamp when cooldown expires
        self._symbol_cooldowns: dict[str, float] = {}
        # Global cooldown expiry timestamp
        self._global_cooldown_until: float = 0.0
        # Recent consecutive losses (reset on any win)
        self._consecutive_losses: int = 0

    def record_trade_result(self, symbol: str, is_win: bool) -> None:
        """Call after a position is closed."""
        if is_win:
            self._consecutive_losses = 0
        else:
            self._consecutive_losses += 1
            # Per-symbol cooldown
            expiry = time.time() + self._config.symbol_cooldown_sec
            self._symbol_cooldowns[symbol] = expiry
            logger.info(
                "Cooldown: %s blocked for %ds after loss",
                symbol, self._config.symbol_cooldown_sec,
            )
            # Global cooldown
            if self._consecutive_losses >= self._config.global_cooldown_losses:
                self._global_cooldown_until = time.time() + self._config.global_cooldown_sec
                logger.warning(
                    "Global cooldown: %d consecutive losses — all BUYs blocked for %ds",
                    self._consecutive_losses, self._config.global_cooldown_sec,
                )

    def can_buy(self, symbol: str) -> tuple[bool, str]:
        """Check if a BUY is allowed for this symbol right now."""
        now = time.time()

        # Global cooldown
        if now < self._global_cooldown_until:
            remaining = int(self._global_cooldown_until - now)
            return False, f"Global cooldown active ({remaining}s remaining, {self._consecutive_losses} consecutive losses)"

        # Per-symbol cooldown
        expiry = self._symbol_cooldowns.get(symbol, 0.0)
        if now < expiry:
            remaining = int(expiry - now)
            return False, f"Symbol cooldown active for {symbol} ({remaining}s remaining)"

        return True, ""

    def is_global_cooldown_active(self) -> bool:
        """Check if global cooldown is currently active."""
        return time.time() < self._global_cooldown_until

    def get_symbol_cooldown_remaining(self, symbol: str) -> float:
        """Return remaining cooldown seconds for a symbol (0 if none)."""
        expiry = self._symbol_cooldowns.get(symbol, 0.0)
        return max(0.0, expiry - time.time())

    def get_state(self) -> dict[str, object]:
        now = time.time()
        active_symbol = {
            sym: int(exp - now)
            for sym, exp in self._symbol_cooldowns.items()
            if exp > now
        }
        return {
            "consecutive_losses": self._consecutive_losses,
            "global_cooldown_remaining": max(0, int(self._global_cooldown_until - now)),
            "symbol_cooldowns": active_symbol,
        }

    def load_state(self, data: dict[str, object]) -> None:
        """Restore cooldown state from a previously saved dict."""
        now = time.time()
        self._consecutive_losses = int(data.get("consecutive_losses", 0))
        global_remaining = int(data.get("global_cooldown_remaining", 0))
        if global_remaining > 0:
            self._global_cooldown_until = now + global_remaining
        symbol_cds = data.get("symbol_cooldowns", {})
        if isinstance(symbol_cds, dict):
            for sym, remaining in symbol_cds.items():
                remaining_sec = int(remaining)
                if remaining_sec > 0:
                    self._symbol_cooldowns[sym] = now + remaining_sec
        logger.info(
            "Cooldown state restored: %d consecutive losses, global_cd=%ds, %d symbol cooldowns",
            self._consecutive_losses, global_remaining, len(self._symbol_cooldowns),
        )
