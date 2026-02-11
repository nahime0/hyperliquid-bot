# Hyperliquid Perpetual Trading Bot

## Documentazione

**IMPORTANTE:** `README.md` e la cartella `docs/` devono essere sempre tenuti aggiornati quando si modificano funzionalita', architettura, configurazione, o schema del database. Ogni modifica al codice che cambia comportamenti documentati deve includere l'aggiornamento della documentazione corrispondente.

- `README.md` — Overview, quick start, configurazione
- `docs/architecture.md` — Architettura del sistema, componenti, flusso dati
- `docs/strategies.md` — Strategie di trading, indicatori, condizioni entry/exit
- `docs/risk-management.md` — Risk management, trailing stop, Kelly sizing
- `docs/ai-engine.md` — AI Decision Engine, tier, prompt, schema
- `docs/configuration.md` — Tutte le variabili di configurazione
- `docs/database.md` — Schema database, migrazioni
- `docs/deployment.md` — Setup, installazione, deployment

## Obiettivo

Bot automatico in Python per trading su **Hyperliquid Perpetual Futures** con AI review opzionale. Opera 24/7 con strategia Mean Reversion (LONG + SHORT), leva conservativa (2-3x), fee ultra-basse (0.06% RT).

## Contesto

- L'utente e' un full-stack developer (Laravel/PHP, React Native) italiano
- Migrato da Binance Spot (fee troppo alte) a Hyperliquid Perps
- Binance Futures bloccato per utenti EU (MiCA)
- Hyperliquid: 0.06% RT fees, shorts, leverage, no KYC
- Settlement nativo in USDC
- Home lab con Proxmox per deployment

## Architettura

### Stack
- Python 3.11+ (testato 3.14)
- `hyperliquid-python-sdk` (sync SDK wrappato con `asyncio.to_thread()`)
- `eth-account` per wallet signing (EIP-712)
- `asyncio` + WebSocket per dati real-time
- `pandas` + `ta` (0.11.0) per indicatori tecnici
- `aiosqlite` per persistenza
- `anthropic` SDK per AI engine
- `aiohttp` per dashboard e Telegram
- `python-dotenv` per config

### Struttura progetto
```
binance/
├── main.py                    # Entry point: Bot class, main loop
├── CLAUDE.md                  # Questo file
├── README.md                  # Documentazione principale
├── docs/                      # Documentazione dettagliata
├── config/
│   ├── settings.py            # HyperliquidConfig, AIConfig, RiskConfig, etc.
│   └── pairs.py               # CORE_COINS, discover_perp_coins()
├── core/
│   ├── client.py              # HyperliquidClient (async wrapper)
│   ├── market_data.py         # WebSocket feeds, candle cache, indicatori
│   └── ai_engine/             # 3-tier AI: PreScreen → Haiku → Sonnet
├── strategies/
│   ├── mean_reversion.py      # Mean Reversion LONG + SHORT
│   ├── trend_filter.py        # EMA50/EMA200 trend classification
│   ├── cooldown.py            # Per-symbol + global cooldown
│   └── grid.py                # Disabilitata (mantenuta per history)
├── risk/
│   ├── position_sizer.py      # Kelly Criterion f/4
│   ├── position_tracker.py    # LONG/SHORT, trailing SL, time stop
│   └── risk_manager.py        # Kill switch, daily pause, validation
├── data/
│   ├── db.py                  # SQLite async, schema, migrazioni
│   └── backtest.py            # BacktestEngine
├── schemas/                   # JSON Schema per decisioni AI
├── prompts/                   # Prompt templates per AI
├── dashboard/                 # Web dashboard (aiohttp + WebSocket)
├── utils/                     # Logger, Telegram
└── scripts/                   # Test, backtest, download history
```

### Configurazione (.env)
```env
HL_PRIVATE_KEY=0x...
HL_ACCOUNT_ADDRESS=0x...
HL_TESTNET=true
HL_DEFAULT_LEVERAGE=2
HL_MARGIN_MODE=cross
HL_MAX_FUNDING_RATE=0.0005
ANTHROPIC_API_KEY=sk-ant-...
```

## Hyperliquid SDK

- **Info** class: dati read-only (`all_mids`, `user_state`, `candles_snapshot`, `meta`)
- **Exchange** class: trading (`market_open`, `market_close`, `order`, `cancel`, `update_leverage`)
- SDK sincrono → wrappato con `asyncio.to_thread()`
- Auth: private key EIP-712 (SDK gestisce signing)
- `info.meta()` → universe con `szDecimals` per rounding
- `info.all_mids()` → mid prices per tutti gli asset
- WebSocket: SDK interno (threading) per `allMids` e `candle` streams

## Flusso decisionale

```
Loop (60s) → Risk Refresh → Check SL/TP → Update Strategies
           → Generate Decisions (Mean Reversion rules)
           → AI Review (optional) → Risk Validate → Execute
```

### Azioni
- `BUY` → LONG entry (`exchange.market_open(coin, is_buy=True, sz)`)
- `SHORT` → SHORT entry (`exchange.market_open(coin, is_buy=False, sz)`)
- `CLOSE` → Close position (`exchange.market_close(coin)`)
- `HOLD` → Do nothing

### Schema decisione AI
```json
{
  "action": "BUY | SHORT | SELL | HOLD | CLOSE",
  "symbol": "ETH",
  "size_pct": 5.0,
  "confidence": 0.85,
  "reasoning": "...",
  "stop_loss": 1980.00,
  "take_profit": 2030.75,
  "strategy_type": "mean_reversion"
}
```

## Strategie

### Mean Reversion (LONG + SHORT)
- LONG: Trend BULLISH + RSI<25 + price<=BB_lower + vol_ratio>=1.2 + RSI_1h<60
- SHORT: Trend BEARISH + RSI>75 + price>=BB_upper + vol_ratio>=1.2 + RSI_1h>40
- Exit LONG: RSI>70 or price>=BB_upper
- Exit SHORT: RSI<30 or price<=BB_lower
- Funding rate check prima di ogni entry

### Trend Filter (EMA50/EMA200 su 1h)
- BULLISH: EMA50>EMA200, price>EMA50, slope>0
- BEARISH: EMA50<EMA200, price<EMA50, slope<0

## Risk Management

### Regole
- Max per trade: 10% bankroll
- Stop loss: -1%, Take profit: +1.5%
- Daily drawdown max: -5% → pausa
- Total drawdown max: -15% → kill switch
- Max posizioni: 5
- Min balance: 50 USDC
- 5 perdite consecutive → kill switch

### Trailing Stop (direction-aware)
- Break-even: gain>=1.0% → SL a entry
- Trailing: gain>=1.5% → SL segue a 1.0% distanza
- Tight: gain>=2.5% → SL a 0.75% distanza
- LONG: SL solo sale. SHORT: SL solo scende.

### Time Stop
- 4h+ con PnL<0.5% → auto-close

### Kelly Criterion (f/4)
- Cold start (<10 trade): 5% fisso
- Post cold start: quarter-Kelly * confidence
- Min order: 10 USDC

### Cooldown
- Per-symbol: 30 min dopo perdita
- Globale: 15 min dopo 3 perdite consecutive

## Fee Hyperliquid
- Maker: 0.015%
- Taker: 0.045%
- Round-trip: ~0.06%
- Il bot usa MARKET orders (taker)

## API Hyperliquid

### Endpoint
- REST/WS Mainnet: `https://api.hyperliquid.xyz`
- REST/WS Testnet: `https://api.hyperliquid-testnet.xyz`

### Rate Limits
- 1200 weight/min REST
- WebSocket illimitato

## Convenzioni di codice
- Type hints ovunque
- Async/await per I/O (SDK sync wrappato con `to_thread`)
- Logging strutturato (non print)
- Ogni strategia estende `Strategy` base class
- Config via .env + dataclass frozen
- `threading.Lock` per dati condivisi con WS thread
- Coin names (es. `ETH`, `BTC`) non pair names (es. `ETHUSDC`)
