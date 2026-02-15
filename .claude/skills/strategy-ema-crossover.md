---
name: strategy-ema-crossover
description: Analyze and modify EMA crossover strategy — EMA periods, cross detection, RSI filter
user-invocable: true
---

# EMA Crossover Strategy — Analyze & Modify

**Source**: `strategies/ema_crossover.py`
**Strategy type**: `ema_crossover`
**Thesis**: EMA crossovers signal trend changes. Golden cross → LONG, death cross → SHORT.

## Code Map

| What | Location |
|------|----------|
| Weights `W_*` | Lines 56-61 |
| `SCORE_THRESHOLD` | Line 62 (default 0.60) |
| `update()` | Line 149 — computes 15m EMA fast/slow + cross detection |
| Cross detection | In `update()`, within `max_bars` window |
| `_check_long_entry()` | Line 289 — hard blocks (295-308), scoring (310-342) |
| `_check_short_entry()` | Line 374 — hard blocks (380-393), scoring (395-427) |
| `_check_long_exit()` | Line 459 — death cross (EMA fast back below slow) |
| `_check_short_exit()` | Line 480 — golden cross (EMA fast back above slow) |

## Scoring Weights (lines 56-61)

```
W_CROSS      = 0.30   Cross strength (EMA spread magnitude)
W_TREND      = 0.20   1h trend alignment
W_VOLUME     = 0.15   Volume confirmation
W_RSI        = 0.15   RSI confirmation (< 60 for LONG, > 40 for SHORT)
W_FUNDING    = 0.10   Funding acceptable
W_COOLDOWN   = 0.10   No per-symbol cooldown
```

## Hard Blocks

| Block | Long (line) | Short (line) |
|-------|-------------|--------------|
| `check_hard_blocks()` | 295 | 380 |
| Trend: BEARISH blocks LONG | 306 | — |
| Trend: BULLISH blocks SHORT | — | 391 |

## Config (`config/settings.py`)

| Field | Line | Env Var | Default |
|-------|------|---------|---------|
| `ema_cross_fast` | 134 | `EMA_CROSS_FAST` | `9` |
| `ema_cross_slow` | 135 | `EMA_CROSS_SLOW` | `21` |
| `ema_cross_max_bars` | 136 | `EMA_CROSS_MAX_BARS` | `5` |

## How to Modify

### More signals
1. Narrow EMA gap: `fast=12, slow=15` — closer EMAs cross more often
2. Increase `ema_cross_max_bars`: `5` → `8` — accept older crossovers
3. Remove trend hard block (line 306/391) — allow counter-trend crosses
4. Lower `SCORE_THRESHOLD` (line 62)

### Better quality (too many whipsaws)
1. Widen EMA gap: `fast=8, slow=26` — fewer but stronger crosses
2. Decrease `ema_cross_max_bars`: `5` → `2` — only fresh crosses
3. Increase `W_CROSS` weight — emphasize cross strength
4. Add MACD confirmation: require MACD histogram agrees with cross direction

### Change EMA periods
Edit env vars or `config/settings.py` lines 134-136. Popular alternatives:
- 5/13 (fast scalping)
- 9/21 (current default)
- 12/26 (MACD-like)
- 20/50 (swing trading)
- 50/200 (golden/death cross classic)

### Change RSI filter
In scoring section, RSI filter uses `< 60` for LONG and `> 40` for SHORT. To tighten: use `< 50` / `> 50`. To remove: delete RSI scoring component and redistribute weight.

### Change interval
Currently uses 15m candles. For slower signals, change to 1h in `update()`. For faster, use 5m (more noise but faster entries).

### Add MACD confirmation
In entry scoring, add a MACD check:
```python
macd = ta.trend.MACD(close).macd_diff()
if macd.iloc[-1] > 0:  # MACD histogram positive
    score += W_MACD
```

## Performance Context

Read `.claude/skills/_base_strategy_analysis.md` for SQL queries. Use `{STRATEGY_TYPE}` = `ema_crossover`.
