---
name: strategy-bb-squeeze
description: Analyze and modify BB squeeze strategy — squeeze detection, breakout scoring, volume thresholds
user-invocable: true
---

# Bollinger Band Squeeze Strategy — Analyze & Modify

**Source**: `strategies/bb_squeeze.py`
**Strategy type**: `bb_squeeze`
**Thesis**: Tight BB (squeeze) precedes explosive breakouts. Enter on squeeze exit with volume.

## Code Map

| What | Location |
|------|----------|
| Weights `W_*` | Lines 57-62 |
| `SCORE_THRESHOLD` | Line 63 (default 0.60) |
| `update()` | Line 150 — computes 15m BB, tracks squeeze state |
| `_squeeze_history` | Dict tracking per-symbol squeeze state over time |
| `_check_long_entry()` | Line 310 — hard blocks (316-329), scoring (331-366) |
| `_check_short_entry()` | Line 397 — hard blocks (403-416), scoring (418-453) |
| `_check_long_exit()` | Line 484 — price < BB midline (SMA20) |
| `_check_short_exit()` | Line 500 — price > BB midline (SMA20) |

## Scoring Weights (lines 57-62)

```
W_SQUEEZE    = 0.25   Tightness (1.0=tight, 0.0=wide)
W_BREAKOUT   = 0.25   Distance past band on breakout
W_VOLUME     = 0.20   Volume spike >= config threshold
W_TREND      = 0.10   1h trend alignment
W_FUNDING    = 0.10   Funding acceptable
W_COOLDOWN   = 0.10   No per-symbol cooldown
```

## Hard Blocks

| Block | Long (line) | Short (line) |
|-------|-------------|--------------|
| `check_hard_blocks()` | 316 | 403 |
| Trend: BEARISH blocks LONG | 327 | — |
| Trend: BULLISH blocks SHORT | — | 414 |

## Config (`config/settings.py`)

| Field | Line | Env Var | Default |
|-------|------|---------|---------|
| `bbs_squeeze_percentile` | 138 | `BBS_SQUEEZE_PERCENTILE` | `20.0` |
| `bbs_lookback_bars` | 139 | `BBS_LOOKBACK_BARS` | `100` |
| `bbs_min_volume_spike` | 140 | `BBS_MIN_VOLUME_SPIKE` | `1.5` |

## How to Modify

### More signals
1. Raise `bbs_squeeze_percentile`: `20.0` → `30.0` — less tight squeeze required
2. Lower `bbs_min_volume_spike`: `1.5` → `1.2` — less volume needed on breakout
3. Lower `SCORE_THRESHOLD` (line 63): `0.60` → `0.50`

### Better quality
1. Lower `bbs_squeeze_percentile`: `20.0` → `10.0` — only tightest squeezes
2. Raise `bbs_min_volume_spike`: `1.5` → `2.0` — stronger volume confirmation
3. Increase `W_VOLUME` weight, decrease `W_COOLDOWN`

### Change squeeze detection
The squeeze state is tracked in `_squeeze_history`. Currently uses BB bandwidth percentile. To modify:
- Change how tightness is calculated in `update()`
- Add Keltner Channel overlap (classic TTM Squeeze uses BB inside KC)
- Track number of consecutive squeeze bars before breakout

### Change exit logic
Currently exits at BB midline (SMA20). Alternatives:
- Exit at opposite BB band (ride the full move)
- Exit when momentum indicator reverses
- Time-based exit after N bars

### Stateful behavior
This strategy tracks `_squeeze_history` per symbol. Be careful when modifying `update()` — state must persist across cycles for squeeze exit detection to work.

## Performance Context

Read `.claude/skills/_base_strategy_analysis.md` for SQL queries. Use `{STRATEGY_TYPE}` = `bb_squeeze`.
