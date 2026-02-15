---
name: strategy-btc-correlation
description: Analyze and modify BTC correlation strategy — lag thresholds, exit logic, alt selection
user-invocable: true
---

# BTC Correlation (Lag) Strategy — Analyze & Modify

**Source**: `strategies/btc_correlation.py`
**Strategy type**: `btc_correlation`
**Thesis**: Altcoins lag BTC moves. Enter alts that haven't caught up yet.

## Code Map

| What | Location |
|------|----------|
| Weights `W_*` | Lines 57-62 |
| `SCORE_THRESHOLD` | Line 63 (default 0.60) |
| `update()` | Line 153 — computes BTC 1h change, scans alts for lag |
| BTC change calculation | In `update()`, close[-2] → close[-1] on 1h |
| `_check_long_entry()` | Line 284 — hard blocks (295-303), scoring (305-336) |
| `_check_short_entry()` | Line 359 — hard blocks (370-378), scoring (380-411) |
| `_check_long_exit()` | Line 434 — lag < catch_up_pct (alt caught up) |
| `_check_short_exit()` | Line 454 — lag > -catch_up_pct |

## Scoring Weights (lines 57-62)

```
W_BTC_MOVE   = 0.30   BTC 1h move magnitude
W_ALT_LAG    = 0.25   Lag magnitude between BTC and alt
W_VOLUME     = 0.15   Volume confirmation
W_FUNDING    = 0.10   Funding acceptable
W_TREND      = 0.10   1h trend alignment
W_COOLDOWN   = 0.10   No per-symbol cooldown
```

## Hard Blocks

| Block | Long (line) | Short (line) |
|-------|-------------|--------------|
| `check_hard_blocks()` | 296 | 371 |

No trend hard block — BTC correlation relies on BTC direction, not alt's own trend.

## Config (`config/settings.py`)

| Field | Line | Env Var | Default |
|-------|------|---------|---------|
| `btc_min_move_pct` | 160 | `BTC_MIN_MOVE_PCT` | `1.0` (%) |
| `btc_min_lag_pct` | 161 | `BTC_MIN_LAG_PCT` | `0.5` (%) |
| `btc_catch_up_pct` | 162 | `BTC_CATCH_UP_PCT` | `0.2` (%) |

## How to Modify

### More signals
1. Lower `btc_min_move_pct`: `1.0` → `0.5` — smaller BTC moves trigger scanning
2. Lower `btc_min_lag_pct`: `0.5` → `0.3` — smaller lag counts
3. Lower `SCORE_THRESHOLD` (line 63)

### Better quality
1. Raise `btc_min_move_pct`: `1.0` → `1.5` — only strong BTC moves
2. Raise `btc_min_lag_pct`: `0.5` → `1.0` — need bigger lag to enter
3. Add correlation filter: only trade alts with high historical BTC correlation

### Change exit logic
Currently exits when lag shrinks below `btc_catch_up_pct` (0.2%). To modify:
- Tighten: lower `btc_catch_up_pct` to `0.1` — exit later (ride longer)
- Loosen: raise to `0.5` — exit sooner
- Add time-based exit: close after N hours regardless of lag
- Add trend reversal exit: close if BTC reverses direction

### Change BTC move calculation
Currently uses 1h change (close[-2] to close[-1]). To use longer window:
- Change to use close[-5] to close[-1] for 4h equivalent
- Or use actual 4h candles (add to `MarketConfig.intervals`)

### Add correlation filter
To only trade high-correlation alts, add in entry methods:
```python
# Calculate rolling correlation with BTC
if correlation < min_correlation:
    return None  # Skip low-correlation alts
```

## Performance Context

Read `.claude/skills/_base_strategy_analysis.md` for SQL queries. Use `{STRATEGY_TYPE}` = `btc_correlation`.
