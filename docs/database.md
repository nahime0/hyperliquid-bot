# Database

## Overview

The bot uses **SQLite** (via `aiosqlite`) for asynchronous persistence. The database is located by default at `data/trading_bot.db`.

WAL mode is enabled to allow concurrent reads (Next.js dashboard via `better-sqlite3` + bot).

## Schema

### `trades` — Trade History

| Column | Type | Description |
|---|---|---|
| `id` | INTEGER PK | Auto-increment |
| `timestamp` | TEXT | UTC ISO-8601 |
| `symbol` | TEXT | Coin (e.g. ETH, BTC) |
| `side` | TEXT | BUY, SHORT, SELL, CLOSE |
| `price` | REAL | Execution price |
| `quantity` | REAL | Quantity |
| `fee` | REAL | Fee paid |
| `fee_asset` | TEXT | Fee asset (default: USDC) |
| `pnl` | REAL | Net profit/loss (NULL if not calculable) |
| `strategy` | TEXT | mean_reversion, ai |
| `order_id` | TEXT | Hyperliquid order ID |
| `notes` | TEXT | Additional notes (e.g. [PAPER]) |
| `direction` | TEXT | LONG or SHORT (default: LONG) |

### `positions` — Open/Closed Positions

| Column | Type | Description |
|---|---|---|
| `id` | INTEGER PK | Auto-increment |
| `symbol` | TEXT | Coin |
| `entry_price` | REAL | Entry price |
| `exit_price` | REAL | Exit price (NULL if open) |
| `quantity` | REAL | Quantity |
| `stop_loss` | REAL | Current stop loss |
| `take_profit` | REAL | Take profit |
| `trailing_sl` | REAL | Trailing stop loss (dynamically updated) |
| `original_sl` | REAL | Original SL (before trailing) |
| `max_price_seen` | REAL | Maximum price seen (for LONG trailing) |
| `min_price_seen` | REAL | Minimum price seen (for SHORT trailing) |
| `strategy` | TEXT | Strategy that opened the position |
| `status` | TEXT | OPEN or CLOSED |
| `pnl` | REAL | Net PnL (calculated at close) |
| `close_reason` | TEXT | Close reason (stop_loss, take_profit, trailing_sl, time_stop, strategy_exit) |
| `opened_at` | TEXT | Open timestamp (UTC) |
| `closed_at` | TEXT | Close timestamp (UTC) |
| `direction` | TEXT | LONG or SHORT (default: LONG) |
| `leverage` | INTEGER | Leverage used (default: 1) |
| `liquidation_price` | REAL | Liquidation price |
| `funding_paid` | REAL | Funding paid (default: 0) |
| `ask_close` | INTEGER | 1 if manual close requested from webapp (default: 0) |

### `balance_snapshots` — Balance History

| Column | Type | Description |
|---|---|---|
| `id` | INTEGER PK | Auto-increment |
| `timestamp` | TEXT | UTC ISO-8601 |
| `total_usdc` | REAL | Total balance in USDC |
| `positions` | TEXT | JSON with open positions |
| `peak_balance` | REAL | Historical balance peak |

### `orders` — Placed Orders

| Column | Type | Description |
|---|---|---|
| `id` | INTEGER PK | Auto-increment |
| `order_id` | TEXT UNIQUE | Hyperliquid order ID |
| `timestamp` | TEXT | UTC ISO-8601 |
| `symbol` | TEXT | Coin |
| `side` | TEXT | BUY or SELL |
| `order_type` | TEXT | LIMIT or MARKET |
| `price` | REAL | Order price |
| `quantity` | REAL | Quantity |
| `status` | TEXT | NEW, PARTIALLY_FILLED, FILLED, CANCELED |
| `strategy` | TEXT | Strategy |
| `filled_price` | REAL | Actual fill price |
| `filled_quantity` | REAL | Executed quantity |

### `ai_decisions` — AI Decision History

| Column | Type | Description |
|---|---|---|
| `id` | INTEGER PK | Auto-increment |
| `timestamp` | TEXT | UTC ISO-8601 |
| `snapshot_hash` | TEXT | SHA-256 hash of snapshot (dedup) |
| `action` | TEXT | BUY, SHORT, SELL, HOLD, CLOSE |
| `symbol` | TEXT | Target coin |
| `confidence` | REAL | Confidence 0-1 |
| `reasoning` | TEXT | AI reasoning |
| `raw_response` | TEXT | Full JSON response |
| `executed` | INTEGER | 1 if the order was executed |
| `tier` | TEXT | prescreen, haiku, opus, fallback |
| `cost_usd` | REAL | Estimated API call cost |

## Migrations

Migrations are applied automatically at startup via `ALTER TABLE ... ADD COLUMN`:

```python
_MIGRATIONS = [
    # Phase 11: Trailing stop
    "ALTER TABLE positions ADD COLUMN trailing_sl REAL",
    "ALTER TABLE positions ADD COLUMN original_sl REAL",
    "ALTER TABLE positions ADD COLUMN max_price_seen REAL",
    # Phase 13: Hyperliquid perpetuals
    "ALTER TABLE positions ADD COLUMN direction TEXT DEFAULT 'LONG'",
    "ALTER TABLE positions ADD COLUMN leverage INTEGER DEFAULT 1",
    "ALTER TABLE positions ADD COLUMN liquidation_price REAL",
    "ALTER TABLE positions ADD COLUMN funding_paid REAL DEFAULT 0",
    "ALTER TABLE positions ADD COLUMN min_price_seen REAL",
    "ALTER TABLE trades ADD COLUMN direction TEXT DEFAULT 'LONG'",
    "ALTER TABLE positions ADD COLUMN partial_closed INTEGER DEFAULT 0",
    "ALTER TABLE positions ADD COLUMN ask_close INTEGER DEFAULT 0",
]
```

Migrations are idempotent (the `try/except` ignores already existing columns).

## Backward Compatibility

Old Binance records have `symbol='ETHUSDC'`, new Hyperliquid ones have `symbol='ETH'`. They are distinguishable and don't conflict. To clean up old data:

```sql
DELETE FROM positions WHERE symbol LIKE '%USDC';
DELETE FROM trades WHERE symbol LIKE '%USDC';
```

## Main Queries Used by the Bot

```sql
-- Last 50 trades
SELECT * FROM trades ORDER BY id DESC LIMIT 50

-- Trade stats (last 100 with PnL)
SELECT pnl FROM trades WHERE pnl IS NOT NULL ORDER BY id DESC LIMIT 100

-- Open positions
SELECT * FROM positions WHERE status = 'OPEN' ORDER BY id

-- Last balance snapshot
SELECT * FROM balance_snapshots ORDER BY id DESC LIMIT 1

-- AI costs by tier
SELECT tier, COUNT(*) as cnt, COALESCE(SUM(cost_usd), 0) as total
FROM ai_decisions GROUP BY tier
```

## Dashboard Queries

The Next.js dashboard (`web/`) reads the DB in read-only mode (WAL mode) via `better-sqlite3`. Data is refreshed automatically via client-side SWR polling.

### Manual Close (ask_close)

The dashboard can request a position close via `POST /api/positions/close` with `{ "position_id": N }`. This sets `ask_close = 1` on the position through a separate writable DB connection (`getWriteDb()`). The bot checks every ~20 seconds (in the SL/TP monitor) for positions with `ask_close = 1` and closes them at the current market price. The event is logged as `MANUAL_CLOSE` with source `webapp`.
