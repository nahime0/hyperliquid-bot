---
name: strategies
description: Compare all 13 strategies, identify what to tune/disable/boost, modify code
user-invocable: true
---

# Strategy Dashboard — Compare, Tune, Modify

Compare all 13 strategies and decide what to change. DB: `data/trading_bot.db` (readonly).

## Step 1: Run diagnostic queries

### Strategy Leaderboard
```sql
SELECT strategy, COUNT(*) AS trades,
  SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) AS wins,
  ROUND(100.0 * SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) / MAX(COUNT(*), 1), 1) AS win_rate,
  ROUND(SUM(pnl), 4) AS total_pnl,
  ROUND(SUM(CASE WHEN pnl > 0 THEN pnl ELSE 0 END) / MAX(ABS(SUM(CASE WHEN pnl < 0 THEN pnl ELSE 0 END)), 0.0001), 2) AS pf
FROM positions WHERE status = 'CLOSED' GROUP BY strategy ORDER BY total_pnl DESC;
```

### Signal Volume & Conversion (7 days)
```sql
SELECT source AS strategy,
  COUNT(*) FILTER (WHERE event_type = 'SIGNAL') AS signals,
  COUNT(*) FILTER (WHERE event_type = 'RISK_BLOCKED') AS blocked,
  COUNT(*) FILTER (WHERE event_type = 'TRADE_ENTRY') AS entries
FROM events WHERE event_type IN ('SIGNAL','RISK_BLOCKED','RISK_APPROVED','TRADE_ENTRY')
  AND timestamp >= datetime('now', '-7 days') GROUP BY source ORDER BY signals DESC;
```

### Direction Breakdown
```sql
SELECT strategy, direction, COUNT(*) AS trades, ROUND(SUM(pnl), 4) AS pnl
FROM positions WHERE status = 'CLOSED' GROUP BY strategy, direction ORDER BY strategy;
```

### Close Reasons
```sql
SELECT strategy, close_reason, COUNT(*) AS count, ROUND(SUM(pnl), 4) AS pnl
FROM positions WHERE status = 'CLOSED' GROUP BY strategy, close_reason ORDER BY strategy, count DESC;
```

## Step 2: Identify actions

Based on query results, classify each strategy:

| Category | Criteria | Action |
|----------|----------|--------|
| **Disable** | Negative PnL, low win rate, no improvement path | Remove from `ACTIVE_STRATEGIES` |
| **Tune** | Decent signals but poor PnL or conversion | Adjust weights/thresholds |
| **Boost** | Positive PnL, good win rate | Keep or relax threshold for more signals |
| **Silent** | Very few signals in 7 days | Lower threshold or relax hard blocks |

## Step 3: Make changes

### Disable a strategy
Edit `config/settings.py:113-117` — remove from `active_strategies` tuple.
Or set env var: `ACTIVE_STRATEGIES=mean_reversion,trend_following,...` (CSV without the one to disable).

### Tune a strategy
Use the specific skill (`/strategy-<name>`) for detailed guidance. Key files:

| Strategy | Source | Weights (lines) | Threshold |
|----------|--------|-----------------|-----------|
| mean_reversion | `strategies/mean_reversion.py` | 70-75 | L76 |
| rsi_divergence | `strategies/rsi_divergence.py` | *(no scoring)* | — |
| trend_following | `strategies/trend_following.py` | 55-60 | L61 |
| bb_squeeze | `strategies/bb_squeeze.py` | 57-62 | L63 |
| breakout | `strategies/breakout.py` | 56-61 | L62 |
| btc_correlation | `strategies/btc_correlation.py` | 57-62 | L63 |
| buy_the_dip | `strategies/buy_the_dip.py` | 49-54 | L55 |
| ema_crossover | `strategies/ema_crossover.py` | 56-61 | L62 |
| funding_rate | `strategies/funding_rate.py` | 55-60 | L61 |
| macd_divergence | `strategies/macd_divergence.py` | 61-66 | L67 |
| mtf_confluence | `strategies/mtf_confluence.py` | 58-63 | L64 |
| session_momentum | `strategies/session_momentum.py` | 65-70 | L71 |
| volume_spike | `strategies/volume_spike.py` | 57-62 | L63 |

### Config parameters
All in `config/settings.py` `StrategyConfig` (lines 112-170), env var mapping (lines 264-321).

### Common modifications
See `.claude/skills/_base_strategy_analysis.md` for 10 code modification patterns (weights, thresholds, hard blocks, new components, intervals, exit logic, state tracking).

## All strategy skills

| Skill | Strategy type |
|-------|--------------|
| `/strategy-mean-reversion` | `mean_reversion` |
| `/strategy-rsi-divergence` | `rsi_divergence` |
| `/strategy-trend-following` | `trend_following` |
| `/strategy-bb-squeeze` | `bb_squeeze` |
| `/strategy-breakout` | `breakout` |
| `/strategy-btc-correlation` | `btc_correlation` |
| `/strategy-buy-the-dip` | `buy_the_dip` |
| `/strategy-ema-crossover` | `ema_crossover` |
| `/strategy-funding-rate` | `funding_rate` |
| `/strategy-macd-divergence` | `macd_divergence` |
| `/strategy-mtf-confluence` | `mtf_confluence` |
| `/strategy-session-momentum` | `session_momentum` |
| `/strategy-volume-spike` | `volume_spike` |
