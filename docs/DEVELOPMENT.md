# Development Guide

Technical reference for the Hyperliquid Perpetual Trading Bot codebase.

## Project Structure

```
binance/
├── main.py                    # Entry point: Bot class, main loop
├── CLAUDE.md                  # AI assistant instructions
├── README.md                  # User documentation
├── docs/                      # Detailed documentation
├── config/
│   ├── settings.py            # HyperliquidConfig, AIConfig, RiskConfig, etc.
│   └── pairs.py               # CORE_COINS, discover_perp_coins()
├── core/
│   ├── client.py              # HyperliquidClient (async wrapper)
│   ├── market_data.py         # WebSocket feeds, candle cache, indicators
│   ├── ai_advisor.py          # Claude Code CLI advisor (structured JSON)
│   └── types.py               # Decision dataclass
├── strategies/
│   ├── base.py                # Strategy ABC
│   ├── mean_reversion.py      # Mean Reversion LONG + SHORT
│   ├── rsi_divergence.py      # RSI Divergence swing detection
│   ├── multi_strategy.py      # Multi-strategy aggregator
│   ├── trend_filter.py        # EMA50/EMA200 trend classification
│   ├── cooldown.py            # Per-symbol + global cooldown
│   └── grid.py                # Disabled (kept for history)
├── risk/
│   ├── position_sizer.py      # Kelly Criterion f/4
│   ├── position_tracker.py    # LONG/SHORT, trailing SL, time stop
│   └── risk_manager.py        # Kill switch, daily pause, validation
├── data/
│   ├── db.py                  # SQLite async, schema, migrations
│   └── backtest.py            # BacktestEngine
├── schemas/                   # JSON Schema for AI advisor output
├── prompts/                   # System prompt for AI advisor
├── web/                       # Next.js dashboard (TypeScript + Tailwind)
├── tests/                     # pytest unit + integration tests
├── utils/                     # Logger, Telegram
└── scripts/                   # Test, backtest, download history
```

## Hyperliquid SDK

- **Info** class: read-only data (`all_mids`, `user_state`, `candles_snapshot`, `meta`)
- **Exchange** class: trading (`market_open`, `market_close`, `order`, `cancel`, `update_leverage`)
- SDK is synchronous — wrapped with `asyncio.to_thread()`
- Auth: private key EIP-712 (SDK handles signing)
- `info.meta()` -> universe with `szDecimals` for rounding
- `info.all_mids()` -> mid prices for all assets
- WebSocket: SDK internal (threading) for `allMids` and `candle` streams

## Configuration (.env)

```env
HL_PRIVATE_KEY=0x...
HL_ACCOUNT_ADDRESS=0x...
HL_TESTNET=true
HL_DEFAULT_LEVERAGE=2
HL_MARGIN_MODE=cross
HL_MAX_FUNDING_RATE=0.0005
```

## Decision Flow

```
Loop (60s) -> Risk Refresh -> Check SL/TP -> Update Strategies
           -> Generate Candidates (rule-based)
           -> AI Advisor (optional, via claude CLI)
           -> Risk Validate -> Execute
```

### Actions
- `BUY` -> LONG entry (`exchange.market_open(coin, is_buy=True, sz)`)
- `SHORT` -> SHORT entry (`exchange.market_open(coin, is_buy=False, sz)`)
- `CLOSE` -> Close position (`exchange.market_close(coin)`)
- `HOLD` -> Do nothing

## AI Advisor

Single Claude Code CLI call per cycle. The AI acts as an advisor that can:
- Review open positions: HOLD, CLOSE, or ADJUST (SL/TP/leverage)
- Review proposed opportunities: approve (BUY/SHORT), defer (HOLD with conditions), or skip
- Deferred opportunities are re-evaluated when conditions are met

Invocation: `claude -p <payload> --json-schema <schema> --output-format json --model opus`

## Strategies

### Mean Reversion (LONG + SHORT)
- LONG: Trend BULLISH + RSI<25 + price<=BB_lower + vol_ratio>=1.2 + RSI_1h<60
- SHORT: Trend BEARISH + RSI>75 + price>=BB_upper + vol_ratio>=1.2 + RSI_1h>40
- Exit LONG: RSI>70 or price>=BB_upper
- Exit SHORT: RSI<30 or price<=BB_lower
- Funding rate check before every entry

### RSI Divergence
- LONG: Bullish divergence (price lower low, RSI higher low) + trend BULLISH/NEUTRAL
- SHORT: Bearish divergence (price higher high, RSI lower high) + trend BEARISH/NEUTRAL
- Exit LONG: RSI > 55 (configurable)
- Exit SHORT: RSI < 35 (configurable)

### Trend Filter (EMA50/EMA200 on 1h)
- BULLISH: EMA50>EMA200, price>EMA50, slope>0
- BEARISH: EMA50<EMA200, price<EMA50, slope<0

## Risk Management

### Rules
- Max per trade: 10% bankroll
- Stop loss: -1% (auto-generated)
- Take profit: off by default (trailing stop handles profit-taking). Enable with `AUTO_TAKE_PROFIT=true`
- Daily drawdown max: -5% -> pause
- Total drawdown max: -15% -> kill switch
- Max positions: dynamic (1 per 200 USDC, capped at `MAX_OPEN_POSITIONS`)
- Min balance: 50 USDC
- 5 consecutive losses -> kill switch

### Trailing Stop (direction-aware)
- Break-even: gain>=1.0% -> SL at entry
- Trailing: gain>=1.5% -> SL follows at 1.0% distance
- Tight: gain>=2.5% -> SL at 0.75% distance
- LONG: SL only rises. SHORT: SL only falls.

### Time Stop
- 4h+ with PnL<0.5% -> auto-close

### Kelly Criterion (f/4)
- Cold start (<10 trades): 5% fixed
- Post cold start: quarter-Kelly * confidence
- Min order: 10 USDC

### Cooldown
- Per-symbol: 30 min after loss
- Global: 15 min after 3 consecutive losses

## Fee Structure (Hyperliquid)
- Maker: 0.015%
- Taker: 0.045%
- Round-trip: ~0.06%
- Bot uses MARKET orders (taker)

## API Endpoints
- REST/WS Mainnet: `https://api.hyperliquid.xyz`
- REST/WS Testnet: `https://api.hyperliquid-testnet.xyz`
- Rate limits: 1200 weight/min REST, WebSocket unlimited

## Code Conventions
- Type hints everywhere
- Async/await for I/O (sync SDK wrapped with `to_thread`)
- Structured logging (no print)
- Every strategy extends `Strategy` base class
- Config via .env + frozen dataclass
- `threading.Lock` for data shared with WS thread
- Coin names (e.g. `ETH`, `BTC`) not pair names (e.g. `ETHUSDC`)

## Testing

See [testing.md](testing.md) for full details.

```bash
# Unit tests (fast, no external dependencies)
.venv/bin/python -m pytest tests/ -m "not integration" -v

# Integration tests (require claude CLI)
.venv/bin/python -m pytest tests/ -m integration -v
```
