---
name: strategy-volume-spike
description: Analyze and modify volume spike strategy — spike threshold, candle pattern detection, BB level
user-invocable: true
---

# Volume Spike Reversal Strategy — Analyze & Modify

**Source**: `strategies/volume_spike.py`
**Strategy type**: `volume_spike`
**Thesis**: Extreme volume spikes with reversal candle patterns (hammer/inverted hammer) signal exhaustion.

## Code Map

| What | Location |
|------|----------|
| Weights `W_*` | Lines 57-62 |
| `SCORE_THRESHOLD` | Line 63 (default 0.60) |
| `update()` | Line 159 — computes 5m candles, BB, volume SMA |
| Candle pattern detection | In entry methods, wick/body ratio analysis |
| `_check_long_entry()` | Line 323 — hard blocks (333-341), scoring (343-376) |
| `_check_short_entry()` | Line 405 — hard blocks (415-423), scoring (425-458) |
| `_check_long_exit()` | Line 487 — RSI > 70 |
| `_check_short_exit()` | Line 500 — RSI < 30 |

## Scoring Weights (lines 57-62)

```
W_SPIKE       = 0.30   Volume spike magnitude (graduated by ratio)
W_PATTERN     = 0.25   Reversal candle pattern (wick/body ratio)
W_PRICE_LEVEL = 0.15   Price near BB extreme (< 0.3 %B or > 0.7 %B)
W_TREND       = 0.10   1h trend not opposing
W_FUNDING     = 0.10   Funding acceptable
W_COOLDOWN    = 0.10   No per-symbol cooldown
```

## Hard Blocks

| Block | Long (line) | Short (line) |
|-------|-------------|--------------|
| `check_hard_blocks()` | 333 | 415 |

## Config (`config/settings.py`)

| Field | Line | Env Var | Default |
|-------|------|---------|---------|
| `vs_spike_threshold` | 145 | `VS_SPIKE_THRESHOLD` | `3.0` (x SMA) |
| `vs_wick_ratio` | 146 | `VS_WICK_RATIO` | `2.0` |

## Class Constants

`RSI_PERIOD=14`, `BB_PERIOD=20`, `BB_STD=2.0`

## Candle Pattern Detection

| Pattern | Direction | Condition |
|---------|-----------|-----------|
| Hammer | LONG | close > open + lower_wick > body × wick_ratio |
| Inverted Hammer | SHORT | close < open + upper_wick > body × wick_ratio |

## How to Modify

### More signals
1. Lower `vs_spike_threshold`: `3.0` → `2.0` — accept smaller volume spikes
2. Lower `vs_wick_ratio`: `2.0` → `1.5` — less strict candle pattern
3. Widen BB threshold: `0.3` → `0.4` for %B (accept price further from extreme)
4. Lower `SCORE_THRESHOLD` (line 63)

### Better quality
1. Raise `vs_spike_threshold`: `3.0` → `5.0` — only extreme spikes
2. Raise `vs_wick_ratio`: `2.0` → `3.0` — clearer reversal patterns
3. Add multiple-candle confirmation: require 2 consecutive reversal candles
4. Add RSI oversold/overbought as entry hard block

### Change candle pattern detection
Currently uses simple wick/body ratio. To improve:
- Add doji detection (very small body, long wicks both sides)
- Add engulfing pattern (current candle body engulfs prior)
- Add morning/evening star (3-candle pattern)

Example to add engulfing:
```python
# Bullish engulfing: prior red + current green covering prior body
if (prior_close < prior_open and  # prior is red
    close > open and              # current is green
    open <= prior_close and       # current opens at/below prior close
    close >= prior_open):         # current closes at/above prior open
    pattern_score = W_PATTERN
```

### Change exit logic
Currently RSI-based (> 70 for LONG, < 30 for SHORT). Alternatives:
- BB midline exit (like bb_squeeze)
- Volume dry-up exit (volume drops below average after spike)
- Time-based: exit within N bars (spike is short-lived event)

### Change interval
Currently 5m for fast spike detection. For less noise:
- Switch to 15m (fewer but clearer spikes)
- Require spike on both 5m AND 15m (dual confirmation)

## Performance Context

Read `.claude/skills/_base_strategy_analysis.md` for SQL queries. Use `{STRATEGY_TYPE}` = `volume_spike`.
