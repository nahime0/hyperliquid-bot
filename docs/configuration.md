# Configurazione

## Panoramica

Tutte le configurazioni sono gestite tramite variabili d'ambiente caricate da `.env` via `python-dotenv`. Le configurazioni sono rappresentate come dataclass frozen (immutabili) in `config/settings.py`.

## Variabili d'ambiente

### Hyperliquid API

| Variabile | Default | Tipo | Descrizione |
|---|---|---|---|
| `HL_PRIVATE_KEY` | `""` | str | Private key del wallet (EIP-712). Necessaria per trading. |
| `HL_ACCOUNT_ADDRESS` | `""` | str | Indirizzo pubblico del wallet. Usato per query account state. |
| `HL_TESTNET` | `true` | bool | `true` per testnet, `false` per mainnet. |
| `HL_DEFAULT_LEVERAGE` | `2` | int | Leva di default applicata ai core coins all'avvio. 2-3x consigliato. |
| `HL_MARGIN_MODE` | `cross` | str | `cross` (condiviso) o `isolated` (per posizione). |
| `HL_MAX_FUNDING_RATE` | `0.0005` | float | Max funding rate accettabile (0.05%/8h). Entry bloccata se superato. |

**API URLs (derivati automaticamente):**
- Testnet: `https://api.hyperliquid-testnet.xyz`
- Mainnet: `https://api.hyperliquid.xyz`

### AI Advisor (Claude Code CLI)

| Variabile | Default | Tipo | Descrizione |
|---|---|---|---|
| `AI_DECISION_INTERVAL` | `60` | int | Secondi tra un ciclo decisionale e l'altro. |
| `AI_MODEL` | `opus` | str | Modello Claude da usare (opus, haiku, sonnet). |
| `AI_TIMEOUT` | `120` | int | Timeout per la chiamata CLI (secondi). |
| `AI_MIN_CONFIDENCE` | `0.6` | float | Sotto questa soglia, l'azione e' forzata a HOLD. |
| `AI_FALLBACK_ON_ERROR` | `HOLD` | str | Azione di default se l'AI non risponde. |
| `AI_LOG_REASONING` | `true` | bool | Salva il reasoning completo di ogni decisione nel DB. |

### Risk Management

| Variabile | Default | Tipo | Descrizione |
|---|---|---|---|
| `MAX_TRADE_PCT` | `15` | float | Max % del bankroll per singolo trade. |
| `STOP_LOSS_PCT` | `1.0` | float | Stop loss automatico (%). Applicato se non specificato. |
| `TAKE_PROFIT_PCT` | `1.5` | float | Take profit automatico (%). |
| `MAX_DAILY_DRAWDOWN_PCT` | `5.0` | float | Drawdown giornaliero max. Supera → pausa fino a mezzanotte UTC. |
| `MAX_TOTAL_DRAWDOWN_PCT` | `15.0` | float | Drawdown totale max dal peak. Supera → kill switch. |
| `MAX_OPEN_POSITIONS` | `15` | int | Max posizioni aperte (hard cap per dynamic, valore fisso se static). |
| `DYNAMIC_POSITIONS` | `true` | bool | Scala max posizioni con balance (1 slot ogni USDC_PER_POSITION). |
| `USDC_PER_POSITION` | `25.0` | float | USDC necessari per ogni slot di posizione. |
| `TARGET_UTILIZATION` | `0.50` | float | Target utilizzo capitale (50% del balance come margine). |
| `MAX_SIZE_BOOST` | `2.5` | float | Max moltiplicatore sul sizing base quando utilizzo e' basso. |
| `AUTO_TAKE_PROFIT` | `false` | bool | Se true, genera TP automatico. Se false, il trailing stop gestisce i profitti. |
| `MIN_BALANCE_USDC` | `50.0` | float | Balance minimo. Sotto → kill switch. |
| `MIN_HOLDING_MINUTES` | `15` | int | Tempo minimo di holding prima che l'AI possa chiudere una posizione. |

### Trailing Stop

| Variabile | Default | Tipo | Descrizione |
|---|---|---|---|
| `TRAILING_BREAKEVEN_PCT` | `1.0` | float | Sposta SL a entry price dopo gain >= X%. |
| `TRAILING_START_PCT` | `1.5` | float | Inizia trailing dopo gain >= X%. |
| `TRAILING_DISTANCE_PCT` | `1.0` | float | Distanza trail dal prezzo max/min. |
| `TRAILING_TIGHT_PCT` | `2.5` | float | Tighten trail dopo gain >= X%. |
| `TRAILING_TIGHT_DISTANCE_PCT` | `0.75` | float | Distanza trail stretta. |

### Time Stop

| Variabile | Default | Tipo | Descrizione |
|---|---|---|---|
| `TIME_STOP_HOURS` | `4.0` | float | Chiudi posizioni dopo X ore con PnL basso. |
| `TIME_STOP_MIN_PNL_PCT` | `0.5` | float | Solo se PnL < X%. |

### Cooldown

| Variabile | Default | Tipo | Descrizione |
|---|---|---|---|
| `SYMBOL_COOLDOWN_SEC` | `1800` | int | Cooldown per-symbol dopo perdita (30 min). |
| `GLOBAL_COOLDOWN_SEC` | `900` | int | Cooldown globale dopo N perdite (15 min). |
| `GLOBAL_COOLDOWN_LOSSES` | `3` | int | Numero perdite che triggera il cooldown globale. |

### Market Discovery & Liquidity

| Variabile | Default | Tipo | Descrizione |
|---|---|---|---|
| `MIN_PAIR_VOLUME` | `50000` | float | Volume 24h minimo in USDC per includere un coin nella discovery. Filtro su `dayNtlVlm` da Hyperliquid. |
| `MAX_COINS` | `60` | int | Max coin perpetual da monitorare. |
| `MAX_SPREAD_PCT` | `0.5` | float | Max bid-ask spread % per entry. Se spread > soglia, BUY/SHORT/SCALE_UP bloccati. 0 = disabilitato. |
| `MIN_CANDLE_VOLUME_USDC` | `10000` | float | Volume minimo USDC per candela nelle strategie. Coins con volume candela inferiore vengono skippati. |

### Telegram (opzionale)

| Variabile | Default | Tipo | Descrizione |
|---|---|---|---|
| `TELEGRAM_BOT_TOKEN` | `""` | str | Token del bot Telegram (da @BotFather). |
| `TELEGRAM_CHAT_ID` | `""` | str | Chat ID per ricevere notifiche. |

### Altro

| Variabile | Default | Tipo | Descrizione |
|---|---|---|---|
| `LOG_LEVEL` | `INFO` | str | Livello log: DEBUG, INFO, WARNING, ERROR. |
| `DB_PATH` | `data/trading_bot.db` | str | Percorso del database SQLite. |

## Dataclass di configurazione

```python
# config/settings.py

Settings
├── hyperliquid: HyperliquidConfig
│   ├── private_key, account_address
│   ├── testnet, api_url (property)
│   ├── default_leverage, margin_mode
│   └── max_funding_rate
├── ai: AIConfig
│   ├── decision_interval, model, timeout
│   ├── min_confidence, fallback_on_error
│   └── log_reasoning
├── risk: RiskConfig
│   ├── max_trade_pct, stop_loss_pct, take_profit_pct, auto_take_profit
│   ├── drawdown limits, position limits (dynamic/static)
│   ├── trailing stop parameters
│   ├── time stop parameters
│   └── cooldown parameters
├── market: MarketConfig
│   ├── min_pair_volume, max_coins
│   └── max_spread_pct
├── strategy: StrategyConfig
│   ├── active_strategies, rsi_div params
│   ├── min_candle_volume_usdc
│   └── intervals (primary, mr, trend)
├── telegram: TelegramConfig
│   ├── bot_token, chat_id
│   └── enabled (property)
├── log_level: str
├── db_path: str
└── project_root: Path
```

## CLI Override

Il flag `--live` override `HL_TESTNET=true` a runtime:

```python
if args.live:
    settings = replace(settings, hyperliquid=replace(settings.hyperliquid, testnet=False))
```
