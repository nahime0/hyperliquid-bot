---
name: strategy-rsi-divergence
description: Analyze and modify RSI divergence strategy — swing detection, exit thresholds, divergence logic
user-invocable: true
---

# RSI Divergence Strategy — Analyze & Modify

**Source**: `strategies/rsi_divergence.py`
**Strategy type**: `rsi_divergence`
**Thesis**: Price-RSI divergences signal trend exhaustion. Non-scoring, divergence detection based.

## Code Map

| What | Location |
|------|----------|
| `update()` | Line 124 — computes 5m RSI + swing detection |
| `_detect_bullish_divergence()` | Swing lows: price LL + RSI HL |
| `_detect_bearish_divergence()` | Swing highs: price HH + RSI LH |
| `_check_long_entry()` | Line 344 — bullish divergence + funding + cooldown |
| `_check_short_entry()` | Line 371 — bearish divergence + funding + cooldown |
| `_check_exit()` | Line 321 — RSI threshold exit |

## Detection Method (no scoring weights)

Unlike other strategies, RSI divergence uses **binary divergence detection**:
- Swing highs/lows found via rolling min/max over `2 * swing_window + 1` centered window
- Bullish: price makes lower low, RSI makes higher low → LONG
- Bearish: price makes higher high, RSI makes lower high → SHORT
- Confidence based on divergence magnitude, not weighted score

## Hard Blocks

| Block | Long (line) | Short (line) |
|-------|-------------|--------------|
| High funding rate | 350 | 377 |
| Cooldown check | 354-357 | 381-384 |

## Config (`config/settings.py`)

| Field | Line | Env Var | Default |
|-------|------|---------|---------|
| `primary_interval` | 126 | `PRIMARY_INTERVAL` | `"5m"` |
| `rsi_div_period` | 119 | `RSI_DIV_PERIOD` | `14` |
| `rsi_div_swing_window` | 122 | `RSI_DIV_SWING_WINDOW` | `5` |
| `rsi_div_long_exit` | 123 | `RSI_DIV_LONG_EXIT` | `55.0` |
| `rsi_div_short_exit` | 124 | `RSI_DIV_SHORT_EXIT` | `35.0` |

## How to Modify

### More signals (too few divergences detected)
1. Reduce `rsi_div_swing_window` (config line 122): `5` → `3` — smaller window finds more swings
2. Change interval from `5m` to `15m` — longer candles show clearer divergences
3. Relax the divergence magnitude check in entry methods

### Better quality (false divergences)
1. Increase `rsi_div_swing_window` to `7` — stricter pivot detection
2. Add volume confirmation: in entry methods, check volume ratio before accepting divergence
3. Add trend filter: block bullish divergence in strong downtrends

### Change exit thresholds
Edit config values:
- `rsi_div_long_exit` (default 55.0) — RSI above this exits LONG. Lower = earlier exit, higher = ride longer
- `rsi_div_short_exit` (default 35.0) — RSI below this exits SHORT. Higher = earlier exit, lower = ride longer

### Convert to scoring-based
To add scoring like other strategies:
1. Add `W_*` constants and `SCORE_THRESHOLD` at top of file
2. In entry methods, score divergence strength + volume + trend + funding + cooldown
3. Only signal when score >= threshold

### Change swing detection
The swing detection algorithm uses `rolling(2*window+1).min/max()`. To use a different method:
- Edit the divergence detection methods
- Consider scipy.signal.argrelextrema for more sophisticated peak detection

## Performance Context

Read `.claude/skills/_base_strategy_analysis.md` for SQL queries. Use `{STRATEGY_TYPE}` = `rsi_divergence`.
Note: score distribution query won't return results (this strategy doesn't use scoring).
