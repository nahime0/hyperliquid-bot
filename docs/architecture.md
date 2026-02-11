# Architettura del Sistema

## Panoramica

Il bot e' un'applicazione Python asincrona (`asyncio`) che opera come loop continuo su Hyperliquid Perpetual Futures. Ogni ciclo (60s di default) attraversa le fasi: raccolta dati, analisi, generazione decisioni, validazione, esecuzione.

## Componenti principali

### 1. HyperliquidClient (`core/client.py`)

Wrapper asincrono attorno al SDK sincrono di Hyperliquid. Tutte le chiamate SDK vengono wrappate con `asyncio.to_thread()` per non bloccare l'event loop.

**Classi SDK utilizzate:**
- `Info(api_url, skip_ws=True)` — dati read-only (prezzi, candles, stato account)
- `Exchange(private_key, api_url, account_address)` — operazioni di trading

**Retry:** Backoff esponenziale (1s, 2s, 4s) con 3 tentativi per ogni chiamata SDK.

**Metodi principali:**

| Metodo | Descrizione |
|---|---|
| `connect()` | Crea `Info` + `Exchange`, carica metadata (szDecimals) |
| `get_all_mids()` | Mid prices per tutti gli asset |
| `get_price(coin)` | Mid price singolo |
| `get_candles(coin, interval, start_ms, end_ms)` | Candele OHLCV |
| `get_user_state()` | Account: margine, posizioni, balance |
| `get_account_balance()` | accountValue in USDC |
| `get_open_positions()` | Posizioni con szi (signed), entry, PnL, liquidation |
| `place_market_order(coin, is_buy, size)` | Market order via `exchange.market_open()` |
| `close_position(coin)` | Chiusura via `exchange.market_close()` |
| `update_leverage(coin, leverage, is_cross)` | Imposta leva per coin |
| `round_size(coin, size)` | Arrotonda a szDecimals (truncate/floor) |

**szDecimals:** Ogni coin ha una precisione di size specifica (es. BTC: 5 decimali, DOGE: 0). Il client carica questi dati da `info.meta()` all'avvio e li usa per arrotondare tutte le quantita'.

### 2. MarketData (`core/market_data.py`)

Cache in memoria dei dati di mercato con aggiornamento via WebSocket.

**Thread safety:** Il WebSocket SDK gira in un thread separato. `threading.Lock` protegge `_mid_prices` e `_candles`. Contention minima: write dal WS thread, read ogni 60s dal main loop asyncio.

**Flusso dati:**
1. **Avvio:** Carica candele storiche via REST (max 10 richieste parallele)
2. **Runtime:** Aggiorna in real-time via WebSocket
   - `allMids` stream: tutti i mid prices in un singolo stream
   - `candle` stream: per ogni coppia coin/interval

**Indicatori calcolati:**
- RSI(14) via `ta.momentum.RSIIndicator`
- Bollinger Bands(20, 2) via `ta.volatility.BollingerBands`
- MACD(12, 26, 9) via `ta.trend.MACD`
- EMA(9, 21, 50) via `ta.trend.EMAIndicator`

**Intervalli:** `15m` (200 candele iniziali) e `1h` (300 candele per EMA200).

### 3. Bot (`main.py`)

Orchestratore principale. Possiede tutti i componenti e gestisce il loop.

**Ciclo di vita:**
1. `start()` — Connette client, DB, scopre coin, avvia WS, strategie, dashboard
2. `run()` — Loop principale con `_tick()` ogni `decision_interval` secondi
3. `stop()` — Shutdown graceful: salva stato, chiude connessioni

**Flusso `_tick()`:**
1. Risk refresh (balance, posizioni, kill switch, daily pause)
2. Check SL/TP/trailing/time stop su posizioni aperte
3. Refresh funding rates (ogni 5 cicli)
4. Update trend filter + mean reversion signals
5. Generate candidate decisions
6. AI review opzionale
7. Sort: CLOSE first, poi entries
8. Anti-churning: non comprare cio' che stai vendendo nello stesso ciclo
9. Validate + execute per ogni decisione
10. Balance snapshot periodico (ogni 5 min)
11. Check consecutive losses
12. Write status per dashboard

### 4. Flusso dati

```
Hyperliquid API
    │
    ├──REST──→ HyperliquidClient ──→ MarketData (candle loading)
    │                                     │
    └──WS───→ MarketData._ws_info ────────┤
              (allMids, candles)           │
                                          ▼
                                    DataFrame cache
                                    (per coin/interval)
                                          │
                    ┌─────────────────────┤
                    │                     │
              TrendFilter          MeanReversion
              (1h EMA50/200)       (15m RSI/BB/Vol)
                    │                     │
                    └──────────┬──────────┘
                               │
                          Decisions
                          (BUY/SHORT/CLOSE)
                               │
                         ┌─────┤
                         │     │
                    AI Review  Risk Manager
                    (optional) (mandatory)
                         │     │
                         └──┬──┘
                            │
                        Execute
                   (Hyperliquid SDK)
```

## Concorrenza

- **Main loop:** Singolo thread asyncio
- **WebSocket:** Thread separato (gestito dal SDK)
- **Candle loading:** `asyncio.gather()` con semaphore (10 richieste parallele)
- **SDK calls:** `asyncio.to_thread()` per wrappare le chiamate sincrone

## Configurazione

Tutte le configurazioni sono dataclass frozen (`@dataclass(frozen=True)`):
- `HyperliquidConfig` — API, testnet, leverage, margin mode
- `AIConfig` — modelli, timeout, backend, soglie
- `RiskConfig` — limiti trading, trailing, cooldown
- `MarketConfig` — volume minimo, max coins
- `TelegramConfig` — notifiche

Caricate da variabili d'ambiente in `load_settings()`.
