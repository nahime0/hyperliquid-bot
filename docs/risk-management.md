# Risk Management

## Architecture

The risk management system is composed of three cooperating components:

1. **RiskManager** (`risk/risk_manager.py`) — Central validator with veto power
2. **PositionTracker** (`risk/position_tracker.py`) — Position management, SL/TP, trailing stop
3. **PositionSizer** (`risk/position_sizer.py`) — Position size calculation (Kelly Criterion)

The Risk Manager has **absolute veto power** over every decision. Even if the strategy and AI say BUY, the Risk Manager can block it.

## Kill Switch

The bot **stops completely** if any of these conditions is true:

| Condition | Threshold | Action |
|---|---|---|
| Total drawdown | >= 15% from peak | Kill switch (stops everything) |
| Balance | < 50 USDC | Kill switch |
| Consecutive losses | >= 5 | Kill switch |

Once activated, the kill switch requires a **manual reset**.

Drawdown is calculated from the historical peak balance:
```
drawdown_pct = (peak_balance - current_balance) / peak_balance * 100
```

## Daily Pause

If the intra-day drawdown exceeds **5%**, new entries (BUY and SHORT) are blocked until the next day (reset at midnight UTC). CLOSE is always allowed.

```
daily_drawdown = (start_of_day_balance - current_balance) / start_of_day_balance * 100
```

## Validation Flow

Every decision passes through `validate_decision()`:

1. **HOLD** -> pass-through (always approved)
2. **CLOSE/SELL** -> check minimum holding period (15 min)
3. **Kill switch** -> blocked if active
4. **Daily pause** -> blocked if active (BUY/SHORT only)
5. **Symbol required** -> blocked if not specified
6. **Max positions** -> blocked if >= max positions (dynamic or static)
7. **Duplicate** -> blocked if position already exists on the same coin
8. **Spread check** -> blocked if bid-ask spread > max_spread_pct (BUY/SHORT)
9. **Min balance** -> blocked if balance < 50 USDC
10. **Min confidence** -> blocked if confidence < 0.5
11. **Position sizing** -> Kelly Criterion calculation
12. **Auto SL/TP** -> applied automatically if not specified

### Automatic SL (direction-aware)

If the decision doesn't include SL, it's calculated automatically:

| Direction | Stop Loss |
|---|---|
| BUY (LONG) | price * (1 - stop_loss_pct / 100) |
| SHORT | price * (1 + stop_loss_pct / 100) |

Default: SL = 1%.

### Take Profit (optional)

By default `AUTO_TAKE_PROFIT=false`: the trailing stop is the primary profit-taking mechanism. With `AUTO_TAKE_PROFIT=true`, an automatic TP is also generated:

| Direction | Take Profit |
|---|---|
| BUY (LONG) | price * (1 + take_profit_pct / 100) |
| SHORT | price * (1 - take_profit_pct / 100) |

The AI advisor can still specify an explicit TP regardless of this setting.

### Max Positions (dynamic)

With `DYNAMIC_POSITIONS=true` (default), the maximum number of positions scales with balance:

```
max_positions = min(MAX_OPEN_POSITIONS, balance // USDC_PER_POSITION)
max_positions = max(1, max_positions)  # floor at 1
```

Example with `USDC_PER_POSITION=25`, `MAX_OPEN_POSITIONS=15`:
| Balance | Max Positions |
|---|---|
| 10 USDC | 1 (floor) |
| 50 USDC | 2 |
| 100 USDC | 4 |
| 375 USDC | 15 (cap) |
| 1000 USDC | 15 (cap) |

## Capital Utilization

The bot tracks capital utilization (margin used / balance) and adapts sizing dynamically.

### Configuration

| Parameter | Default | Description |
|---|---|---|
| `TARGET_UTILIZATION` | `0.50` | Target 50% of balance as margin |
| `MAX_SIZE_BOOST` | `2.5` | Max multiplier on base sizing |

### Boost Formula

When utilization is below target, sizing is amplified:

```
utilization = total_margin_used / balance
if utilization < target:
    boost = min(target / max(utilization, 0.05), max_size_boost)
else:
    boost = 1.0  # no amplification
```

The boost is applied to the `size_pct` calculated by Kelly (or cold-start) **before** the hard cap `max_trade_pct`.

### Example

With balance=990, margin_used=70 (7% utilization), target=50%:
- `boost = min(0.50 / 0.07, 2.5) = min(7.14, 2.5) = 2.5`
- Cold-start 5% -> 5% * 2.5 = 12.5% (below hard cap 15%)

### SCALE_UP

The AI advisor can recommend `SCALE_UP` on profitable positions:
- Adds margin to the existing position in the same direction
- Entry price is recalculated as VWAP (weighted average)
- Blocked on losing positions
- Validated by Risk Manager (sizing, balance, utilization)

## Position Sizing (Kelly Criterion)

### Formula

```
f = (win_rate * payoff_ratio - (1 - win_rate)) / payoff_ratio
size = bankroll * (f / 4)  # quarter-Kelly (conservative)
```

### Cold Start (< 10 trades)

When there's not enough history for Kelly:
- Fixed size: **5% of bankroll**
- AI hint as soft cap (takes the minimum between 5% and AI suggestion)
- Hard cap: `max_trade_pct` (default 10%)

### Post Cold Start

- Kelly fraction calculated from historical win_rate, avg_win, avg_loss
- Quarter-Kelly for conservatism
- Scaled by AI confidence
- AI suggestion as soft cap
- Hard cap from config
- Floor: min 10 USDC (smaller orders are discarded)

### Notional Calculation

```python
margin = size_usdc  # from Kelly output
notional = margin * leverage  # e.g. margin 10 USDC * 2x = 20 USDC notional
qty = notional / price  # asset quantity
qty = round_size(coin, qty)  # rounded to szDecimals
```

## Position Tracking

### Open Position

Every BUY or SHORT creates a position in the DB with:
- `entry_price`, `quantity`, `stop_loss`, `take_profit`
- `direction` (LONG or SHORT)
- `leverage`
- `max_price_seen` (for LONG trailing)
- `min_price_seen` (for SHORT trailing)
- `original_sl` (original SL, unmodified by trailing)

### Close Position

Direction-aware PnL:
```python
if direction == "LONG":
    pnl = (exit_price - entry_price) * quantity
else:  # SHORT
    pnl = (entry_price - exit_price) * quantity

# Fee estimate (Hyperliquid taker: 0.045% per leg)
fee = (entry_price + exit_price) * quantity * 0.00045
pnl_net = pnl - fee
```

## Trailing Stop

### LONG Trailing

Tracks the maximum price seen (`max_price_seen`). SL can only rise, never fall.

| Phase | Trigger (gain_pct) | SL |
|---|---|---|
| Fixed | < 1.0% | Original SL (doesn't move) |
| Break-even | >= 1.0% | SL = entry price |
| Trailing | >= 1.5% | SL = max_price * (1 - 1.0%) |
| Tight trailing | >= 2.5% | SL = max_price * (1 - 0.75%) |

```
gain_pct = (max_price_seen - entry_price) / entry_price * 100
```

### SHORT Trailing

Tracks the minimum price seen (`min_price_seen`). SL can only fall, never rise.

| Phase | Trigger (gain_pct) | SL |
|---|---|---|
| Fixed | < 1.0% | Original SL |
| Break-even | >= 1.0% | SL = entry price |
| Trailing | >= 1.5% | SL = min_price * (1 + 1.0%) |
| Tight trailing | >= 2.5% | SL = min_price * (1 + 0.75%) |

```
gain_pct = (entry_price - min_price_seen) / entry_price * 100
```

## Time Stop

Positions open for more than `time_stop_hours` (default 4h) with PnL below `time_stop_min_pnl_pct` (default 0.5%) are automatically closed.

Direction-aware PnL:
```python
if direction == "LONG":
    pnl_pct = (current_price - entry) / entry * 100
else:
    pnl_pct = (entry - current_price) / entry * 100
```

## Liquidation Monitoring

At every refresh, the Risk Manager checks the distance from the liquidation price for every open position:

```python
distance_pct = abs(entry_price - liquidation_price) / entry_price * 100
if distance_pct < liquidation_buffer_pct:  # default 5%
    logger.warning("LIQUIDATION WARNING: ...")
```

With 2x leverage and 1% SL, liquidation risk is practically zero (it would require a 50% move).

## Cooldown

| Type | Duration | Trigger |
|---|---|---|
| Per-symbol | 30 min | After a loss on the same coin |
| Global | 15 min | After 3 consecutive losses |

Cooldown prevents "revenge trading" after losses.

## Liquidity Filters

### Discovery: 24h Volume Filter

Coin discovery uses `meta_and_asset_ctxs()` to get the real 24h volume (`dayNtlVlm`) for each asset. Coins with volume below `MIN_PAIR_VOLUME` (default 50k USDC) are excluded. Coins are sorted by descending volume, with the most liquid ones having priority. CORE_COINS (BTC, ETH, SOL) are included only if they pass the volume filter.

### Pre-entry Spread Check

Before every BUY, SHORT, or SCALE_UP, the Risk Manager checks the bid-ask spread via L2 order book:

```python
spread_pct = (best_ask - best_bid) / mid * 100
if spread_pct > max_spread_pct:  # default 0.5%
    block("Spread too wide")
```

| Parameter | Default | Description |
|---|---|---|
| `MAX_SPREAD_PCT` | `0.5` | Max spread % for entry. 0 = disabled. |

Graceful degradation: if the L2 snapshot fails, the entry is still allowed.

### Candle Volume Floor (Strategies)

Each strategy (Mean Reversion, RSI Divergence) checks the USDC volume of the current candle before generating signals:

```python
volume_usdc = close * volume  # last candle
if volume_usdc < min_candle_volume_usdc:  # default 10,000 USDC
    skip  # no signal generated
```

This prevents signals on coins with "staircase" charts and zero volume (e.g. POLYX).

## Anti-Churning

The main loop implements two anti-churning protections:

1. **Cross-decision:** If the same cycle has CLOSE and BUY/SHORT on the same coin, the entry is discarded
2. **Intra-cycle:** If a coin is closed during the cycle, it cannot be reopened in the same cycle
