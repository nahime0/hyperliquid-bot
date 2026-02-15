---
name: strategy-mean-reversion
description: Analyze and modify mean reversion strategy — weights, thresholds, entry/exit logic
user-invocable: true
---

# Mean Reversion Strategy — Analyze & Modify

**Source**: `strategies/mean_reversion.py`
**Strategy type**: `mean_reversion`
**Thesis**: Price reverts to the mean after extreme RSI/BB moves.

## Code Map

| What | Location |
|------|----------|
| Weights `W_*` | Lines 70-75 |
| `SCORE_THRESHOLD` | Line 76 (default 0.60) |
| `update()` | Line 164 — computes 15m + 1h indicators |
| `_check_long_entry()` | Line 386 — hard blocks (391-412), scoring (414-447) |
| `_check_short_entry()` | Line 477 — hard blocks (482-503), scoring (505-538) |
| `_check_long_exit()` | Line 342 — RSI > 65 or price >= upper BB |
| `_check_short_exit()` | Line 364 — RSI < 30 or price <= lower BB |
| `_classify()` | Line 300 — OVERSOLD/OVERBOUGHT classification |

## Scoring Weights (lines 70-75)

```
W_RSI        = 0.25   RSI oversold (<35) / overbought (>65)
W_BB         = 0.25   Price at/beyond Bollinger Band
W_VOLUME     = 0.15   Volume ratio >= 1.0
W_MACRO_RSI  = 0.15   1h RSI alignment (<60 for LONG, >40 for SHORT)
W_FUNDING    = 0.10   |funding| < threshold
W_COOLDOWN   = 0.10   No per-symbol cooldown active
```

## Hard Blocks (before scoring)

| Block | Long (line) | Short (line) |
|-------|-------------|--------------|
| Extreme funding \|rate\| >= 0.001 | 393 | 484 |
| Global cooldown | 397 | 488 |
| Fresh symbol cooldown < 600s | 401-407 | 492-498 |
| Trend: BEARISH blocks LONG | 410 | — |
| Trend: BULLISH blocks SHORT | — | 501 |

## Config (`config/settings.py`)

| Field | Line | Env Var | Default |
|-------|------|---------|---------|
| `mr_interval` | 127 | `MR_INTERVAL` | `"15m"` |
| `rsi_div_period` | 119 | `RSI_DIV_PERIOD` | `14` |

## Class Constants (in source)

`RSI_PERIOD=14`, `BB_PERIOD=20`, `BB_STD=2.0`

## How to Modify

### More signals (too few trades)
1. Lower `SCORE_THRESHOLD` (line 76): `0.60` → `0.55` or `0.50`
2. Remove trend hard block (line 410/501) — allows counter-trend entries
3. Lower RSI thresholds: change `< 35` to `< 40` for LONG, `> 65` to `> 60` for SHORT (in scoring section)

### Better quality (too many losing trades)
1. Raise `SCORE_THRESHOLD` to `0.65` or `0.70`
2. Increase `W_RSI` and `W_BB` (primary indicators), decrease `W_COOLDOWN`/`W_FUNDING`
3. Add spread hard block: require spread < X% before entry

### Change exit logic
Edit `_check_long_exit()` (line 342) / `_check_short_exit()` (line 364). Currently:
- LONG exits when RSI > 65 or price >= upper BB
- SHORT exits when RSI < 30 or price <= lower BB

To add time-based exit: check `(now - opened_at) > max_hold` and return CLOSE Decision.

### Change interval
In `update()` (line 164), the strategy fetches `mr_interval` candles. Change `mr_interval` in config or edit `update()` to use a different interval. Ensure the interval is in `MarketConfig.intervals` (settings.py line 108).

## Performance Context

Read `.claude/skills/_base_strategy_analysis.md` for SQL queries. Use `{STRATEGY_TYPE}` = `mean_reversion`.
Run performance queries first to understand what needs changing before modifying code.
