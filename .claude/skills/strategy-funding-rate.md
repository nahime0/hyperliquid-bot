---
name: strategy-funding-rate
description: Analyze and modify funding rate strategy — extreme thresholds, normalization exit, contrarian logic
user-invocable: true
---

# Funding Rate (Contrarian) Strategy — Analyze & Modify

**Source**: `strategies/funding_rate.py`
**Strategy type**: `funding_rate`
**Thesis**: Extreme funding = crowded trade. Go contrarian — fade the crowd.

## Code Map

| What | Location |
|------|----------|
| Weights `W_*` | Lines 55-60 |
| `SCORE_THRESHOLD` | Line 61 (default 0.60) |
| `update()` | Line 159 — reads 1h candles + funding rates |
| `_check_long_entry()` | Line 281 — hard blocks (291-301), scoring (304-342) |
| `_check_short_entry()` | Line 373 — hard blocks (383-393), scoring (395-430) |
| `_check_long_exit()` | Line 461 — funding normalizes (>= -0.0001) |
| `_check_short_exit()` | Line 481 — funding normalizes (<= 0.0001) |

## Scoring Weights (lines 55-60)

```
W_FUNDING_EXTREMITY = 0.30   Distance past extreme threshold
W_PRICE_ACTION      = 0.20   RSI confirmation
W_VOLUME            = 0.15   Volume confirmation
W_TREND             = 0.15   1h trend not opposing
W_COOLDOWN          = 0.10   No per-symbol cooldown
W_CROWDED           = 0.10   High volume as crowding proxy
```

## Hard Blocks — UNIQUE: no funding block

| Block | Long (line) | Short (line) |
|-------|-------------|--------------|
| Global cooldown | 291 | 383 |
| Fresh symbol cooldown | 296-301 | 388-393 |

**No funding hard block** — extreme funding IS the signal. This is the only strategy where funding is the entry condition, not a blocker.

## Config (`config/settings.py`)

| Field | Line | Env Var | Default |
|-------|------|---------|---------|
| `fr_extreme_negative` | 156 | `FR_EXTREME_NEGATIVE` | `-0.0005` |
| `fr_extreme_positive` | 157 | `FR_EXTREME_POSITIVE` | `0.0005` |
| `fr_normalize_threshold` | 158 | `FR_NORMALIZE_THRESHOLD` | `0.0001` |

## How to Modify

### More signals
1. Relax extreme thresholds: `-0.0005` → `-0.0003` and `0.0005` → `0.0003`
2. Lower `SCORE_THRESHOLD` (line 61)
3. Remove trend component weight — pure funding play

### Better quality
1. Tighten extreme thresholds: `-0.0005` → `-0.001` and `0.0005` → `0.001` — only very extreme funding
2. Increase `W_PRICE_ACTION` — require stronger RSI confirmation
3. Add open interest check: high OI + extreme funding = stronger signal

### Change exit logic
Currently exits when funding normalizes (crosses threshold). To modify:
- Tighter exit: change `fr_normalize_threshold` from `0.0001` to `0.00005` — hold longer
- Add price target: exit when +X% profit regardless of funding
- Time-based: exit after N hours if funding hasn't normalized
- Partial exit: close half when funding starts normalizing

### Add open interest
Hyperliquid provides open interest data. To add as scoring component:
1. Fetch OI in `update()` from `info.open_interest()`
2. Add `W_OI = 0.10` weight (redistribute from others)
3. Score: high OI + extreme funding = very crowded

### Change contrarian direction
Currently: negative funding → LONG (fade shorts), positive → SHORT (fade longs).
To reverse (momentum-following): flip the logic in entry methods.

## Performance Context

Read `.claude/skills/_base_strategy_analysis.md` for SQL queries. Use `{STRATEGY_TYPE}` = `funding_rate`.
