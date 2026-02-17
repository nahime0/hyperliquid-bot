# Trading Strategies

## Overview

The bot supports two operating modes:

### Live Trading (main bot — `main.py`)

The live bot uses a **MultiStrategy aggregator** that combines independent decisions from multiple strategies:

| Live Strategy | Timeframe | Class | CLI flag |
|---|---|---|---|
| **Mean Reversion** | 15m | `MeanReversionStrategy` | `--strategy mean_reversion` |
| **RSI Divergence** | 5m | `RSIDivergenceStrategy` | `--strategy rsi_div` |
| **Trend Following** | 1h | `TrendFollowingStrategy` | `--strategy trend_following` |
| **Multi (default)** | 5m + 15m + 1h | `MultiStrategy` | `--strategy multi` |

The `MultiStrategy` merger:
- Collects Decisions from each sub-strategy independently
- If 2+ strategies agree on BUY/SHORT for the same coin -> boost confidence (+0.1)
- If strategies conflict (BUY vs SHORT on the same coin) -> the signal with the **highest score** wins, with a `[Conflict: ...]` note in reasoning for the AI
- If only 1 strategy signals -> uses original confidence
- Never more than `max_open_positions` entries per cycle

### Backtesting (9+ strategies — `scripts/backtest_runner.py`)

The backtester supports all original strategies with parameter sweep:

Available strategies:

| Strategy | Type | Backtest Class | CLI flag |
|---|---|---|---|
| **Mean Reversion** | Mean reversion | `HyperliquidMeanRevRule` | `hyperliquid_mr` |
| **Scalp** | Relaxed mean reversion | `ScalpMeanRevRule` | `scalp` |
| **EMA Crossover + ADX** | Trend following | `EMACrossoverADXRule` | `ema_adx` |
| **BB Squeeze** | Volatility breakout | `BBSqueezeRule` | `bb_squeeze` |
| **MACD Momentum** | Momentum | `MACDMomentumRule` | `macd` |
| **Donchian Breakout** | Channel breakout | `DonchianBreakoutRule` | `donchian` |
| **Keltner Breakout** | ATR breakout | `KeltnerBreakoutRule` | `keltner` |
| **VWAP Reversion** | Intraday mean rev | `VWAPReversionRule` | `vwap_rev` |
| **RSI Divergence** | Divergence reversal | `RSIDivergenceRule` | `rsi_div` |
| **Stochastic Cross** | Oscillator cross | `StochasticCrossRule` | `stoch` |
| **Combined** | Multi-strategy consensus | `CombinedRule` | `combined` |

## Trend Filter (universal gate)

**All** strategies use the same 1h trend filter as a quality gate:

| Trend | Conditions |
|---|---|
| **BULLISH** | EMA50 > EMA200 AND price > EMA50 AND slope > 0 |
| **BEARISH** | EMA50 < EMA200 AND price < EMA50 AND slope < 0 |
| **NEUTRAL** | Everything else |

**Slope:** EMA50 change over the last 5 bars, normalized by price (as percentage).

Usage per strategy:
- **Directional** strategies (EMA cross, MACD, Donchian, Keltner, Stochastic): LONG only if BULLISH, SHORT only if BEARISH
- **Mean reversion** strategies (BB Squeeze, VWAP, RSI Div): LONG if BULLISH/NEUTRAL, SHORT if BEARISH/NEUTRAL

---

## Mean Reversion (`hyperliquid_mr`)

### Indicators

| Indicator | Timeframe | Parameters | Usage |
|---|---|---|---|
| RSI | 15m | Period 14 | Entry/exit trigger |
| RSI | 1h | Period 14 | Macro filter |
| Bollinger Bands | 15m | Period 20, 2 std dev | Extreme confirmation |
| Volume SMA | 15m | Period 20 | Volume confirmation |
| EMA50 / EMA200 | 1h | - | Trend filter |

### LONG Entry Conditions

1. Trend BULLISH (1h)
2. RSI(14) < 25
3. Price <= lower BB * 1.005
4. Volume ratio >= 1.2
5. RSI 1h < 60

### SHORT Entry Conditions

1. Trend BEARISH (1h)
2. RSI(14) > 75
3. Price >= upper BB * 0.995
4. Volume ratio >= 1.2
5. RSI 1h > 40

### Exit

- **LONG exit:** RSI > 70 OR price >= BB upper
- **SHORT exit:** RSI < 30 OR price <= BB lower

---

## EMA Crossover + ADX (`ema_adx`)

### Philosophy

Pure trend following: follows EMA crossovers confirmed by trend strength (ADX).

### Indicators

| Indicator | Parameters | Usage |
|---|---|---|
| EMA12 / EMA26 | Periods 12, 26 | Crossover signal |
| ADX | Period 14 | Trend strength confirmation |

### Entry Conditions

- **LONG:** trend BULLISH + EMA12 crosses above EMA26 + ADX > 20
- **SHORT:** trend BEARISH + EMA12 crosses below EMA26 + ADX > 20

### Exit

- Opposite EMA cross OR ADX < 15

---

## BB Squeeze (`bb_squeeze`)

### Philosophy

Identifies low volatility periods (squeeze) and trades the breakout when volatility expands.

### Indicators

| Indicator | Parameters | Usage |
|---|---|---|
| Bollinger Bands | Period 20, 2 std | Volatility channel |
| BB Bandwidth | - | Squeeze detection (< 2%) |
| Volume SMA | Period 20 | Breakout volume confirmation |

### Entry Conditions

- **Squeeze detected:** BB bandwidth < 2% for 3+ consecutive candles
- **LONG:** trend BULLISH/NEUTRAL + squeeze + price breaks BB upper + volume > 1.5x
- **SHORT:** trend BEARISH/NEUTRAL + squeeze + price breaks BB lower + volume > 1.5x

### Exit

- Price returns to BB mid OR bandwidth > 3%

---

## MACD Momentum (`macd`)

### Philosophy

Captures directional momentum through MACD crossovers with histogram confirmation.

### Indicators

| Indicator | Parameters | Usage |
|---|---|---|
| MACD line | Fast 12, Slow 26 | Direction |
| Signal line | Period 9 | Crossover trigger |
| Histogram | MACD - Signal | Momentum confirmation |

### Entry Conditions

- **LONG:** trend BULLISH + MACD > 0 + MACD crosses above signal + increasing histogram
- **SHORT:** trend BEARISH + MACD < 0 + MACD crosses below signal + decreasing histogram

### Exit

- Opposite MACD cross OR zero-line cross

---

## Donchian Breakout (`donchian`)

### Philosophy

Classic channel breakout (Turtle trading style): trades the break of period highs/lows after consolidation.

### Indicators

| Indicator | Parameters | Usage |
|---|---|---|
| Donchian Channel | Period 20 | High/low/mid |
| Consolidation range | 5 bars | Tight range confirmation |
| Volume SMA | Period 20 | Breakout confirmation |

### Entry Conditions

- **LONG:** trend BULLISH + price breaks 20-bar high + volume > 1.3x + 5+ bar consolidation (range < 1.5%)
- **SHORT:** trend BEARISH + price breaks 20-bar low + volume > 1.3x + 5+ bar consolidation

### Exit

- Price returns below Donchian mid

---

## Keltner Breakout (`keltner`)

### Philosophy

ATR-based (Average True Range) breakout — adaptive to current volatility.

### Indicators

| Indicator | Parameters | Usage |
|---|---|---|
| Keltner Channel | EMA20 +/- 2*ATR | Adaptive channel |
| Volume SMA | Period 20 | Breakout confirmation |

### Entry Conditions

- **LONG:** trend BULLISH + price breaks Keltner upper + volume > 1.2x
- **SHORT:** trend BEARISH + price breaks Keltner lower + volume > 1.2x

### Exit

- Price returns below Keltner mid

---

## VWAP Reversion (`vwap_rev`)

### Philosophy

Intraday mean reversion around VWAP (Volume Weighted Average Price). Looks for extreme deviations from the volume-weighted average price.

### Indicators

| Indicator | Parameters | Usage |
|---|---|---|
| VWAP | Rolling 96 bars (session) | Fair value average price |
| ATR | Period 14 | Deviation measure |
| RSI | Period 14 | Oversold/overbought confirmation |

### VWAP Calculation

```python
hlc3 = (high + low + close) / 3
VWAP = cumsum(volume * hlc3) / cumsum(volume)  # rolling window
```

### Entry Conditions

- **LONG:** trend BULLISH/NEUTRAL + price < VWAP - 2*ATR + RSI < 35
- **SHORT:** trend BEARISH/NEUTRAL + price > VWAP + 2*ATR + RSI > 65

### Exit

- Price returns to VWAP OR RSI normalizes (> 50 for long, < 50 for short)

---

## RSI Divergence (`rsi_div`)

### Philosophy

Identifies divergences between price and RSI (swing detection). A bullish divergence indicates that bearish pressure is exhausting; a bearish one indicates the opposite. **Top performer in backtesting**: +$47, 61% WR, 34/35 coins profitable.

### Indicators

| Indicator | Parameters | Usage |
|---|---|---|
| RSI | Period 14 (configurable) | Divergence detection |
| Swing detection | Window 5 bars (configurable) | Identifies pivot points |

### Entry Conditions

- **Bullish divergence (LONG):** trend BULLISH/NEUTRAL + price makes lower low + RSI makes higher low
- **Bearish divergence (SHORT):** trend BEARISH/NEUTRAL + price makes higher high + RSI makes lower high

### Exit

- RSI > 60 (long exit, configurable: `RSI_DIV_LONG_EXIT`) OR RSI < 40 (short exit, configurable: `RSI_DIV_SHORT_EXIT`)

### Configurable Parameters (`.env`)

| Variable | Default | Description |
|---|---|---|
| `RSI_DIV_PERIOD` | 14 | RSI period |
| `RSI_DIV_SWING_WINDOW` | 5 | Swing detection window (2*w+1 bars) |
| `RSI_DIV_LONG_EXIT` | 60.0 | RSI threshold for LONG exit |
| `RSI_DIV_SHORT_EXIT` | 40.0 | RSI threshold for SHORT exit |
| `PRIMARY_INTERVAL` | 5m | Primary timeframe for RSI Div |

### Live vs Backtest

- **Live** (`strategies/rsi_divergence.py`): `RSIDivergenceStrategy(Strategy)` — operates on 5m candles in real-time via WebSocket
- **Backtest** (`data/backtest.py`): `RSIDivergenceRule(TradingRule)` — same logic on historical data
- **Parameter sweep**: `.venv/bin/python -m scripts.backtest_runner --sweep --strategy rsi_div`

---

## Trend Following (`trend_following`) — LIVE

### Philosophy

Captures strong directional moves. Complementary to Mean Reversion: MR looks for bounces (counter-trend), TF follows the trend (trend-following). In a strong downtrend (BTC -3.5%), MR can't short (RSI doesn't reach overbought levels), but TF can.

### Indicators

| Indicator | Timeframe | Parameters | Usage |
|---|---|---|---|
| Price change 4h | 1h (4 candles) | Threshold 3% | Primary entry trigger |
| EMA50 / EMA200 | 1h | TrendFilter | 1h trend |
| EMA12 / EMA26 | 1h | Slope 5 bars | 4h trend |
| Volume SMA | 1h | Period 20 | Volume confirmation |

### SHORT Entry Scoring

| Component | Weight | Condition |
|---|---|---|
| 4h change | +0.30 | change_4h < -3% |
| 1h trend BEARISH | +0.20 | EMA50 < EMA200, price < EMA50, slope < 0 |
| 4h trend BEARISH | +0.20 | EMA12 < EMA26, price < EMA12, slope < 0 |
| Volume ratio | +0.10 | volume_ratio >= 1.0 |
| Funding rate ok | +0.10 | \|funding\| < 0.05% |
| No cooldown | +0.10 | No active cooldown |

Threshold >= 0.50. The 3 main criteria (change + trend_1h + trend_4h) = 0.70.

### LONG Entry Scoring (mirrored)

change_4h > +3%, BULLISH trend on 1h and 4h.

### Hard Blocks

Identical to Mean Reversion:
- \|funding\| >= 0.001 (extreme funding)
- Global cooldown active
- Fresh cooldown < 10 min

### Exit

- SHORT exit: trend_1h becomes BULLISH **or** trend_4h becomes BULLISH
- LONG exit: trend_1h becomes BEARISH **or** trend_4h becomes BEARISH

### Configurable Parameters (`.env`)

| Variable | Default | Description |
|---|---|---|
| `TF_CHANGE_THRESHOLD` | 3.0 | Minimum 4h change % for trigger |

### 4h Data Without Additional WebSocket

The 4h change and 4h trend are calculated from the already available 1h candles:
- `change_4h = (close[-1] - close[-5]) / close[-5] * 100`
- `trend_4h` = EMA12/EMA26 on 1h close (same logic as `_get_market_context`)

---

## Multi-Strategy Conflict Management

When different strategies generate opposite signals on the same coin:

| Scenario | Behavior |
|---|---|
| MR BUY + TF SHORT (higher score) | TF SHORT goes to AI with conflict note |
| MR BUY (higher score) + TF SHORT | MR BUY goes to AI with conflict note |
| MR SHORT + TF SHORT (agree) | Boost +0.10 on the best confidence |
| Only one strategy signals | No change |

The AI receives the note `[Conflict: trend_following SHORT wins over mean_reversion BUY (0.55)]` and evaluates whether the trend will continue or a bounce is more likely.

---

## Stochastic Cross (`stoch`)

### Philosophy

Classic stochastic oscillator: %K/%D crossover in extreme zones.

### Indicators

| Indicator | Parameters | Usage |
|---|---|---|
| Stochastic %K | Period 14 | Fast line |
| Stochastic %D | Smooth 3 | Signal line |

### Entry Conditions

- **LONG:** trend BULLISH + %K and %D < 20 (oversold) + %K crosses above %D
- **SHORT:** trend BEARISH + %K and %D > 80 (overbought) + %K crosses below %D

### Exit

- Opposite cross OR opposite zone reached (overbought for long, oversold for short)

---

## Combined Strategy (`combined`)

### Philosophy

**Consensus voting** system that aggregates signals from all strategies. Conservative entries (requires 2+ concurrent votes), aggressive exits (any exit signal).

### Mechanism

```
For each candle:
  1. Collect signals from all sub-strategies
  2. Count votes BUY, SHORT, SELL, CLOSE_SHORT
  3. Entry: if votes_BUY >= threshold -> BUY
           if votes_SHORT >= threshold -> SHORT
  4. Exit: if ANY strategy votes SELL -> SELL (conservative)
          if ANY strategy votes CLOSE_SHORT -> CLOSE_SHORT
```

### Parameters

| Parameter | Default | Description |
|---|---|---|
| `entry_threshold` | 2 | Minimum number of strategies that must agree for entry |
| Sub-strategies | All 9 | List of strategies included in voting |

### CLI

```bash
# Default (threshold 2)
.venv/bin/python -m scripts.backtest_runner --strategy combined

# Threshold 3 (more conservative)
.venv/bin/python -m scripts.backtest_runner --strategy combined --combined-threshold 3

# Sweep threshold 2/3/4
.venv/bin/python -m scripts.backtest_runner --sweep --strategy combined
```

### Threshold Trade-off

| Threshold | Trade Frequency | Signal Quality |
|---|---|---|
| 2 | High | Medium |
| 3 | Medium | High |
| 4 | Low | Very high |

---

## Cooldown (`strategies/cooldown.py`)

### Per-symbol Cooldown

After a loss on a coin, trading on that coin is blocked for 30 minutes (configurable: `SYMBOL_COOLDOWN_SEC`).

### Global Cooldown

After 3 consecutive losses (configurable: `GLOBAL_COOLDOWN_LOSSES`), trading on all coins is blocked for 15 minutes (configurable: `GLOBAL_COOLDOWN_SEC`).

## Funding Rate Check

Before every entry (LONG or SHORT), the bot verifies that the coin's funding rate is not too high:

```python
abs(funding_rate) < max_funding_rate  # default 0.0005 (0.05%/8h)
```

## Technical Indicators

The `ta` library (0.11.0) is used for all indicators:

```python
# RSI
ta.momentum.RSIIndicator(close, window=14).rsi()

# Bollinger Bands
ta.volatility.BollingerBands(close, window=20, window_dev=2)

# MACD
ta.trend.MACD(close, window_slow=26, window_fast=12, window_sign=9)

# EMA
ta.trend.EMAIndicator(close, window=50).ema_indicator()

# ADX
ta.trend.ADXIndicator(high, low, close, window=14).adx()

# Donchian Channel
ta.volatility.DonchianChannel(high, low, close, window=20)

# Keltner Channel
ta.volatility.KeltnerChannel(high, low, close, window=20, window_atr=20, multiplier=2.0)

# Stochastic Oscillator
ta.momentum.StochasticOscillator(high, low, close, window=14, smooth_window=3)

# ATR
ta.volatility.AverageTrueRange(high, low, close, window=14)
```

Note: `pandas-ta` is not usable because it requires `numba` which doesn't support Python 3.14.
