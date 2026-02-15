---
name: strategy-macd-divergence
description: Analyze and modify MACD divergence strategy — swing detection, histogram scoring, MACD params
user-invocable: true
---

# MACD Divergence Strategy — Analyze & Modify

**Source**: `strategies/macd_divergence.py`
**Strategy type**: `macd_divergence`
**Thesis**: MACD histogram divergences from price signal momentum shifts before price follows.

## Code Map

| What | Location |
|------|----------|
| Weights `W_*` | Lines 61-66 |
| `SCORE_THRESHOLD` | Line 67 (default 0.60) |
| `update()` | Line 155 — computes 15m MACD(12,26,9), swing detection |
| MACD constants | `MACD_FAST=12, MACD_SLOW=26, MACD_SIGNAL=9` |
| Divergence detection | Swing highs/lows on price vs histogram |
| `_check_long_entry()` | Line 411 — hard blocks (417-430), scoring (432-468) |
| `_check_short_entry()` | Line 500 — hard blocks (506-519), scoring (521-556) |
| `_check_exit()` | Line 388 — histogram zero-cross |

## Scoring Weights (lines 61-66)

```
W_DIVERGENCE = 0.30   Divergence strength (price vs histogram magnitude)
W_HISTOGRAM  = 0.20   Histogram direction (rising/falling)
W_VOLUME     = 0.15   Volume confirmation
W_TREND      = 0.15   1h trend alignment
W_FUNDING    = 0.10   Funding acceptable
W_COOLDOWN   = 0.10   No per-symbol cooldown
```

## Hard Blocks

| Block | Long (line) | Short (line) |
|-------|-------------|--------------|
| `check_hard_blocks()` | 417 | 506 |
| Trend: BEARISH blocks LONG | 428 | — |
| Trend: BULLISH blocks SHORT | — | 517 |

## Config (`config/settings.py`)

| Field | Line | Env Var | Default |
|-------|------|---------|---------|
| `macd_div_swing_window` | 142 | `MACD_DIV_SWING_WINDOW` | `5` |
| `macd_div_recency` | 143 | `MACD_DIV_RECENCY` | `40` |

## MACD Parameters (hardcoded in source)

`MACD_FAST=12`, `MACD_SLOW=26`, `MACD_SIGNAL=9` — standard MACD settings. To change, edit the constants in the source file.

## How to Modify

### More signals
1. Increase `macd_div_recency`: `40` → `60` — look further back for divergences
2. Decrease `macd_div_swing_window`: `5` → `3` — find more pivot points
3. Remove trend hard block (line 428/517)
4. Lower `SCORE_THRESHOLD` (line 67)

### Better quality
1. Decrease `macd_div_recency`: `40` → `20` — only recent divergences
2. Increase `macd_div_swing_window`: `5` → `7` — stricter pivots
3. Increase `W_DIVERGENCE` — emphasize divergence strength
4. Add volume spike requirement as hard block

### Change MACD parameters
Edit constants in source file (currently hardcoded, not in config). To make configurable:
1. Add `macd_fast`, `macd_slow`, `macd_signal` to `StrategyConfig`
2. Add env var mapping in `load_settings()`
3. Read in constructor

Alternative MACD settings:
- 8/17/9 (faster)
- 5/35/5 (impulse system)
- 12/26/9 (standard — current)

### Change exit logic
Currently exits on histogram zero-cross. Alternatives:
- Exit when histogram momentum slows (absolute value decreasing for N bars)
- Exit on signal line cross (MACD crosses signal line)
- Combine with RSI: exit when RSI normalizes

### Improve divergence detection
Current: simple swing high/low comparison between price and histogram.
Improvements:
- Weight by number of bars between divergence points (longer = stronger)
- Require minimum divergence magnitude
- Check for hidden divergences (continuation patterns)

## Performance Context

Read `.claude/skills/_base_strategy_analysis.md` for SQL queries. Use `{STRATEGY_TYPE}` = `macd_divergence`.
