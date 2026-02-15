# Base Strategy Analysis — Shared Reference

Reference file for all `/strategy-*` skills. Contains SQL queries for performance context and common modification patterns.

**Database**: `data/trading_bot.db` (SQLite, WAL mode, readonly)
**Config**: `config/settings.py` — `StrategyConfig` (line 112), env var mapping (lines 264-321)

---

## Performance SQL Queries

Use these to understand current performance before making changes. Replace `{STRATEGY_TYPE}` with the strategy name.

### Performance Summary
```sql
SELECT COUNT(*) AS trades, SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) AS wins,
  ROUND(100.0 * SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) / MAX(COUNT(*), 1), 1) AS win_rate,
  ROUND(SUM(pnl), 4) AS total_pnl, ROUND(AVG(pnl), 4) AS avg_pnl,
  ROUND(MAX(pnl), 4) AS best, ROUND(MIN(pnl), 4) AS worst,
  ROUND(SUM(CASE WHEN pnl > 0 THEN pnl ELSE 0 END) / MAX(ABS(SUM(CASE WHEN pnl < 0 THEN pnl ELSE 0 END)), 0.0001), 2) AS profit_factor
FROM positions WHERE strategy = '{STRATEGY_TYPE}' AND status = 'CLOSED';
```

### PnL by Direction
```sql
SELECT direction, COUNT(*) AS trades, ROUND(SUM(pnl), 4) AS pnl,
  ROUND(100.0 * SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) / MAX(COUNT(*), 1), 1) AS win_rate
FROM positions WHERE strategy = '{STRATEGY_TYPE}' AND status = 'CLOSED' GROUP BY direction;
```

### Close Reason Breakdown
```sql
SELECT close_reason, COUNT(*) AS count, ROUND(SUM(pnl), 4) AS pnl, ROUND(AVG(pnl), 4) AS avg
FROM positions WHERE strategy = '{STRATEGY_TYPE}' AND status = 'CLOSED' GROUP BY close_reason ORDER BY count DESC;
```

### Signal & Conversion (last 7 days)
```sql
SELECT event_type, COUNT(*) FROM events
WHERE source = '{STRATEGY_TYPE}' AND event_type IN ('SIGNAL','RISK_BLOCKED','RISK_APPROVED','TRADE_ENTRY')
  AND timestamp >= datetime('now', '-7 days') GROUP BY event_type;
```

### Score Distribution
```sql
SELECT CASE
    WHEN json_extract(details, '$.score') >= 0.90 THEN '0.90+'
    WHEN json_extract(details, '$.score') >= 0.80 THEN '0.80-0.89'
    WHEN json_extract(details, '$.score') >= 0.70 THEN '0.70-0.79'
    WHEN json_extract(details, '$.score') >= 0.60 THEN '0.60-0.69'
    ELSE 'below 0.60'
  END AS bucket, COUNT(*) AS count
FROM events WHERE source = '{STRATEGY_TYPE}' AND event_type = 'SIGNAL'
  AND json_extract(details, '$.score') IS NOT NULL GROUP BY bucket ORDER BY bucket;
```

---

## Common Code Modification Patterns

### Pattern 1: Change scoring weights
All scoring strategies have `W_*` constants at the top of the file. **Weights must sum to 1.0.**

```python
# Example: increase RSI weight, decrease cooldown
W_RSI = 0.30      # was 0.25
W_BB = 0.25
W_VOLUME = 0.15
W_MACRO_RSI = 0.15
W_FUNDING = 0.10
W_COOLDOWN = 0.05  # was 0.10 — redistributed to RSI
```

### Pattern 2: Change score threshold
Each strategy has `SCORE_THRESHOLD` just after the weights. Lower = more signals, higher = fewer/better.

```python
SCORE_THRESHOLD = 0.55  # was 0.60 — allow lower-quality signals through
```

### Pattern 3: Add/remove hard blocks
Hard blocks are in `_check_long_entry()` / `_check_short_entry()`, before the scoring section. They return `None` early.

```python
# To remove a hard block: delete or comment the block
# if self._trend_filter.get(symbol) == "BEARISH":
#     return None

# To add a hard block:
if some_condition:
    return None
```

### Pattern 4: Add a new scoring component
1. Add `W_NEW = 0.10` constant (redistribute from others to keep sum = 1.0)
2. In the scoring section, compute the component and add to score:
```python
# New component
if new_condition:
    score += W_NEW
    parts.append("new_component")
```

### Pattern 5: Change entry/exit indicators
Indicators are computed in `update()` and stored in `self._data[symbol]`. Entry methods read from there.

```python
# Example: change RSI period from 14 to 10
rsi = ta.momentum.RSIIndicator(close, window=10).rsi()
```

### Pattern 6: Add new config parameter
1. Add field to `StrategyConfig` in `config/settings.py` (line ~112-170)
2. Add env var mapping in `load_settings()` (line ~264-321)
3. Read in strategy constructor: `self._my_param = strategy_config.my_param`

### Pattern 7: Change intervals
Intervals are set in `update()` via `self._md.get_candles(symbol, interval)`. To change:
```python
# Switch mean reversion from 15m to 5m
candles_15m = self._md.get_candles(symbol, "5m")  # was "15m"
```
Note: the interval must be in `MarketConfig.intervals` tuple (line 108) to be subscribed via WebSocket.

### Pattern 8: Modify exit logic
Exit methods (`_check_long_exit`, `_check_short_exit`) return a Decision with `action="CLOSE"` or `None`.
```python
async def _check_long_exit(self, symbol: str) -> Decision | None:
    # Add new exit condition
    if new_exit_condition:
        return Decision(coin=symbol, action="CLOSE", confidence=0.8,
                       reasoning="New exit reason", strategy_type="my_strategy")
    return None
```

### Pattern 9: Disable a strategy
Set env var `ACTIVE_STRATEGIES` without it, or edit `config/settings.py` line 113-117.

### Pattern 10: Add state tracking
Some strategies (bb_squeeze, btc_correlation) track state per symbol. Pattern:
```python
def __init__(self, ...):
    self._state: dict[str, Any] = {}

async def update(self):
    for symbol in symbols:
        self._state[symbol] = computed_value
```
