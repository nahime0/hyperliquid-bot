---
name: strategy-mtf-confluence
description: Analyze and modify MTF confluence strategy — RSI thresholds, slope bars, timeframe alignment
user-invocable: true
---

# Multi-Timeframe Confluence Strategy — Analyze & Modify

**Source**: `strategies/mtf_confluence.py`
**Strategy type**: `mtf_confluence`
**Thesis**: When 5m + 15m + 1h all agree on direction, the signal is high conviction.

## Code Map

| What | Location |
|------|----------|
| Weights `W_*` | Lines 58-63 |
| `SCORE_THRESHOLD` | Line 64 (default 0.60) |
| `update()` | Line 144 — computes 5m RSI, 15m RSI slope, 1h trend |
| RSI slope calculation | In `update()`, slope over `mtf_rsi_slope_bars` |
| `_check_long_entry()` | Line 273 — hard blocks (279-287), confluence gates (293-300), scoring (302-334) |
| `_check_short_entry()` | Line 363 — hard blocks (369-377), confluence gates (382-390), scoring (392-424) |
| `_check_long_exit()` | Line 455 — ANY timeframe breaks alignment |
| `_check_short_exit()` | Line 486 — ANY timeframe breaks alignment |

## Scoring Weights (lines 58-63)

```
W_TF_5M      = 0.25   5m RSI oversold/overbought
W_TF_15M     = 0.25   15m RSI slope direction
W_TF_1H      = 0.20   1h trend alignment
W_VOLUME     = 0.10   Volume confirmation
W_FUNDING    = 0.10   Funding acceptable
W_COOLDOWN   = 0.10   No per-symbol cooldown
```

## Hard Blocks + Confluence Gates

| Block | Long (line) | Short (line) |
|-------|-------------|--------------|
| `check_hard_blocks()` | 279 | 369 |
| RSI(5m) not oversold/overbought | 293-295 | 382-384 |
| RSI(15m) slope wrong direction | 296-298 | 385-387 |
| 1h trend not aligned | 299-300 | 388-390 |

**Confluence gates** act as additional hard blocks — ALL 3 timeframes must agree.

## Config (`config/settings.py`)

| Field | Line | Env Var | Default |
|-------|------|---------|---------|
| `mtf_rsi_oversold` | 168 | `MTF_RSI_OVERSOLD` | `35.0` |
| `mtf_rsi_overbought` | 169 | `MTF_RSI_OVERBOUGHT` | `65.0` |
| `mtf_rsi_slope_bars` | 170 | `MTF_RSI_SLOPE_BARS` | `3` |

## How to Modify

### More signals (too strict — 3 TFs must agree)
1. Widen RSI thresholds: oversold `35` → `40`, overbought `65` → `60`
2. Change confluence to "2 of 3" instead of all 3: make gates soft (scoring) instead of hard blocks
3. Increase `mtf_rsi_slope_bars`: `3` → `5` — smoother slope, less noise
4. Lower `SCORE_THRESHOLD` (line 64)

### Convert confluence gates to scoring
Currently ALL 3 TFs are hard blocks. To make more flexible:
```python
# Instead of returning None, add to score
confluence_count = 0
if rsi_5m < oversold: confluence_count += 1
if rsi_15m_slope > 0: confluence_count += 1
if trend_1h == "BULLISH": confluence_count += 1
if confluence_count < 2:  # Require at least 2 of 3
    return None
score += (confluence_count / 3) * W_CONFLUENCE
```

### Better quality
1. Tighten RSI: oversold `35` → `30`, overbought `65` → `70`
2. Add 4h trend as 4th timeframe (compute from 1h like trend_following does)
3. Require higher RSI slope magnitude

### Change exit logic
Currently exits when ANY timeframe breaks alignment (aggressive). To make less aggressive:
- Require 2+ timeframes to break alignment
- Add grace period: ignore temporary breaks lasting < N bars
- Switch to RSI-based exit (exit when 5m RSI normalizes)

### Add timeframes
Currently: 5m, 15m, 1h. To add 4h:
1. Compute 4h trend in `update()` (from 1h candles, like trend_following)
2. Add `W_TF_4H` weight (redistribute)
3. Add confluence check in entry methods

## Performance Context

Read `.claude/skills/_base_strategy_analysis.md` for SQL queries. Use `{STRATEGY_TYPE}` = `mtf_confluence`.
