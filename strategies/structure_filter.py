"""Structure Confirmation Filter for reversal strategies.

Prevents premature entries on impulsive dumps/pumps by requiring at least
one structural confirmation on 5m candles before allowing a reversal entry.

Three filters (OR logic — at least one must pass):

1. Lateral Base: price has stopped making new extremes (base formed after dump/pump)
2. RSI Micro-Divergence: momentum exhaustion (price new low but RSI higher low, or vice versa)
3. Break of Recent Structure: price breaks local resistance/support

If 5m data is insufficient (< 15 candles), the filter passes automatically (passthrough).
"""
from __future__ import annotations

import pandas as pd
import ta as ta_lib

from utils.logger import get_logger

logger = get_logger(__name__)

# Minimum candles needed for any filter to work
MIN_CANDLES = 15


def check_structure_confirmation(
    df_5m: pd.DataFrame | None,
    direction: str,
) -> tuple[bool, str]:
    """Check if price structure confirms a reversal entry.

    Args:
        df_5m: DataFrame with 5m OHLCV candles (columns: open, high, low, close, volume).
        direction: "LONG" or "SHORT".

    Returns:
        (confirmed, reason) — confirmed is True if at least one filter passes
        or data is insufficient. reason describes what passed/failed.
    """
    if df_5m is None or len(df_5m) < MIN_CANDLES:
        return True, "passthrough:insufficient_5m_data"

    if _has_lateral_base(df_5m, direction):
        return True, "lateral_base"

    if _has_rsi_divergence(df_5m, direction):
        return True, "rsi_micro_divergence"

    if _has_structure_break(df_5m, direction):
        return True, "structure_break"

    return False, "no_structural_confirmation"


def _has_lateral_base(df: pd.DataFrame, direction: str, lookback: int = 6) -> bool:
    """Check if price formed a base after the dump/pump.

    LONG: the lowest low of the last `lookback` candles is NOT in the last 2 candles
          (price stopped making new lows → base forming).
    SHORT: the highest high of the last `lookback` candles is NOT in the last 2 candles
           (price stopped making new highs → base forming).
    """
    window = df.iloc[-lookback:]

    if direction == "LONG":
        lows = window["low"].values
        min_idx = lows.argmin()
        # min_idx is 0-based within the window; last 2 candles are indices lookback-2, lookback-1
        return int(min_idx) < lookback - 2
    else:  # SHORT
        highs = window["high"].values
        max_idx = highs.argmax()
        return int(max_idx) < lookback - 2


def _has_rsi_divergence(df: pd.DataFrame, direction: str) -> bool:
    """Check for RSI micro-divergence on 5m candles.

    LONG: price makes lower-low but RSI makes higher-low → bearish momentum exhausting.
    SHORT: price makes higher-high but RSI makes lower-high → bullish momentum exhausting.

    Compares old window [-12:-6] vs recent window [-6:].
    """
    rsi_series = ta_lib.momentum.RSIIndicator(df["close"], window=14).rsi()
    if rsi_series.isna().all():
        return False

    old_slice = slice(-12, -6)
    new_slice = slice(-6, None)

    price_old = df["close"].iloc[old_slice]
    price_new = df["close"].iloc[new_slice]
    rsi_old = rsi_series.iloc[old_slice]
    rsi_new = rsi_series.iloc[new_slice]

    # Drop NaN from RSI (early values may be NaN)
    rsi_old = rsi_old.dropna()
    rsi_new = rsi_new.dropna()

    if rsi_old.empty or rsi_new.empty or price_old.empty or price_new.empty:
        return False

    if direction == "LONG":
        # Price lower-low AND RSI higher-low → bullish divergence
        price_ll = float(price_new.min()) < float(price_old.min())
        rsi_hl = float(rsi_new.min()) > float(rsi_old.min())
        return price_ll and rsi_hl
    else:  # SHORT
        # Price higher-high AND RSI lower-high → bearish divergence
        price_hh = float(price_new.max()) > float(price_old.max())
        rsi_lh = float(rsi_new.max()) < float(rsi_old.max())
        return price_hh and rsi_lh


def _has_structure_break(df: pd.DataFrame, direction: str, lookback: int = 10) -> bool:
    """Check if price breaks recent local structure.

    LONG: current close is above the max high of candles [-lookback:-3]
          → broke above local resistance.
    SHORT: current close is below the min low of candles [-lookback:-3]
           → broke below local support.

    Excludes last 3 candles from the reference window to avoid counting
    the current candle itself.
    """
    reference = df.iloc[-lookback:-3]
    if reference.empty:
        return False

    current_close = float(df["close"].iloc[-1])

    if direction == "LONG":
        resistance = float(reference["high"].max())
        return current_close > resistance
    else:  # SHORT
        support = float(reference["low"].min())
        return current_close < support
