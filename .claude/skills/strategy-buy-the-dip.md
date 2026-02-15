---
name: strategy-buy-the-dip
description: Analyze and modify buy-the-dip strategy — dip range, trend filter, exit logic
user-invocable: true
---

# Buy the Dip Strategy — Analyze & Modify

**Source**: `strategies/buy_the_dip.py`
**Strategy type**: `buy_the_dip`
**Thesis**: Sharp dips in an uptrend are buying opportunities. LONG only.

## Code Map

| What | Location |
|------|----------|
| Weights `W_*` | Lines 49-54 |
| `SCORE_THRESHOLD` | Line 55 (default 0.60) |
| `update()` | Line 137 — computes 5m candles + 4h EMA trend |
| 4h trend (EMA12/EMA26) | Computed from 1h candles in `update()` |
| `_check_long_entry()` | Line 280 — hard blocks (286-298), scoring (313-350) |
| `_check_exit()` | Line 380 — RSI > 70 or price >= pre_dip_high |

**LONG only** — no `_check_short_entry()` method.

## Scoring Weights (lines 49-54)

```
W_DIP        = 0.30   Dip magnitude (0.5%-3% range)
W_TREND_4H   = 0.20   4h trend BULLISH
W_TREND_1H   = 0.15   1h trend BULLISH
W_VOLUME     = 0.15   Volume spike >= 1.5x
W_FUNDING    = 0.10   Funding acceptable
W_COOLDOWN   = 0.10   No per-symbol cooldown
```

## Hard Blocks

| Block | Line |
|-------|------|
| `check_hard_blocks()` | 286 |
| **4h trend NOT BULLISH** | 296 (unique to this strategy) |

## Config (`config/settings.py`)

| Field | Line | Env Var | Default |
|-------|------|---------|---------|
| `btd_dip_min_pct` | 130 | `BTD_DIP_MIN_PCT` | `0.5` (%) |
| `btd_dip_max_pct` | 131 | `BTD_DIP_MAX_PCT` | `3.0` (%) |
| `btd_volume_spike` | 132 | `BTD_VOLUME_SPIKE` | `1.5` |

## Class Constants

`EMA_FAST_4H=12`, `EMA_SLOW_4H=26`, `SLOPE_WINDOW_4H=5`, `RSI_PERIOD=14`

## How to Modify

### More signals
1. Widen dip range: lower `btd_dip_min_pct` to `0.3`, raise `btd_dip_max_pct` to `5.0`
2. Remove 4h BULLISH hard block (line 296) — allow dip-buying in any trend
3. Lower `btd_volume_spike`: `1.5` → `1.2`
4. Lower `SCORE_THRESHOLD` (line 55)

### Better quality
1. Narrow dip range: `btd_dip_min_pct=1.0`, `btd_dip_max_pct=2.5` — sweet spot only
2. Raise volume spike: `1.5` → `2.0` — stronger capitulation signal
3. Add RSI confirmation: require RSI < 30 in addition to dip

### Change exit logic
Edit `_check_exit()` (line 380). Currently exits on:
- RSI(5m) > 70 (overbought recovery)
- Price >= `pre_dip_high` (frozen high from 3 bars before signal)

To modify:
- Change RSI exit threshold: `70` → `60` (exit earlier) or `80` (ride longer)
- Use % gain target instead of pre_dip_high: exit when +1.5% from entry
- Add time-based exit: close after N minutes if no recovery

### Add SHORT support
Currently LONG-only. To add SHORT (sell-the-rip):
1. Add `_check_short_entry()` mirroring LONG logic but for pumps in downtrends
2. Require 4h trend BEARISH for SHORT
3. Exit on RSI < 30 or price <= pre_pump_low

### Pre-dip high behavior
`pre_dip_high` is frozen at signal time (3 bars before current). This was fixed in commit `c398588`. If changing dip detection window, also adjust the pre_dip_high bar offset.

## Performance Context

Read `.claude/skills/_base_strategy_analysis.md` for SQL queries. Use `{STRATEGY_TYPE}` = `buy_the_dip`.
