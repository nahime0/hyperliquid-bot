# Strategie di Trading

## Overview

Il bot utilizza una strategia principale (**Mean Reversion**) con supporto a entrambe le direzioni (LONG e SHORT). Un **Trend Filter** determina la direzione consentita per ogni coin. Un sistema di **Cooldown** previene il trading eccessivo dopo le perdite.

La Grid Strategy e' disabilitata (non adatta a perpetual futures) ma il file `strategies/grid.py` e' mantenuto per reference storica.

## Mean Reversion (`strategies/mean_reversion.py`)

### Filosofia

La mean reversion sfrutta il ritorno dei prezzi alla media dopo estremi. Il bot cerca condizioni di oversold (per LONG) o overbought (per SHORT) confermate da trend, volume, e indicatori su timeframe multipli.

### Indicatori utilizzati

| Indicatore | Timeframe | Parametri | Utilizzo |
|---|---|---|---|
| RSI | 15m | Periodo 14 | Entry/exit trigger |
| RSI | 1h | Periodo 14 | Filtro macro (no divergenze) |
| Bollinger Bands | 15m | Periodo 20, 2 std dev | Conferma estremi di prezzo |
| Volume SMA | 15m | Periodo 20 | Conferma volume sopra media |
| EMA50 / EMA200 | 1h | - | Trend filter (via TrendFilter) |

### Segnali

Ogni ciclo, la strategia classifica ogni coin come:

| Segnale | Condizioni | Strength (0-1) |
|---|---|---|
| **OVERSOLD** | RSI < 25 AND price <= lower BB * 1.005 | Media tra RSI strength e BB strength |
| **OVERBOUGHT** | RSI > 70 AND price >= upper BB * 0.995 | Media tra RSI strength e BB strength |
| **NEUTRAL** | Nessuna delle precedenti | 0 |

### Condizioni LONG Entry

**Tutte** devono essere vere simultaneamente:

1. **Trend BULLISH (1h):** EMA50 > EMA200, prezzo > EMA50, slope EMA50 positiva
2. **RSI oversold (15m):** RSI(14) < 25
3. **Bollinger (15m):** Prezzo <= lower BB * 1.005
4. **Volume:** Volume ratio >= 1.2 (volume corrente / SMA 20 periodi)
5. **RSI macro (1h):** RSI < 60 (conferma che il mercato non e' in divergenza)
6. **Funding rate:** |rate| < max_funding_rate (0.05%/8h di default)
7. **Cooldown:** Nessun cooldown attivo per il coin
8. **No posizione aperta:** Non esiste gia' una posizione sullo stesso coin

**Confidenza:** `min(0.8, 0.5 + signal_strength * 0.3)`

### Condizioni SHORT Entry

**Tutte** devono essere vere simultaneamente:

1. **Trend BEARISH (1h):** EMA50 < EMA200, prezzo < EMA50, slope EMA50 negativa
2. **RSI overbought (15m):** RSI(14) > 75 (soglia piu' alta rispetto al LONG per sicurezza)
3. **Bollinger (15m):** Prezzo >= upper BB * 0.995
4. **Volume:** Volume ratio >= 1.2
5. **RSI macro (1h):** RSI > 40
6. **Funding rate:** |rate| < max_funding_rate
7. **Cooldown:** Nessun cooldown attivo
8. **No posizione aperta:** Non esiste gia' una posizione sullo stesso coin

### Condizioni Exit

#### LONG Exit
- RSI > 70 (overbought → prendere profitto)
- Prezzo >= upper BB * 0.995 (raggiunta la banda superiore)

#### SHORT Exit
- RSI < 30 (oversold → coprire)
- Prezzo <= lower BB * 1.005 (raggiunta la banda inferiore)

Le exit sono aggiuntive rispetto a SL/TP/trailing/time stop (gestiti dal PositionTracker).

### Ordine di elaborazione

1. **Exit LONG** — Controlla posizioni LONG aperte per exit signals
2. **Exit SHORT** — Controlla posizioni SHORT aperte per exit signals
3. **Entry LONG** — Cerca nuove opportunita' LONG su tutti i coin
4. **Entry SHORT** — Cerca nuove opportunita' SHORT su tutti i coin

L'ordine di esecuzione in `main.py` mette sempre CLOSE prima di BUY/SHORT (anti-churning).

## Trend Filter (`strategies/trend_filter.py`)

### Classificazione

Il trend filter classifica ogni coin su candele 1h:

| Trend | Condizioni |
|---|---|
| **BULLISH** | EMA50 > EMA200 AND price > EMA50 AND slope > 0 |
| **BEARISH** | EMA50 < EMA200 AND price < EMA50 AND slope < 0 |
| **NEUTRAL** | Tutto il resto |

**Slope:** Variazione dell'EMA50 negli ultimi 5 barre, normalizzata per il prezzo (in percentuale).

### Utilizzo

- `is_bullish(coin)` — Gate per LONG entry
- `is_bearish(coin)` — Gate per SHORT entry
- Il trend NEUTRAL blocca entrambe le direzioni

## Cooldown (`strategies/cooldown.py`)

### Per-symbol cooldown

Dopo una perdita su un coin, il trading su quel coin e' bloccato per 30 minuti (configurabile: `SYMBOL_COOLDOWN_SEC`).

### Global cooldown

Dopo 3 perdite consecutive (configurabile: `GLOBAL_COOLDOWN_LOSSES`), il trading su tutti i coin e' bloccato per 15 minuti (configurabile: `GLOBAL_COOLDOWN_SEC`).

### Metodo `can_buy(symbol)`

Ritorna `(True, "")` se il trading e' permesso, `(False, "reason")` se bloccato.

## Funding Rate Check

Prima di ogni entry (LONG o SHORT), il bot verifica che il funding rate del coin non sia troppo alto:

```python
abs(funding_rate) < max_funding_rate  # default 0.0005 (0.05%/8h)
```

Se il funding rate non e' disponibile, l'entry e' permessa (fail-open).

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
```

Nota: `pandas-ta` non e' utilizzabile perche' richiede `numba` che non supporta Python 3.14.
