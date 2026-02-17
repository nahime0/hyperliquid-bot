# Configuration

## Overview

All configurations are managed through environment variables loaded from `.env` via `python-dotenv`. Configurations are represented as frozen (immutable) dataclasses in `config/settings.py`.

## Environment Variables

### Hyperliquid API

| Variable | Default | Type | Description |
|---|---|---|---|
| `HL_PRIVATE_KEY` | `""` | str | Wallet private key (EIP-712). Required for trading. |
| `HL_ACCOUNT_ADDRESS` | `""` | str | Wallet public address. Used for account state queries. |
| `HL_TESTNET` | `true` | bool | `true` for testnet, `false` for mainnet. |
| `HL_DEFAULT_LEVERAGE` | `2` | int | Default leverage applied to core coins at startup. 2-3x recommended. |
| `HL_MARGIN_MODE` | `cross` | str | `cross` (shared) or `isolated` (per position). |
| `HL_MAX_FUNDING_RATE` | `0.0005` | float | Max acceptable funding rate (0.05%/8h). Entry blocked if exceeded. |

**API URLs (derived automatically):**
- Testnet: `https://api.hyperliquid-testnet.xyz`
- Mainnet: `https://api.hyperliquid.xyz`

### AI Advisor (Claude Code CLI)

| Variable | Default | Type | Description |
|---|---|---|---|
| `AI_DECISION_INTERVAL` | `60` | int | Seconds between decision cycles. |
| `AI_MODEL` | `opus` | str | Claude model to use (opus, haiku, sonnet). |
| `AI_TIMEOUT` | `120` | int | CLI call timeout (seconds). |
| `AI_MIN_CONFIDENCE` | `0.6` | float | Below this threshold, action is forced to HOLD. |
| `AI_FALLBACK_ON_ERROR` | `HOLD` | str | Default action if the AI doesn't respond. |
| `AI_LOG_REASONING` | `true` | bool | Save full reasoning of each decision to DB. |

### Risk Management

| Variable | Default | Type | Description |
|---|---|---|---|
| `MAX_TRADE_PCT` | `15` | float | Max % of bankroll per single trade. |
| `STOP_LOSS_PCT` | `1.0` | float | Automatic stop loss (%). Applied if not specified. |
| `TAKE_PROFIT_PCT` | `1.5` | float | Automatic take profit (%). |
| `MAX_DAILY_DRAWDOWN_PCT` | `5.0` | float | Max daily drawdown. Exceeded -> pause until midnight UTC. |
| `MAX_TOTAL_DRAWDOWN_PCT` | `15.0` | float | Max total drawdown from peak. Exceeded -> kill switch. |
| `MAX_OPEN_POSITIONS` | `15` | int | Max open positions (hard cap for dynamic, fixed value if static). |
| `DYNAMIC_POSITIONS` | `true` | bool | Scale max positions with balance (1 slot per USDC_PER_POSITION). |
| `USDC_PER_POSITION` | `25.0` | float | USDC needed for each position slot. |
| `TARGET_UTILIZATION` | `0.50` | float | Target capital utilization (50% of balance as margin). |
| `MAX_SIZE_BOOST` | `2.5` | float | Max multiplier on base sizing when utilization is low. |
| `AUTO_TAKE_PROFIT` | `false` | bool | If true, generates automatic TP. If false, trailing stop handles profits. |
| `MIN_BALANCE_USDC` | `50.0` | float | Minimum balance. Below -> kill switch. |
| `MIN_HOLDING_MINUTES` | `15` | int | Minimum holding time before AI can close a position. |

### Trailing Stop

| Variable | Default | Type | Description |
|---|---|---|---|
| `TRAILING_BREAKEVEN_PCT` | `1.0` | float | Move SL to entry price after gain >= X%. |
| `TRAILING_START_PCT` | `1.5` | float | Start trailing after gain >= X%. |
| `TRAILING_DISTANCE_PCT` | `1.0` | float | Trail distance from max/min price. |
| `TRAILING_TIGHT_PCT` | `2.5` | float | Tighten trail after gain >= X%. |
| `TRAILING_TIGHT_DISTANCE_PCT` | `0.75` | float | Tight trail distance. |

### Time Stop

| Variable | Default | Type | Description |
|---|---|---|---|
| `TIME_STOP_HOURS` | `4.0` | float | Close positions after X hours with low PnL. |
| `TIME_STOP_MIN_PNL_PCT` | `0.5` | float | Only if PnL < X%. |

### Cooldown

| Variable | Default | Type | Description |
|---|---|---|---|
| `SYMBOL_COOLDOWN_SEC` | `1800` | int | Per-symbol cooldown after loss (30 min). |
| `GLOBAL_COOLDOWN_SEC` | `900` | int | Global cooldown after N losses (15 min). |
| `GLOBAL_COOLDOWN_LOSSES` | `3` | int | Number of losses that triggers global cooldown. |

### Market Discovery & Liquidity

| Variable | Default | Type | Description |
|---|---|---|---|
| `MIN_PAIR_VOLUME` | `50000` | float | Minimum 24h volume in USDC to include a coin in discovery. Filters on `dayNtlVlm` from Hyperliquid. |
| `MAX_COINS` | `60` | int | Max perpetual coins to monitor. |
| `MAX_SPREAD_PCT` | `0.5` | float | Max bid-ask spread % for entry. If spread > threshold, BUY/SHORT/SCALE_UP blocked. 0 = disabled. |
| `MIN_CANDLE_VOLUME_USDC` | `10000` | float | Minimum USDC volume per candle in strategies. Coins with lower candle volume are skipped. |

### Telegram (optional)

| Variable | Default | Type | Description |
|---|---|---|---|
| `TELEGRAM_BOT_TOKEN` | `""` | str | Telegram bot token (from @BotFather). |
| `TELEGRAM_CHAT_ID` | `""` | str | Chat ID for receiving notifications. |

### Other

| Variable | Default | Type | Description |
|---|---|---|---|
| `LOG_LEVEL` | `INFO` | str | Log level: DEBUG, INFO, WARNING, ERROR. |
| `DB_PATH` | `data/trading_bot.db` | str | SQLite database path. |

## Configuration Dataclasses

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

The `--live` flag overrides `HL_TESTNET=true` at runtime:

```python
if args.live:
    settings = replace(settings, hyperliquid=replace(settings.hyperliquid, testnet=False))
```
