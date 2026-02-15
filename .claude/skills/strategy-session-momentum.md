---
name: strategy-session-momentum
description: Analyze and modify session momentum strategy — session change threshold, entry window, time-based exit
user-invocable: true
---

# Session Momentum Strategy — Analyze & Modify

**Source**: `strategies/session_momentum.py`
**Strategy type**: `session_momentum`
**Thesis**: Momentum from prior trading session carries into the next. Trade the continuation.

## Code Map

| What | Location |
|------|----------|
| Weights `W_*` | Lines 65-70 |
| `SCORE_THRESHOLD` | Line 71 (default 0.60) |
| `update()` | Line 180 — detects session, computes 1h candle change |
| Session definitions | Asia 00-08 UTC, EU 08-16 UTC, US 16-24 UTC |
| Entry window check | In `update()`, first N hours of new session |
| `_check_long_entry()` | Line 315 — hard blocks (325-333), scoring (337-372) |
| `_check_short_entry()` | Line 402 — hard blocks (412-420), scoring (424-459) |
| `_check_exit()` | Line 489 — time-based + momentum reversal |

## Scoring Weights (lines 65-70)

```
W_SESSION_MOMENTUM = 0.30   Prior session change meets threshold
W_OVERNIGHT_CHANGE = 0.20   RSI confirms direction
W_VOLUME           = 0.15   Volume confirmation
W_TREND            = 0.15   1h trend alignment
W_FUNDING          = 0.10   Funding acceptable
W_COOLDOWN         = 0.10   No per-symbol cooldown
```

## Hard Blocks

| Block | Long (line) | Short (line) |
|-------|-------------|--------------|
| `check_hard_blocks()` | 325 | 412 |

## Config (`config/settings.py`)

| Field | Line | Env Var | Default |
|-------|------|---------|---------|
| `sm_min_session_change` | 164 | `SM_MIN_SESSION_CHANGE` | `0.5` (%) |
| `sm_entry_window_hours` | 165 | `SM_ENTRY_WINDOW_HOURS` | `2` |
| `sm_exit_hours` | 166 | `SM_EXIT_HOURS` | `4.0` |

## Class Constants

`SESSION_BARS=8`, `RSI_PERIOD=14`

## How to Modify

### More signals
1. Lower `sm_min_session_change`: `0.5` → `0.3` — smaller session moves count
2. Widen `sm_entry_window_hours`: `2` → `4` — enter later in session
3. Lower `SCORE_THRESHOLD` (line 71)

### Better quality
1. Raise `sm_min_session_change`: `0.5` → `1.0` — only strong session moves
2. Narrow `sm_entry_window_hours`: `2` → `1` — only first hour of session
3. Add session volume filter: require above-average volume in prior session

### Change exit logic
Edit `_check_exit()` (line 489). Currently:
- Time-based: held >= `sm_exit_hours` (4h)
- Momentum reversal: session change reverses beyond threshold

To modify:
- Shorter hold: `sm_exit_hours=2.0` — faster exits
- Remove time-based: let momentum reversal be only exit
- Add trailing stop: instead of fixed time, trail a stop that tightens over time
- Session boundary exit: close at end of current session

### Change session definitions
Sessions are hardcoded (Asia/EU/US, 8h each). To change:
- Edit session hour ranges in `update()` method
- Add overlap sessions (e.g., Asia-EU overlap 06-10 UTC)
- Make sessions configurable via env vars

### Add session-specific behavior
Different sessions have different characteristics:
```python
# Example: only trade US session for higher volatility
if current_session != "US":
    return None
```

Or weight signals differently per session:
```python
session_boost = {"ASIA": 0.8, "EU": 1.0, "US": 1.2}
score *= session_boost[current_session]
```

## Performance Context

Read `.claude/skills/_base_strategy_analysis.md` for SQL queries. Use `{STRATEGY_TYPE}` = `session_momentum`.
Tip: check if signals cluster at session boundaries (they should).
