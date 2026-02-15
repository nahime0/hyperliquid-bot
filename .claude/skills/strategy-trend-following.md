---
name: strategy-trend-following
description: Analyze and modify trend following strategy — change threshold, EMA params, enable/disable SHORT
user-invocable: true
---

# Trend Following Strategy — Analyze & Modify

**Source**: `strategies/trend_following.py`
**Strategy type**: `trend_following`
**Thesis**: Ride strong directional momentum when 4h change and trend align.

## Code Map

| What | Location |
|------|----------|
| Weights `W_*` | Lines 55-60 |
| `SCORE_THRESHOLD` | Line 61 (default 0.60) |
| `update()` | Line ~155 — computes 1h candles, 4h EMA trend |
| `_compute_trend_4h()` | Lines 208-226 — EMA12/EMA26 crossover from 1h candles |
| `_check_short_entry()` | Line 287 — hard blocks (295-307), scoring (309-337) |
| `_check_long_entry()` | Line 360 — hard blocks (366-378), scoring (380-408) |
| `_check_short_exit()` | Line 431 — 1h OR 4h trend turns BULLISH |
| `_check_long_exit()` | Line 452 — 1h OR 4h trend turns BEARISH |

## Scoring Weights (lines 55-60)

```
W_CHANGE_4H  = 0.30   4h price change magnitude
W_TREND_1H   = 0.20   1h trend direction (EMA-based)
W_TREND_4H   = 0.20   4h trend direction (EMA12/EMA26)
W_VOLUME     = 0.10   Volume confirmation
W_FUNDING    = 0.10   Funding acceptable
W_COOLDOWN   = 0.10   No per-symbol cooldown
```

## Hard Blocks

| Block | Long (line) | Short (line) |
|-------|-------------|--------------|
| Extreme funding | 367 | 296 |
| Global cooldown | 370 | 299 |
| Fresh symbol cooldown | 373-378 | 302-307 |

## Config (`config/settings.py`)

| Field | Line | Env Var | Default |
|-------|------|---------|---------|
| `tf_change_threshold` | 121 | `TF_CHANGE_THRESHOLD` | `4.0` (%) |
| `tf_allow_short` | 120 | `TF_ALLOW_SHORT` | `False` |

## Class Constants (in source)

`EMA_FAST_4H=12`, `EMA_SLOW_4H=26`, `SLOPE_WINDOW_4H=5`, `CHANGE_4H_THRESHOLD=3.0`

Note: `CHANGE_4H_THRESHOLD` in the source (3.0) vs `tf_change_threshold` in config (4.0) — config overrides.

## How to Modify

### More signals (strategy is too selective)
1. Lower `tf_change_threshold`: `4.0` → `2.5` — catches smaller moves
2. Lower `SCORE_THRESHOLD` (line 61): `0.60` → `0.50`
3. Enable SHORT: set `TF_ALLOW_SHORT=true` (currently disabled due to 0% historical win rate)

### Enable SHORT trading
Set `TF_ALLOW_SHORT=true` env var or edit `config/settings.py` line 120. Currently disabled because SHORT had 0% win rate in testing. Consider re-enabling after market conditions change.

### Change 4h trend calculation
Edit `_compute_trend_4h()` (lines 208-226). Currently uses EMA12/EMA26 crossover on 1h data. To modify:
- Change EMA periods: edit `EMA_FAST_4H` / `EMA_SLOW_4H` constants
- Use different indicator: replace EMA with SMA, WMA, or MACD signal
- Change slope window: edit `SLOPE_WINDOW_4H`

### Change exit logic
Exit at lines 431/452 triggers when ANY trend reverses. To make less aggressive:
- Require BOTH 1h AND 4h to reverse (change `or` to `and`)
- Add momentum confirmation before exiting

### Change 4h data source
Currently calculates 4h from 1h candles (no extra WS needed). To use actual 4h candles, add `"4h"` to `MarketConfig.intervals` and fetch directly.

## Performance Context

Read `.claude/skills/_base_strategy_analysis.md` for SQL queries. Use `{STRATEGY_TYPE}` = `trend_following`.
