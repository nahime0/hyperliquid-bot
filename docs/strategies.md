# Strategie di Trading

## Overview

Il bot supporta due modalita' operative:

### Live Trading (bot principale — `main.py`)

Il bot live utilizza un **MultiStrategy aggregator** che combina decisioni indipendenti da piu' strategie:

| Strategia Live | Timeframe | Classe | CLI flag |
|---|---|---|---|
| **Mean Reversion** | 15m | `MeanReversionStrategy` | `--strategy mean_reversion` |
| **RSI Divergence** | 5m | `RSIDivergenceStrategy` | `--strategy rsi_div` |
| **Multi (default)** | 5m + 15m | `MultiStrategy` | `--strategy multi` |

Il `MultiStrategy` merger:
- Raccoglie Decision da ciascuna sub-strategia indipendentemente
- Se 2+ strategie concordano su BUY/SHORT per lo stesso coin → boost confidence (+0.1)
- Se strategie in conflitto (BUY vs SHORT sullo stesso coin) → skip coin
- Se solo 1 strategia segnala → usa confidence originale
- Mai piu' di `max_open_positions` entry per ciclo

### Backtesting (9+ strategie — `scripts/backtest_runner.py`)

Il backtester supporta tutte le strategie originali con parameter sweep:

Strategie disponibili:

| Strategia | Tipo | Classe backtest | CLI flag |
|---|---|---|---|
| **Mean Reversion** | Mean reversion | `HyperliquidMeanRevRule` | `hyperliquid_mr` |
| **Scalp** | Mean reversion rilassata | `ScalpMeanRevRule` | `scalp` |
| **EMA Crossover + ADX** | Trend following | `EMACrossoverADXRule` | `ema_adx` |
| **BB Squeeze** | Volatility breakout | `BBSqueezeRule` | `bb_squeeze` |
| **MACD Momentum** | Momentum | `MACDMomentumRule` | `macd` |
| **Donchian Breakout** | Channel breakout | `DonchianBreakoutRule` | `donchian` |
| **Keltner Breakout** | ATR breakout | `KeltnerBreakoutRule` | `keltner` |
| **VWAP Reversion** | Intraday mean rev | `VWAPReversionRule` | `vwap_rev` |
| **RSI Divergence** | Divergence reversal | `RSIDivergenceRule` | `rsi_div` |
| **Stochastic Cross** | Oscillator cross | `StochasticCrossRule` | `stoch` |
| **Combined** | Consenso multi-strategia | `CombinedRule` | `combined` |

## Trend Filter (gate universale)

**Tutte** le strategie usano lo stesso trend filter 1h come quality gate:

| Trend | Condizioni |
|---|---|
| **BULLISH** | EMA50 > EMA200 AND price > EMA50 AND slope > 0 |
| **BEARISH** | EMA50 < EMA200 AND price < EMA50 AND slope < 0 |
| **NEUTRAL** | Tutto il resto |

**Slope:** Variazione dell'EMA50 negli ultimi 5 barre, normalizzata per il prezzo (in percentuale).

Utilizzo per strategia:
- Strategie **direzionali** (EMA cross, MACD, Donchian, Keltner, Stochastic): LONG solo se BULLISH, SHORT solo se BEARISH
- Strategie **mean reversion** (BB Squeeze, VWAP, RSI Div): LONG se BULLISH/NEUTRAL, SHORT se BEARISH/NEUTRAL

---

## Mean Reversion (`hyperliquid_mr`)

### Indicatori

| Indicatore | Timeframe | Parametri | Utilizzo |
|---|---|---|---|
| RSI | 15m | Periodo 14 | Entry/exit trigger |
| RSI | 1h | Periodo 14 | Filtro macro |
| Bollinger Bands | 15m | Periodo 20, 2 std dev | Conferma estremi |
| Volume SMA | 15m | Periodo 20 | Conferma volume |
| EMA50 / EMA200 | 1h | - | Trend filter |

### Condizioni LONG Entry

1. Trend BULLISH (1h)
2. RSI(14) < 25
3. Prezzo <= lower BB * 1.005
4. Volume ratio >= 1.2
5. RSI 1h < 60

### Condizioni SHORT Entry

1. Trend BEARISH (1h)
2. RSI(14) > 75
3. Prezzo >= upper BB * 0.995
4. Volume ratio >= 1.2
5. RSI 1h > 40

### Exit

- **LONG exit:** RSI > 70 OR prezzo >= BB upper
- **SHORT exit:** RSI < 30 OR prezzo <= BB lower

---

## EMA Crossover + ADX (`ema_adx`)

### Filosofia

Trend following puro: segue i crossover delle EMA confermate dalla forza del trend (ADX).

### Indicatori

| Indicatore | Parametri | Utilizzo |
|---|---|---|
| EMA12 / EMA26 | Periodi 12, 26 | Crossover signal |
| ADX | Periodo 14 | Conferma forza trend |

### Condizioni Entry

- **LONG:** trend BULLISH + EMA12 crosses above EMA26 + ADX > 20
- **SHORT:** trend BEARISH + EMA12 crosses below EMA26 + ADX > 20

### Exit

- EMA cross opposto OR ADX < 15

---

## BB Squeeze (`bb_squeeze`)

### Filosofia

Identifica periodi di bassa volatilita' (squeeze) e opera il breakout quando la volatilita' esplode.

### Indicatori

| Indicatore | Parametri | Utilizzo |
|---|---|---|
| Bollinger Bands | Periodo 20, 2 std | Canale volatilita' |
| BB Bandwidth | - | Detecta squeeze (< 2%) |
| Volume SMA | Periodo 20 | Conferma volume breakout |

### Condizioni Entry

- **Squeeze detectata:** BB bandwidth < 2% per 3+ candele consecutive
- **LONG:** trend BULLISH/NEUTRAL + squeeze + prezzo rompe BB upper + volume > 1.5x
- **SHORT:** trend BEARISH/NEUTRAL + squeeze + prezzo rompe BB lower + volume > 1.5x

### Exit

- Prezzo torna a BB mid OR bandwidth > 3%

---

## MACD Momentum (`macd`)

### Filosofia

Cattura il momentum direzionale tramite crossover MACD con conferma dell'istogramma.

### Indicatori

| Indicatore | Parametri | Utilizzo |
|---|---|---|
| MACD line | Fast 12, Slow 26 | Direction |
| Signal line | Periodo 9 | Crossover trigger |
| Histogram | MACD - Signal | Conferma momentum |

### Condizioni Entry

- **LONG:** trend BULLISH + MACD > 0 + MACD crosses above signal + histogram crescente
- **SHORT:** trend BEARISH + MACD < 0 + MACD crosses below signal + histogram decrescente

### Exit

- MACD cross opposto OR zero-line cross

---

## Donchian Breakout (`donchian`)

### Filosofia

Classic channel breakout (Turtle trading style): opera la rottura dei massimi/minimi di periodo dopo consolidamento.

### Indicatori

| Indicatore | Parametri | Utilizzo |
|---|---|---|
| Donchian Channel | Periodo 20 | High/low/mid |
| Consolidation range | 5 bar | Conferma range stretto |
| Volume SMA | Periodo 20 | Conferma breakout |

### Condizioni Entry

- **LONG:** trend BULLISH + prezzo rompe 20-bar high + volume > 1.3x + consolidamento 5+ bar (range < 1.5%)
- **SHORT:** trend BEARISH + prezzo rompe 20-bar low + volume > 1.3x + consolidamento 5+ bar

### Exit

- Prezzo torna sotto Donchian mid

---

## Keltner Breakout (`keltner`)

### Filosofia

Breakout basato su ATR (Average True Range) — adattivo alla volatilita' corrente.

### Indicatori

| Indicatore | Parametri | Utilizzo |
|---|---|---|
| Keltner Channel | EMA20 +/- 2*ATR | Canale adattivo |
| Volume SMA | Periodo 20 | Conferma breakout |

### Condizioni Entry

- **LONG:** trend BULLISH + prezzo rompe Keltner upper + volume > 1.2x
- **SHORT:** trend BEARISH + prezzo rompe Keltner lower + volume > 1.2x

### Exit

- Prezzo torna sotto Keltner mid

---

## VWAP Reversion (`vwap_rev`)

### Filosofia

Mean reversion intraday attorno al VWAP (Volume Weighted Average Price). Cerca deviazioni estreme rispetto al prezzo medio pesato per volume.

### Indicatori

| Indicatore | Parametri | Utilizzo |
|---|---|---|
| VWAP | Rolling 96 bar (sessione) | Prezzo medio fair value |
| ATR | Periodo 14 | Misura deviazione |
| RSI | Periodo 14 | Conferma oversold/overbought |

### VWAP Calculation

```python
hlc3 = (high + low + close) / 3
VWAP = cumsum(volume * hlc3) / cumsum(volume)  # rolling window
```

### Condizioni Entry

- **LONG:** trend BULLISH/NEUTRAL + prezzo < VWAP - 2*ATR + RSI < 35
- **SHORT:** trend BEARISH/NEUTRAL + prezzo > VWAP + 2*ATR + RSI > 65

### Exit

- Prezzo torna al VWAP OR RSI si normalizza (> 50 per long, < 50 per short)

---

## RSI Divergence (`rsi_div`)

### Filosofia

Identifica divergenze tra prezzo e RSI (swing detection). Una divergenza bullish indica che la pressione ribassista si sta esaurendo; una bearish il contrario. **Top performer nel backtest**: +$47, 61% WR, 34/35 coin profittevoli.

### Indicatori

| Indicatore | Parametri | Utilizzo |
|---|---|---|
| RSI | Periodo 14 (configurabile) | Divergence detection |
| Swing detection | Finestra 5 bar (configurabile) | Identifica pivot points |

### Condizioni Entry

- **Bullish divergence (LONG):** trend BULLISH/NEUTRAL + prezzo fa lower low + RSI fa higher low
- **Bearish divergence (SHORT):** trend BEARISH/NEUTRAL + prezzo fa higher high + RSI fa lower high

### Exit

- RSI > 60 (long exit, configurabile: `RSI_DIV_LONG_EXIT`) OR RSI < 40 (short exit, configurabile: `RSI_DIV_SHORT_EXIT`)

### Parametri configurabili (`.env`)

| Variabile | Default | Descrizione |
|---|---|---|
| `RSI_DIV_PERIOD` | 14 | Periodo RSI |
| `RSI_DIV_SWING_WINDOW` | 5 | Finestra swing detection (2*w+1 barre) |
| `RSI_DIV_LONG_EXIT` | 60.0 | RSI threshold per exit LONG |
| `RSI_DIV_SHORT_EXIT` | 40.0 | RSI threshold per exit SHORT |
| `PRIMARY_INTERVAL` | 5m | Timeframe primario per RSI Div |

### Live vs Backtest

- **Live** (`strategies/rsi_divergence.py`): `RSIDivergenceStrategy(Strategy)` — opera su candele 5m in real-time via WebSocket
- **Backtest** (`data/backtest.py`): `RSIDivergenceRule(TradingRule)` — stessa logica su dati storici
- **Parameter sweep**: `.venv/bin/python -m scripts.backtest_runner --sweep --strategy rsi_div`

---

## Stochastic Cross (`stoch`)

### Filosofia

Classico oscillatore stocastico: crossover %K/%D nelle zone estreme.

### Indicatori

| Indicatore | Parametri | Utilizzo |
|---|---|---|
| Stochastic %K | Periodo 14 | Fast line |
| Stochastic %D | Smooth 3 | Signal line |

### Condizioni Entry

- **LONG:** trend BULLISH + %K e %D < 20 (oversold) + %K crosses above %D
- **SHORT:** trend BEARISH + %K e %D > 80 (overbought) + %K crosses below %D

### Exit

- Cross opposto OR zona opposta raggiunta (overbought per long, oversold per short)

---

## Combined Strategy (`combined`)

### Filosofia

Sistema di **voting a consenso** che aggrega segnali da tutte le strategie. Entry conservativi (richiede 2+ voti concordi), exit aggressivi (qualsiasi segnale di exit).

### Meccanismo

```
Per ogni candela:
  1. Raccoglie segnali da tutte le sub-strategie
  2. Conta voti BUY, SHORT, SELL, CLOSE_SHORT
  3. Entry: se voti_BUY >= threshold -> BUY
           se voti_SHORT >= threshold -> SHORT
  4. Exit: se QUALSIASI strategia vota SELL -> SELL (conservativo)
          se QUALSIASI strategia vota CLOSE_SHORT -> CLOSE_SHORT
```

### Parametri

| Parametro | Default | Descrizione |
|---|---|---|
| `entry_threshold` | 2 | Numero minimo di strategie che devono concordare per entry |
| Sub-strategies | Tutte 9 | Lista di strategie incluse nel voto |

### CLI

```bash
# Default (threshold 2)
.venv/bin/python -m scripts.backtest_runner --strategy combined

# Threshold 3 (piu' conservativo)
.venv/bin/python -m scripts.backtest_runner --strategy combined --combined-threshold 3

# Sweep threshold 2/3/4
.venv/bin/python -m scripts.backtest_runner --sweep --strategy combined
```

### Trade-off threshold

| Threshold | Frequenza trade | Qualita' segnale |
|---|---|---|
| 2 | Alta | Media |
| 3 | Media | Alta |
| 4 | Bassa | Molto alta |

---

## Cooldown (`strategies/cooldown.py`)

### Per-symbol cooldown

Dopo una perdita su un coin, il trading su quel coin e' bloccato per 30 minuti (configurabile: `SYMBOL_COOLDOWN_SEC`).

### Global cooldown

Dopo 3 perdite consecutive (configurabile: `GLOBAL_COOLDOWN_LOSSES`), il trading su tutti i coin e' bloccato per 15 minuti (configurabile: `GLOBAL_COOLDOWN_SEC`).

## Funding Rate Check

Prima di ogni entry (LONG o SHORT), il bot verifica che il funding rate del coin non sia troppo alto:

```python
abs(funding_rate) < max_funding_rate  # default 0.0005 (0.05%/8h)
```

## Indicatori tecnici

La libreria `ta` (0.11.0) viene usata per tutti gli indicatori:

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

Nota: `pandas-ta` non e' utilizzabile perche' richiede `numba` che non supporta Python 3.14.
