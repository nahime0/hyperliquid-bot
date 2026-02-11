from __future__ import annotations

import time
from typing import Any

from config.settings import Settings
from data.db import Database
from utils.logger import get_logger

from .types import Decision, Tier

logger = get_logger(__name__)


class PreScreen:
    """Tier 1: Deterministic pre-screening rules.

    Returns a HOLD decision immediately when any blocking rule fires,
    avoiding unnecessary (and costly) AI calls.
    """

    def __init__(self, settings: Settings, db: Database) -> None:
        self._settings = settings
        self._db = db
        self._last_snapshot_hash: str | None = None
        self._last_decision_ts: float = 0.0

    async def check(self, snapshot: dict[str, Any], snapshot_hash: str) -> tuple[bool, Decision | None]:
        """Run all pre-screening rules.

        Returns:
            (True, None) if all checks pass → escalate to Tier 2.
            (False, Decision) if a rule blocks → return HOLD immediately.
        """
        reason = (
            self._check_rate_limit()
            or self._check_snapshot_hash(snapshot_hash)
            or self._check_balance(snapshot)
            or self._check_open_positions(snapshot)
            or self._check_spread(snapshot)
            or await self._check_consecutive_losses()
        )

        if reason:
            logger.info("PreScreen blocked: %s", reason)
            decision = Decision(
                action="HOLD",
                confidence=1.0,
                reasoning=f"[PreScreen] {reason}",
                tier=Tier.PRESCREEN,
            )
            return False, decision

        # All checks passed — update state and escalate
        self._last_snapshot_hash = snapshot_hash
        self._last_decision_ts = time.monotonic()
        return True, None

    # ── Individual rules ────────────────────────────────────

    def _check_rate_limit(self) -> str | None:
        elapsed = time.monotonic() - self._last_decision_ts
        if self._last_decision_ts > 0 and elapsed < self._settings.ai.decision_interval:
            return f"Rate limit: {elapsed:.0f}s since last decision (min {self._settings.ai.decision_interval}s)"
        return None

    def _check_snapshot_hash(self, snapshot_hash: str) -> str | None:
        if self._last_snapshot_hash == snapshot_hash:
            return "Snapshot unchanged since last decision"
        return None

    def _check_balance(self, snapshot: dict[str, Any]) -> str | None:
        balances = snapshot.get("portfolio", {}).get("balances", {})
        usdc = balances.get("USDC", 0.0)
        if usdc < self._settings.risk.min_balance_usdc:
            return f"USDC balance {usdc:.2f} below minimum {self._settings.risk.min_balance_usdc}"
        return None

    def _check_open_positions(self, snapshot: dict[str, Any]) -> str | None:
        positions = snapshot.get("portfolio", {}).get("open_positions", [])
        if len(positions) >= self._settings.risk.max_open_positions:
            return f"Max open positions reached ({len(positions)}/{self._settings.risk.max_open_positions})"
        return None

    def _check_spread(self, snapshot: dict[str, Any]) -> str | None:
        """Check if ALL monitored pairs have spread above threshold."""
        markets = snapshot.get("markets", {})
        if not markets:
            return "No market data available"
        max_spread = self._settings.ai.max_spread_pct
        all_wide = all(
            m.get("spread_pct", 0) > max_spread
            for m in markets.values()
            if "spread_pct" in m
        )
        if all_wide and markets:
            return f"All spreads above {max_spread}% threshold"
        return None

    async def _check_consecutive_losses(self) -> str | None:
        stats = await self._db.get_trade_stats()
        consecutive = stats.get("consecutive_losses", 0)
        if consecutive >= 5:
            return f"Kill switch: {consecutive} consecutive losses"
        return None
