---
name: strategy-breakout
description: Analyze and modify breakout strategy — S/R detection, breakout confirmation, volume scoring
user-invocable: true
---

# Breakout (Support/Resistance) Strategy — Analyze & Modify

**Source**: `strategies/breakout.py`
**Strategy type**: `breakout`
**Thesis**: Price breaking through key S/R levels with volume signals continuation.

## Code Map

| What | Location |
|------|----------|
| Weights `W_*` | Lines 56-61 |
| `SCORE_THRESHOLD` | Line 62 (default 0.60) |
| `update()` | Line 147 — computes 1h S/R levels + 15m breakout detection |
| S/R level identification | In `update()`, local highs/lows from 1h candles |
| `_check_long_entry()` | Line 343 — hard blocks (352-365), scoring (367-421) |
| `_check_short_entry()` | Line 455 — hard blocks (464-477), scoring (479-533) |
| `_check_long_exit()` | Line 303 — price drops back below resistance |
| `_check_short_exit()` | Line 322 — price rises back above support |

## Scoring Weights (lines 56-61)

```
W_BREAKOUT_STRENGTH = 0.25   % distance past S/R level
W_VOLUME            = 0.25   Volume confirmation ratio
W_TREND             = 0.20   1h trend alignment
W_CLEAN_BREAK       = 0.10   Confirm bars above/below level
W_FUNDING           = 0.10   Funding acceptable
W_COOLDOWN          = 0.10   No per-symbol cooldown
```

## Hard Blocks

| Block | Long (line) | Short (line) |
|-------|-------------|--------------|
| `check_hard_blocks()` | 352 | 464 |
| Trend: BEARISH blocks LONG | 363 | — |
| Trend: BULLISH blocks SHORT | — | 475 |

## Config (`config/settings.py`)

| Field | Line | Env Var | Default |
|-------|------|---------|---------|
| `bo_lookback_hours` | 148/152 | `BO_LOOKBACK_HOURS` | `24` |
| `bo_min_breakout_pct` | 149/153 | `BO_MIN_BREAKOUT_PCT` | `0.1` (%) |
| `bo_confirm_bars` | 150/154 | `BO_CONFIRM_BARS` | `2` |

## How to Modify

### More signals
1. Increase `bo_lookback_hours`: `24` → `48` — finds more S/R levels from wider history
2. Lower `bo_min_breakout_pct`: `0.1` → `0.05` — smaller moves past level count
3. Lower `bo_confirm_bars`: `2` → `1` — faster confirmation
4. Lower `SCORE_THRESHOLD` (line 62)

### Better quality (too many false breakouts)
1. Increase `bo_confirm_bars`: `2` → `3` — more bars must hold past level
2. Increase `bo_min_breakout_pct`: `0.1` → `0.3` — need stronger break
3. Increase `W_VOLUME` and `W_CLEAN_BREAK` — emphasize volume and confirmation

### Change S/R detection
S/R levels are computed in `update()` from 1h candle local highs/lows. To improve:
- Use clustering to merge nearby levels
- Weight levels by number of touches
- Use volume profile for high-volume nodes as S/R

### Change exit logic
Currently exits on failed breakout (price returns past level). Alternatives:
- Trail a stop below the breakout level
- Exit on volume dry-up (no follow-through)
- Time-based exit if no momentum continuation

### Multi-interval approach
Uses 1h for S/R identification, 15m for breakout detection. To change:
- Use 4h for stronger S/R levels
- Use 5m for faster breakout detection (but more noise)

## Performance Context

Read `.claude/skills/_base_strategy_analysis.md` for SQL queries. Use `{STRATEGY_TYPE}` = `breakout`.
