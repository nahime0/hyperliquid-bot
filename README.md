# Hyperliquid Perpetual Trading Bot

![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)
![Hyperliquid](https://img.shields.io/badge/Hyperliquid-Perpetuals-7B3FE4?logo=ethereum&logoColor=white)
![Claude AI](https://img.shields.io/badge/AI-Claude_Anthropic-191919?logo=anthropic&logoColor=white)
![License](https://img.shields.io/badge/License-Private-red)
![Status](https://img.shields.io/badge/Status-Paper_Trading-blue)

Bot di trading automatico in Python per **Hyperliquid Perpetual Futures**, con AI advisor opzionale (**Claude Code CLI**). Il bot opera 24/7 con strategie **Mean Reversion + RSI Divergence** (LONG e SHORT), supporto a leva conservativa (2-3x), e fee ultra-basse (0.06% round-trip).

> **Perche' Hyperliquid**: Binance Spot ha fee troppo alte (0.15% RT) per micro-profitti. Binance Futures e' bloccato per utenti EU (MiCA). Hyperliquid offre fee 3x piu' basse, possibilita' di shortare, leva, e nessun KYC.

> **USDC**: Hyperliquid settla nativamente in USDC. USDT non e' disponibile per utenti EU su Binance per regolamento MiCA.

---

## Indice

1. [Overview](#overview)
2. [Architettura](#architettura)
3. [AI Advisor](#ai-advisor)
4. [Strategie](#strategie)
5. [Risk Management](#risk-management)
6. [Setup](#setup)
7. [Avvio](#avvio)
8. [Dashboard](#dashboard)
9. [Database](#database)
10. [Struttura Progetto](#struttura-progetto)
11. [Configurazione](#configurazione)
12. [Documentazione Dettagliata](#documentazione-dettagliata)

---

## Overview

Il bot implementa un loop continuo che ogni 60 secondi:

1. **Raccoglie dati** dal mercato via WebSocket e REST API di Hyperliquid
2. **Calcola indicatori tecnici** (RSI, Bollinger Bands, MACD, EMA) su timeframe multipli (15m, 1h)
3. **Filtra i trend** con EMA50/EMA200 su 1h (BULLISH/BEARISH/NEUTRAL)
4. **Genera decisioni autonome** (BUY, SHORT, CLOSE) tramite regole codificate (Mean Reversion)
5. **AI advisor opzionale** (Claude Code CLI) approva/defera/aggiusta
6. **Valida con il Risk Manager** (kill switch, drawdown, Kelly sizing)
7. **Esegue l'ordine** su Hyperliquid (o lo logga in modalita' paper)

### Caratteristiche principali

- **Rule-based + AI advisor**: le decisioni vengono generate da regole codificate, l'AI (Claude Code CLI) fa da advisor opzionale
- **LONG e SHORT**: supporto completo per entrambe le direzioni su perpetual futures
- **AI advisor semplice**: una singola chiamata CLI per ciclo, con supporto defer e adjustments
- **Risk management rigoroso**: kill switch, pausa giornaliera, Kelly Criterion per il sizing, trailing stop, time stop
- **Leva conservativa**: 2-3x con cross margin
- **Fee ultra-basse**: 0.06% round-trip (vs 0.15% su Binance Spot)
- **Dashboard web**: monitoraggio real-time via Next.js (in `web/`)
- **Notifiche Telegram**: alert su trade eseguiti e condizioni critiche
- **Discovery automatica**: scopre fino a 60 coin perpetual in base al volume

### Coin monitorate

| Tipo | Coin |
|---|---|
| **Core** (sempre incluse) | BTC, ETH, SOL |
| **Discovery** (top per volume) | Fino a 60 coin perpetual |

---

## Architettura

```
                        ┌─────────────────────────────────────────────┐
                        │              MAIN LOOP (60s)                │
                        │                                             │
                        │  1. Risk Refresh ──────────┐                │
                        │  2. Check Positions ────────┤                │
                        │  3. Update Trend Filter ────┤                │
                        │  4. Update Strategies ──────┤                │
                        │  5. Generate Candidates ─────┤                │
                        │  5b. Check Deferred ─────────┤                │
                        │  6. AI Advisor (optional) ───┤                │
                        │  7. Risk Validate ───────────┤                │
                        │  8. Execute ─────────────────┘                │
                        └────────────────┬────────────────────────────┘
                                         │
              ┌──────────────────────────┼──────────────────────────┐
              │                          │                          │
    ┌─────────▼─────────┐    ┌──────────▼──────────┐    ┌─────────▼─────────┐
    │ HYPERLIQUID CLIENT │    │    AI ADVISOR        │    │   RISK MANAGER    │
    │                    │    │                      │    │                   │
    │  REST (via SDK)    │    │  Claude Code CLI     │    │  Kill Switch      │
    │  WebSocket feeds   │    │  1 call/cycle        │    │  Daily Pause      │
    │  Market orders     │    │  Structured JSON     │    │  Liquidation Mon. │
    │  Position mgmt     │    │  Deferred tracking   │    │  Kelly Criterion  │
    │  Leverage control  │    │  SL/TP adjustments   │    │  Trailing Stop    │
    └─────────┬─────────┘    └──────────────────────┘    └─────────┬─────────┘
              │                                                     │
    ┌─────────▼─────────┐    ┌─────────────────────┐    ┌─────────▼─────────┐
    │   MARKET DATA      │    │   STRATEGIE          │    │  POSITION TRACKER │
    │                    │    │                      │    │                   │
    │  allMids WS stream │    │  Mean Reversion      │    │  LONG / SHORT     │
    │  Candle WS streams │    │  RSI Divergence      │    │  Trailing SL      │
    │  Candle cache      │    │  Multi-Strategy      │    │  Time Stop        │
    │  RSI, BB, EMA      │    │  Trend Filter        │    │  Direction-aware   │
    │  indicators        │    │  Cooldown            │    │  AI adjustments   │
    └───────────────────┘    └─────────────────────┘    └───────────────────┘
              │
    ┌─────────▼─────────┐    ┌───────────────────┐
    │    DATABASE        │    │   DASHBOARD       │
    │                    │    │                   │
    │  SQLite (aiosqlite)│    │  Next.js (web/)   │
    │  Trades, Positions │    │  SQLite readonly  │
    │  Balance Snapshots │    │  bot_status.json  │
    └───────────────────┘    └───────────────────┘
```

### Stack tecnologico

| Componente | Tecnologia |
|---|---|
| Linguaggio | Python 3.11+ (async/await) |
| Exchange | `hyperliquid-python-sdk` (REST + WebSocket) |
| AI Advisor | Claude Code CLI (structured JSON) |
| Indicatori | `ta` library (RSI, BB, MACD, EMA) |
| Dati | `pandas` per time series, `aiosqlite` per persistenza |
| Dashboard | Next.js (in `web/`, standalone) |
| Notifiche | Telegram Bot API via `aiohttp` |
| Auth | `eth-account` (EIP-712 wallet signing) |
| Config | `python-dotenv` + dataclass immutabili |

---

## AI Advisor

Il bot usa un **AI advisor opzionale** basato su Claude Code CLI. Le strategie generano candidate decisions, l'AI le rivede e puo':

- **Approvare** entry (BUY/SHORT) con eventuali aggiustamenti a SL/TP/size
- **Deferire** entry (HOLD con condizioni: attesa cicli o prezzo target)
- **Chiudere** posizioni aperte (CLOSE)
- **Aggiustare** posizioni (ADJUST: SL/TP/leverage)

```
Strategie → Candidates → AI Advisor (1 call/cycle) → Risk Manager → Execute
```

Una singola chiamata CLI per ciclo, con structured JSON (`--json-schema`).
Se non ci sono posizioni ne' opportunita', la chiamata viene saltata.
Su errore/timeout: log warning, il bot continua autonomamente.

Per dettagli, vedi [`docs/ai-engine.md`](docs/ai-engine.md).

---

## Strategie

### Mean Reversion (LONG + SHORT)

La strategia principale genera **decisioni autonome** basate su regole codificate. L'AI interviene solo come reviewer opzionale.

#### Condizioni LONG Entry (tutte devono essere vere)

| Condizione | Soglia |
|---|---|
| Trend (1h) | BULLISH (EMA50 > EMA200, price > EMA50, slope > 0) |
| RSI (15m) | < 25 (deep oversold) |
| Bollinger Band (15m) | Price <= lower BB * 1.005 |
| Volume | Ratio >= 1.2 (sopra media) |
| RSI macro (1h) | < 60 (no divergenza) |
| Funding rate | |rate| < 0.05%/8h |
| Cooldown | Nessun trade recente sullo stesso coin |
| Posizione | Nessuna posizione aperta sullo stesso coin |

#### Condizioni SHORT Entry (tutte devono essere vere)

| Condizione | Soglia |
|---|---|
| Trend (1h) | BEARISH (EMA50 < EMA200, price < EMA50, slope < 0) |
| RSI (15m) | > 75 (strict overbought) |
| Bollinger Band (15m) | Price >= upper BB * 0.995 |
| Volume | Ratio >= 1.2 |
| RSI macro (1h) | > 40 |
| Funding rate | |rate| < 0.05%/8h |
| Cooldown | Nessun trade recente |
| Posizione | Nessuna posizione aperta |

#### Condizioni Exit

| Direzione | Trigger | Azione |
|---|---|---|
| LONG | RSI > 70 OR price >= upper BB | CLOSE |
| SHORT | RSI < 30 OR price <= lower BB | CLOSE |
| Entrambi | SL/TP raggiunto | Auto-close |
| Entrambi | Trailing SL raggiunto | Auto-close |
| Entrambi | Time stop (4h+ con PnL < 0.5%) | Auto-close |

### Trend Filter

Classifica ogni coin come BULLISH, BEARISH, o NEUTRAL basandosi su EMA50/EMA200 su candele 1h.

```
BULLISH:  EMA50 > EMA200  AND  price > EMA50  AND  slope > 0
BEARISH:  EMA50 < EMA200  AND  price < EMA50  AND  slope < 0
NEUTRAL:  tutto il resto
```

---

## Risk Management

Il Risk Manager ha **potere di veto assoluto** su ogni decisione. Anche se la strategia dice BUY, il Risk Manager puo' bloccare.

### Regole

| Regola | Valore | Descrizione |
|---|---|---|
| Max per trade | 10% del bankroll | Nessun trade rischia piu' del 10% del margine |
| Stop loss | -1% per trade | Applicato automaticamente se non specificato |
| Take profit | +1.5% per trade | Suggerito automaticamente se non specificato |
| Max drawdown giornaliero | -5% | Pausa automatica fino a mezzanotte UTC |
| Max drawdown totale | -15% dal peak | **Kill switch**: ferma TUTTO |
| Max posizioni aperte | 5 | Blocca nuove entry se raggiunto |
| Balance minimo | 50 USDC | Kill switch se scende sotto |
| Perdite consecutive | 5 | Kill switch |
| Confidenza minima | 0.5 | Blocca trade con confidenza troppo bassa |
| Max leva | 3x | Limite leverage |
| Liquidation buffer | 5% | Warning se posizione vicina a liquidazione |
| Holding minimo | 15 min | Impedisce chiusure troppo rapide |

### Trailing Stop

| Fase | Trigger | Azione |
|---|---|---|
| Break-even | Gain >= 1.0% | SL sale/scende a entry price |
| Trailing | Gain >= 1.5% | SL segue a 1.0% di distanza |
| Tight trailing | Gain >= 2.5% | SL si stringe a 0.75% di distanza |

Direction-aware: per LONG il SL sale, per SHORT il SL scende.

### Time Stop

Posizioni aperte da piu' di **4 ore** con PnL < **0.5%** vengono chiuse automaticamente.

### Position Sizing: Fractional Kelly

```
f = (win_rate * payoff_ratio - (1 - win_rate)) / payoff_ratio
size = bankroll * (f / 4)  # quarter-Kelly conservativo
```

Con aggiustamenti:
- **Cold start** (< 10 trade): 5% fisso del bankroll
- **Confidence scaling**: size moltiplicata per la confidenza
- **Hard cap**: mai piu' del `max_trade_pct` configurato
- **Minimum**: ordini sotto 10 USDC vengono scartati

### Cooldown

| Tipo | Durata | Trigger |
|---|---|---|
| Per-symbol | 30 min | Dopo una perdita sullo stesso coin |
| Globale | 15 min | Dopo 3 perdite consecutive |

---

## Setup

### Prerequisiti

- **Python 3.11+** (testato con 3.14)
- **Wallet Hyperliquid** (o API wallet dedicato)
- **Claude Code CLI** installato (per AI advisor, opzionale)
- (Opzionale) Bot Telegram per le notifiche

### Installazione

```bash
git clone <repo-url>
cd binance

python3 -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt
```

### Dipendenze

```
hyperliquid-python-sdk    # SDK Hyperliquid (REST + WebSocket)
eth-account               # Wallet signing (EIP-712)
pandas>=2.1.0             # Time series
ta>=0.11.0                # Indicatori tecnici (RSI, BB, MACD, EMA)
aiosqlite>=0.19.0         # SQLite asincrono
python-dotenv>=1.0.0      # Config da .env
aiohttp>=3.9.0            # Telegram notifications
```

### Configurazione .env

Copia `.env.example` e modifica:

```env
# Hyperliquid API
HL_PRIVATE_KEY=0x...          # private key del wallet
HL_ACCOUNT_ADDRESS=0x...      # indirizzo pubblico
HL_TESTNET=true               # true=testnet, false=mainnet
HL_DEFAULT_LEVERAGE=2          # leva (2-3x consigliato)
HL_MARGIN_MODE=cross           # cross o isolated
HL_MAX_FUNDING_RATE=0.0005     # max funding rate accettabile

# AI Advisor (opzionale, richiede Claude Code CLI)
AI_MODEL=opus              # modello (opus, sonnet, haiku)
AI_TIMEOUT=120             # timeout secondi

# Telegram (opzionale)
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
```

Vedi la sezione [Configurazione](#configurazione) per tutte le variabili disponibili.

---

## Avvio

### Modalita'

| Flag | Descrizione |
|---|---|
| `--testnet` | Usa il testnet Hyperliquid (default) |
| `--live` | Usa mainnet (**attenzione: soldi veri!**) |
| `--paper` | Paper trading: logga le decisioni ma non piazza ordini |
| `--no-ai` | Disabilita l'AI review, usa pure regole codificate |
| `--once` | Esegue un solo ciclo e poi esce |

### Esempi

```bash
# Testnet con AI review (modalita' consigliata per iniziare)
.venv/bin/python main.py

# Paper trading (nessun ordine reale, logga tutto)
.venv/bin/python main.py --paper

# Un solo ciclo per verificare che tutto funzioni
.venv/bin/python main.py --paper --once

# Senza AI review (pure rule-based)
.venv/bin/python main.py --no-ai

# Test veloce (paper + no AI + singolo ciclo)
.venv/bin/python main.py --paper --no-ai --once

# MAINNET TRADING (usare con cautela!)
.venv/bin/python main.py --live
```

### Shutdown graceful

Il bot gestisce `SIGINT` (Ctrl+C) e `SIGTERM`:

1. Salva un ultimo balance snapshot
2. Chiude le connessioni WebSocket
3. Chiude il database
4. Chiude il client Hyperliquid

---

## Dashboard

Dashboard web standalone basata su **Next.js**, nella cartella `web/`. Legge il database SQLite in modalita' readonly e `bot_status.json` per visualizzare lo stato del bot. La dashboard e' separata dal processo del bot.

```bash
# Development (con hot reload)
cd web && npm run dev

# Production
cd web && npm run build && npm start
```

Apri `http://localhost:3000` nel browser.

| Sezione | Contenuto |
|---|---|
| **Balance** | Saldo corrente, peak, drawdown |
| **Equity Curve** | Grafico storico del balance |
| **AI Decisions** | Ultime decisioni con azione, confidenza, reasoning |
| **Trades** | Ultimi 50 trade con P&L |
| **Trade Stats** | Win rate, profit factor, perdite consecutive |
| **Positions** | Posizioni aperte con direction, leverage, liquidation price |
| **Bot Status** | Modalita', ciclo corrente, stato strategie |

---

## Database

SQLite (via `aiosqlite`) con 5 tabelle principali. Schema completo in [`docs/database.md`](docs/database.md).

| Tabella | Contenuto |
|---|---|
| `trades` | Storico operazioni con PnL, direction |
| `positions` | Posizioni aperte/chiuse con SL/TP, trailing, direction, leverage |
| `balance_snapshots` | Storico bilancio per equity curve |
| `orders` | Ordini piazzati |

Il database si trova in `data/trading_bot.db`.

---

## Struttura Progetto

```
binance/
├── main.py                          # Entry point: orchestratore (Bot class)
├── CLAUDE.md                        # Istruzioni per Claude Code
├── README.md                        # Questa documentazione
├── requirements.txt                 # Dipendenze Python
├── .env                             # Variabili d'ambiente (NON committare!)
├── .env.example                     # Template .env
│
├── docs/                            # Documentazione dettagliata
│   ├── architecture.md              # Architettura del sistema
│   ├── strategies.md                # Strategie di trading
│   ├── risk-management.md           # Sistema di risk management
│   ├── ai-engine.md                 # AI Decision Engine
│   ├── configuration.md             # Tutte le variabili di configurazione
│   ├── database.md                  # Schema database
│   └── deployment.md                # Setup, installazione, deployment
│
├── config/
│   ├── __init__.py
│   ├── settings.py                  # Dataclass: HyperliquidConfig, AIConfig, RiskConfig, etc.
│   └── pairs.py                     # CORE_COINS, ALL_COINS, discover_perp_coins()
│
├── core/
│   ├── __init__.py
│   ├── client.py                    # HyperliquidClient: wrapper async con retry/backoff
│   ├── market_data.py               # MarketData: WebSocket feeds, candle cache, indicatori
│   ├── ai_advisor.py                # AI Advisor: Claude Code CLI integration
│   └── types.py                     # Decision dataclass
│
├── strategies/
│   ├── __init__.py                  # Esporta Strategy, MeanReversionStrategy, TrendFilter
│   ├── base.py                      # ABC Strategy: start(), stop(), update(), get_state()
│   ├── mean_reversion.py            # Mean Reversion: LONG + SHORT, segnali autonomi
│   ├── rsi_divergence.py            # RSI Divergence: swing detection
│   ├── multi_strategy.py            # Multi-strategy aggregator
│   ├── trend_filter.py              # EMA50/EMA200 trend classification
│   ├── cooldown.py                  # CooldownTracker: per-symbol + global
│   └── grid.py                      # GridStrategy (disabilitata, mantenuta per history)
│
├── risk/
│   ├── __init__.py
│   ├── position_sizer.py            # Kelly Criterion f/4, confidence scaling
│   ├── position_tracker.py          # Position tracking: LONG/SHORT, trailing SL, time stop
│   └── risk_manager.py              # Kill switch, daily pause, validation, metrics
│
├── data/
│   ├── __init__.py
│   ├── db.py                        # Database: SQLite async, schema, CRUD, migrations
│   ├── backtest.py                  # BacktestEngine: simulazione su dati storici
│   ├── history/                     # CSV scaricati (es. ETH_15m.csv)
│   ├── backtest_results/            # Output backtest (equity curve CSV + PNG)
│   ├── trading_bot.db               # Database SQLite (generato a runtime)
│   └── bot_status.json              # Stato live per dashboard (scritto dal bot)
│
├── schemas/
│   ├── __init__.py
│   └── ai_advisor_output.json       # JSON Schema per output AI advisor
│
├── prompts/
│   ├── __init__.py
│   └── ai_advisor.md                # System prompt per AI advisor
│
├── web/                                 # Dashboard Next.js (standalone)
│   ├── package.json
│   ├── next.config.ts
│   └── src/                             # Sorgenti Next.js (App Router)
│
├── utils/
│   ├── __init__.py
│   ├── logger.py                    # Logging strutturato: console + file rotante (5MB x 5)
│   └── telegram.py                  # TelegramNotifier: notify_trade(), notify_alert()
│
├── scripts/
│   ├── __init__.py
│   ├── test_connection.py           # Test connessione Hyperliquid
│   ├── download_history.py          # Scarica klines storiche
│   ├── backtest_runner.py           # Esegue backtest con parameter sweep
│   └── strategy_sweep.py            # Sweep parametri strategie
│
├── tests/
│   └── __init__.py
│
└── logs/
    └── bot.log                      # File di log (rotazione 5MB x 5)
```

---

## Configurazione

### Hyperliquid API

| Variabile | Default | Descrizione |
|---|---|---|
| `HL_PRIVATE_KEY` | (vuoto) | Private key del wallet (EIP-712) |
| `HL_ACCOUNT_ADDRESS` | (vuoto) | Indirizzo pubblico del wallet |
| `HL_TESTNET` | `true` | `true` per testnet, `false` per mainnet |
| `HL_DEFAULT_LEVERAGE` | `2` | Leva di default (2-3x consigliato) |
| `HL_MARGIN_MODE` | `cross` | `cross` o `isolated` |
| `HL_MAX_FUNDING_RATE` | `0.0005` | Max funding rate accettabile (0.05%/8h) |

### AI Advisor

| Variabile | Default | Descrizione |
|---|---|---|
| `AI_DECISION_INTERVAL` | `60` | Secondi tra un ciclo e l'altro |
| `AI_MODEL` | `opus` | Modello Claude Code (`opus`, `sonnet`, `haiku`) |
| `AI_TIMEOUT` | `120` | Timeout per la chiamata CLI (secondi) |
| `AI_MIN_CONFIDENCE` | `0.6` | Sotto questa soglia → HOLD forzato |
| `AI_FALLBACK_ON_ERROR` | `HOLD` | Azione di default se l'AI non risponde |
| `AI_LOG_REASONING` | `true` | Logga il reasoning dell'AI |
| `AI_MAX_SPREAD_PCT` | `0.5` | Spread massimo per screening |

### Risk Management

| Variabile | Default | Descrizione |
|---|---|---|
| `MAX_TRADE_PCT` | `10` | Max % del bankroll per trade |
| `STOP_LOSS_PCT` | `1.0` | Stop loss automatico (%) |
| `TAKE_PROFIT_PCT` | `1.5` | Take profit automatico (%) |
| `MAX_DAILY_DRAWDOWN_PCT` | `5.0` | Drawdown giornaliero max → pausa |
| `MAX_TOTAL_DRAWDOWN_PCT` | `15.0` | Drawdown totale max → kill switch |
| `MAX_OPEN_POSITIONS` | `5` | Max posizioni aperte |
| `MIN_BALANCE_USDC` | `50.0` | Balance minimo → kill switch |
| `TRAILING_BREAKEVEN_PCT` | `1.0` | Sposta SL a entry dopo +X% |
| `TRAILING_START_PCT` | `1.5` | Inizia trailing dopo +X% |
| `TRAILING_DISTANCE_PCT` | `1.0` | Distanza trail dal max/min |
| `TRAILING_TIGHT_PCT` | `2.5` | Tighten trail dopo +X% |
| `TRAILING_TIGHT_DISTANCE_PCT` | `0.75` | Distanza tight trail |
| `TIME_STOP_HOURS` | `4.0` | Chiudi posizioni dopo X ore |
| `TIME_STOP_MIN_PNL_PCT` | `0.5` | Solo se PnL < X% |
| `SYMBOL_COOLDOWN_SEC` | `1800` | Cooldown per-symbol (30 min) |
| `GLOBAL_COOLDOWN_SEC` | `900` | Cooldown globale (15 min) |
| `GLOBAL_COOLDOWN_LOSSES` | `3` | Trigger cooldown dopo N perdite |

### Market Discovery

| Variabile | Default | Descrizione |
|---|---|---|
| `MIN_PAIR_VOLUME` | `50000` | Volume 24h minimo (USDC) |
| `MAX_COINS` | `60` | Max coin perpetual da monitorare |

### Altro

| Variabile | Default | Descrizione |
|---|---|---|
| `LOG_LEVEL` | `INFO` | Livello di log |
| `DB_PATH` | `data/trading_bot.db` | Percorso database SQLite |
| `TELEGRAM_BOT_TOKEN` | (vuoto) | Token bot Telegram |
| `TELEGRAM_CHAT_ID` | (vuoto) | Chat ID Telegram |

---

## Documentazione Dettagliata

Per approfondimenti, consulta la cartella [`docs/`](docs/):

| Documento | Contenuto |
|---|---|
| [`architecture.md`](docs/architecture.md) | Architettura del sistema, flusso dati, componenti |
| [`strategies.md`](docs/strategies.md) | Mean Reversion LONG/SHORT, Trend Filter, segnali |
| [`risk-management.md`](docs/risk-management.md) | Kill switch, trailing stop, Kelly sizing, cooldown |
| [`ai-engine.md`](docs/ai-engine.md) | AI Advisor (Claude Code CLI), schema, defer |
| [`DEVELOPMENT.md`](docs/DEVELOPMENT.md) | Guida tecnica, struttura progetto, convenzioni |
| [`configuration.md`](docs/configuration.md) | Tutte le variabili .env con dettagli |
| [`database.md`](docs/database.md) | Schema completo tabelle, migrazioni |
| [`deployment.md`](docs/deployment.md) | Setup, installazione, deployment su server |

---

## Note importanti

- **Mai** eseguire il bot in modalita' `--live` senza aver validato su testnet
- Il file `.env` contiene chiavi private: **non committarlo**
- Le fee Hyperliquid sono 0.045% taker per leg (0.06% round-trip)
- Con leva 2x e SL 1%, il rischio di liquidazione e' praticamente zero
- Il bot logga tutto in `logs/bot.log` (rotazione 5MB, 5 file di backup)
- Per deployment su server, usare `systemd` o `supervisor` per restart automatico
