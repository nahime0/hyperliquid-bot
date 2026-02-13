"""Position sizing using Fractional Kelly Criterion.

Kelly formula:  f = (win_rate * payoff - (1 - win_rate)) / payoff
Fractional:     size = bankroll * (f / 4)   # conservative quarter-Kelly

Cold-start behaviour (< COLD_START_TRADES trades):
  Kelly produces tiny sizes because defaults are conservative and
  the ×0.5 penalty shrinks them further.  On a small bankroll this
  falls below the minimum order notional ($10).
  → During cold-start we use usdc_per_position from config as the base
    size (e.g. $40), falling back to COLD_START_PCT if not configured.
    This ensures meaningful trade sizes from the start.

Kelly-mode floor:
  Even with enough trade history, quarter-Kelly often produces
  tiny sizes (especially with conservative stats).  The sizer uses
  max(MIN_ORDER_USDC, usdc_per_position) as a floor so that trades
  are at least the configured per-position USDC target.

The sizer also applies:
- Confidence scaling (AI confidence multiplied into size)
- Hard cap from RiskConfig.max_trade_pct
- Minimum notional floor (avoids dust orders)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from config.settings import RiskConfig
from utils.logger import get_logger

logger = get_logger(__name__)

# Minimum order size (USDC) — Hyperliquid min notional ~$10
MIN_ORDER_USDC = 10.0

# Cold-start: fixed percentage of bankroll when trade history is thin
COLD_START_TRADES = 10
COLD_START_PCT = 5.0  # 5% of bankroll per trade during cold-start

# Default payoff ratio when no trade history exists
DEFAULT_PAYOFF = 1.5
DEFAULT_WIN_RATE = 0.5


@dataclass(frozen=True)
class SizeResult:
    """Output of the position sizer."""
    size_usdc: float   # absolute USDC amount to risk
    size_pct: float    # as % of bankroll
    kelly_f: float     # raw Kelly fraction (before /4)
    capped: bool       # True if size was reduced by a cap
    reason: str        # explanation


class PositionSizer:
    """Computes position size for each trade using quarter-Kelly."""

    def __init__(self, config: RiskConfig) -> None:
        self._config = config

    def compute(
        self,
        bankroll: float,
        trade_stats: dict[str, Any],
        ai_confidence: float,
        ai_size_pct: float | None = None,
        utilization_boost: float = 1.0,
        sl_distance_pct: float | None = None,
    ) -> SizeResult:
        """Return how much USDC to allocate for this trade.

        Parameters
        ----------
        bankroll : float
            Current available USDC balance.
        trade_stats : dict
            From Database.get_trade_stats() — needs win_rate, avg_win, avg_loss.
        ai_confidence : float
            AI decision confidence (0-1).
        ai_size_pct : float | None
            Size requested by the AI (0-15 %); used as a soft hint, never blindly trusted.
        utilization_boost : float
            Multiplier from capital utilization logic (1.0 = no boost, up to max_size_boost).
        """
        total_trades = trade_stats.get("total_trades", 0)
        max_pct = self._config.max_trade_pct

        # ── Cold-start: usdc_per_position or fallback % ──────
        # During cold-start, use usdc_per_position from config as the
        # base trade size. Falls back to COLD_START_PCT if bankroll is
        # too small for usdc_per_position to make sense.
        if total_trades < COLD_START_TRADES:
            # Prefer absolute USDC target from config
            usd_per_pos = self._config.usdc_per_position
            if usd_per_pos > 0 and bankroll > 0:
                size_pct = (usd_per_pos / bankroll) * 100
            else:
                size_pct = COLD_START_PCT

            # Apply utilization boost
            if utilization_boost > 1.0:
                size_pct *= utilization_boost

            # AI hint as soft cap
            if ai_size_pct is not None and ai_size_pct > 0:
                size_pct = min(size_pct, ai_size_pct)

            # Risk-based cap: ensure each trade risks at most risk_per_trade_pct
            if sl_distance_pct is not None and sl_distance_pct > 0 and bankroll > 0:
                risk_budget = bankroll * self._config.risk_per_trade_pct / 100
                risk_cap_usdc = risk_budget / (sl_distance_pct / 100)
                risk_cap_pct = (risk_cap_usdc / bankroll) * 100
                if size_pct > risk_cap_pct:
                    size_pct = risk_cap_pct

            # Hard cap
            capped = False
            if size_pct > max_pct:
                size_pct = max_pct
                capped = True

            size_usdc = round(bankroll * (size_pct / 100), 2)

            if size_usdc < MIN_ORDER_USDC:
                return SizeResult(
                    size_usdc=0, size_pct=0, kelly_f=0, capped=False,
                    reason=f"Cold-start size {size_usdc:.2f} USDC below exchange minimum {MIN_ORDER_USDC}",
                )

            reason = (
                f"cold_start ({total_trades}/{COLD_START_TRADES} trades) "
                f"conf={ai_confidence:.2f} → {size_pct:.2f}% = {size_usdc:.2f} USDC"
            )
            if capped:
                reason += f" (capped to {max_pct}%)"

            logger.debug("PositionSizer: %s", reason)
            return SizeResult(
                size_usdc=size_usdc,
                size_pct=size_pct,
                kelly_f=0,
                capped=capped,
                reason=reason,
            )

        # ── Kelly sizing: enough history ─────────────────────
        win_rate = trade_stats.get("win_rate", DEFAULT_WIN_RATE)
        avg_win = abs(trade_stats.get("avg_win", 0)) or DEFAULT_PAYOFF
        avg_loss = abs(trade_stats.get("avg_loss", 0)) or 1.0

        # Payoff ratio (reward / risk)
        payoff = avg_win / avg_loss if avg_loss > 0 else DEFAULT_PAYOFF

        # Kelly fraction
        kelly_f = (win_rate * payoff - (1 - win_rate)) / payoff if payoff > 0 else 0
        kelly_f = max(kelly_f, 0)  # never negative

        # Quarter-Kelly scaled by confidence
        fraction = (kelly_f / 4) * ai_confidence

        # Convert to percentage
        size_pct = fraction * 100

        # Apply utilization boost
        if utilization_boost > 1.0:
            size_pct *= utilization_boost

        # --- Caps ---
        capped = False

        # If the AI suggested a size, use the minimum of Kelly and AI
        if ai_size_pct is not None and ai_size_pct > 0:
            size_pct = min(size_pct, ai_size_pct)

        # Risk-based cap: ensure each trade risks at most risk_per_trade_pct
        if sl_distance_pct is not None and sl_distance_pct > 0 and bankroll > 0:
            risk_budget = bankroll * self._config.risk_per_trade_pct / 100
            risk_cap_usdc = risk_budget / (sl_distance_pct / 100)
            risk_cap_pct = (risk_cap_usdc / bankroll) * 100
            if size_pct > risk_cap_pct:
                size_pct = risk_cap_pct
                capped = True

        # Hard cap from config
        if size_pct > max_pct:
            size_pct = max_pct
            capped = True

        # Floor: ensure minimum viable trade during Kelly mode too
        # Use usdc_per_position as floor (not just MIN_ORDER_USDC) so
        # Kelly doesn't produce dust orders when stats are conservative.
        # If the floor exceeds max_trade_pct, fall back to max allowed;
        # only reject if even that is below the exchange minimum ($10).
        size_usdc = round(bankroll * (size_pct / 100), 2)
        min_size = max(MIN_ORDER_USDC, self._config.usdc_per_position)
        if size_usdc < min_size:
            min_pct = (min_size / bankroll) * 100 if bankroll > 0 else 0
            if min_pct <= max_pct:
                size_pct = min_pct
                size_usdc = round(min_size, 2)
            else:
                # Floor exceeds cap — use max allowed instead
                size_pct = max_pct
                size_usdc = round(bankroll * (max_pct / 100), 2)
                capped = True
                if size_usdc < MIN_ORDER_USDC:
                    return SizeResult(
                        size_usdc=0, size_pct=0, kelly_f=kelly_f, capped=False,
                        reason=f"Size {size_usdc:.2f} USDC below exchange minimum {MIN_ORDER_USDC}",
                    )

        reason = (
            f"kelly_f={kelly_f:.4f} quarter={fraction:.4f} "
            f"conf={ai_confidence:.2f} trades={total_trades} "
            f"WR={win_rate:.1%} payoff={payoff:.2f} "
            f"→ {size_pct:.2f}% = {size_usdc:.2f} USDC"
        )
        if capped:
            reason += f" (capped to {max_pct}%)"

        logger.debug("PositionSizer: %s", reason)
        return SizeResult(
            size_usdc=size_usdc,
            size_pct=size_pct,
            kelly_f=kelly_f,
            capped=capped,
            reason=reason,
        )
