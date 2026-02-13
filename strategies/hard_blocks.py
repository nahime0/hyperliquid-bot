"""Shared hard-block checks for scoring-based strategies.

Hard blocks bypass the scoring system entirely — if any fires, the coin is
skipped for that direction without computing a score.

Hard blocks:
  - |funding| >= 0.001 (extreme funding)
  - Global cooldown active (3+ consecutive losses)
  - Fresh per-symbol cooldown (loss < 10 min ago)
"""
from __future__ import annotations

from strategies.cooldown import CooldownTracker
from config.settings import RiskConfig

# Extreme funding rate — always block
HARD_FUNDING_RATE = 0.001

# Block if loss < 10 min ago (fresh cooldown)
HARD_COOLDOWN_FRESHNESS = 600  # seconds


def check_hard_blocks(
    symbol: str,
    *,
    funding_rates: dict[str, float],
    cooldown: CooldownTracker,
    risk_config: RiskConfig,
) -> tuple[bool, bool, float]:
    """Run shared hard-block checks for a symbol.

    Returns:
        (blocked, can_buy, abs_funding_rate)
        - blocked: True if any hard block fires (caller should skip this coin)
        - can_buy: True if symbol has no cooldown (used for scoring)
        - abs_funding_rate: absolute funding rate for scoring use
    """
    abs_funding = abs(funding_rates.get(symbol, 0.0))

    # Hard block: extreme funding
    if abs_funding >= HARD_FUNDING_RATE:
        return True, False, abs_funding

    # Hard block: global cooldown
    if cooldown.is_global_cooldown_active():
        return True, False, abs_funding

    # Hard block: fresh per-symbol cooldown (< 10 min since loss)
    can_buy, _ = cooldown.can_buy(symbol)
    if not can_buy:
        remaining = cooldown.get_symbol_cooldown_remaining(symbol)
        time_since = risk_config.symbol_cooldown_sec - remaining
        if time_since < HARD_COOLDOWN_FRESHNESS:
            return True, False, abs_funding

    return False, can_buy, abs_funding
