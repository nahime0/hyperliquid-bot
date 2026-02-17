# System Architecture

## Overview

The bot is an asynchronous Python application (`asyncio`) that runs as a continuous loop on Hyperliquid Perpetual Futures. Each cycle (60s by default) goes through the phases: data collection, analysis, decision generation, validation, execution.

## Main Components

### 1. HyperliquidClient (`core/client.py`)

Async wrapper around the synchronous Hyperliquid SDK. All SDK calls are wrapped with `asyncio.to_thread()` to avoid blocking the event loop.

**SDK classes used:**
- `Info(api_url, skip_ws=True)` — read-only data (prices, candles, account state)
- `Exchange(private_key, api_url, account_address)` — trading operations

**Retry:** Exponential backoff (1s, 2s, 4s) with 3 attempts per SDK call.

**Main methods:**

| Method | Description |
|---|---|
| `connect()` | Creates `Info` + `Exchange`, loads metadata (szDecimals) |
| `get_all_mids()` | Mid prices for all assets |
| `get_price(coin)` | Single mid price |
| `get_candles(coin, interval, start_ms, end_ms)` | OHLCV candles |
| `get_user_state()` | Account: margin, positions, balance |
| `get_account_balance()` | accountValue in USDC |
| `get_open_positions()` | Positions with szi (signed), entry, PnL, liquidation |
| `place_market_order(coin, is_buy, size)` | Market order via `exchange.market_open()` |
| `close_position(coin)` | Close via `exchange.market_close()` |
| `update_leverage(coin, leverage, is_cross)` | Set leverage per coin |
| `round_size(coin, size)` | Round to szDecimals (truncate/floor) |

**szDecimals:** Each coin has a specific size precision (e.g. BTC: 5 decimals, DOGE: 0). The client loads this data from `info.meta()` at startup and uses it to round all quantities.

### 2. MarketData (`core/market_data.py`)

In-memory cache of market data with WebSocket updates.

**Thread safety:** The WebSocket SDK runs in a separate thread. `threading.Lock` protects `_mid_prices` and `_candles`. Minimal contention: writes from WS thread, reads every 60s from the main asyncio loop.

**Data flow:**
1. **Startup:** Loads historical candles via REST (max 10 parallel requests)
2. **Runtime:** Updates in real-time via WebSocket
   - `allMids` stream: all mid prices in a single stream
   - `candle` stream: per coin/interval pair

**Calculated indicators:**
- RSI(14) via `ta.momentum.RSIIndicator`
- Bollinger Bands(20, 2) via `ta.volatility.BollingerBands`
- MACD(12, 26, 9) via `ta.trend.MACD`
- EMA(9, 21, 50) via `ta.trend.EMAIndicator`

**Intervals:** `5m` (300 candles, ~25h — for RSI Divergence), `15m` (200 candles) and `1h` (300 candles for EMA200). Configurable via `MarketConfig.intervals`.

### 3. Bot (`main.py`)

Main orchestrator. Owns all components and manages the loop.

**Lifecycle:**
1. `start()` — Connects client, DB, discovers coins, starts WS, strategies
2. `run()` — Main loop with `_tick()` every `decision_interval` seconds
3. `stop()` — Graceful shutdown: saves state, closes connections

**Strategy modes** (`--strategy` flag):
- `multi` (default): `MultiStrategy` aggregates decisions from Mean Reversion (15m) + RSI Divergence (5m)
- `mean_reversion`: Mean Reversion only on 15m (legacy)
- `rsi_div`: RSI Divergence only on 5m

**`_tick()` flow:**
1. Risk refresh (balance, positions, kill switch, daily pause)
2. Check SL/TP/trailing/time stop on open positions
3. Refresh funding rates (every 5 cycles)
4. Update trend filter + active strategy (multi/single)
5. Generate candidate decisions (merged if multi)
6. Optional AI review
7. Sort: CLOSE first, then entries
8. Anti-churning: don't buy what you're selling in the same cycle
9. Validate + execute each decision
10. Periodic balance snapshot (every 5 min)
11. Check consecutive losses
12. Write status to `data/bot_status.json` (read by Next.js dashboard)

### 4. Data Flow

```
Hyperliquid API
    |
    +--REST--> HyperliquidClient --> MarketData (candle loading)
    |                                     |
    +--WS---> MarketData._ws_info --------+
              (allMids, candles)           |
                                          v
                                    DataFrame cache
                                (per coin/interval: 5m, 15m, 1h)
                                          |
                    +---------------------+---------------------+
                    |                     |                     |
              TrendFilter          MeanReversion         RSIDivergence
              (1h EMA50/200)       (15m RSI/BB/Vol)      (5m swing/RSI)
                    |                     |                     |
                    +----------+----------+----------+---------+
                               |                     |
                         MultiStrategy <-------------+
                         (decision merger)
                               |
                          Decisions
                          (BUY/SHORT/CLOSE)
                               |
                         +-----+
                         |     |
                    AI Review  Risk Manager
                    (optional) (mandatory)
                         |     |
                         +--+--+
                            |
                        Execute
                   (Hyperliquid SDK)
```

## Concurrency

- **Main loop:** Single asyncio thread
- **WebSocket:** Separate thread (managed by SDK)
- **Candle loading:** `asyncio.gather()` with semaphore (10 parallel requests)
- **SDK calls:** `asyncio.to_thread()` to wrap synchronous calls

## Configuration

All configurations are frozen dataclasses (`@dataclass(frozen=True)`):
- `HyperliquidConfig` — API, testnet, leverage, margin mode
- `AIConfig` — models, timeout, backend, thresholds
- `RiskConfig` — trading limits, trailing, cooldown
- `MarketConfig` — minimum volume, max coins, candle intervals
- `StrategyConfig` — active strategies, RSI Div parameters, per-strategy intervals
- `TelegramConfig` — notifications

Loaded from environment variables in `load_settings()`.
