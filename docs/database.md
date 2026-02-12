# Database

## Overview

Il bot usa **SQLite** (via `aiosqlite`) per persistenza asincrona. Il database si trova di default in `data/trading_bot.db`.

WAL mode e' abilitato per permettere letture concorrenti (Next.js dashboard via `better-sqlite3` + bot).

## Schema

### `trades` — Storico operazioni

| Colonna | Tipo | Descrizione |
|---|---|---|
| `id` | INTEGER PK | Auto-increment |
| `timestamp` | TEXT | UTC ISO-8601 |
| `symbol` | TEXT | Coin (es. ETH, BTC) |
| `side` | TEXT | BUY, SHORT, SELL, CLOSE |
| `price` | REAL | Prezzo di esecuzione |
| `quantity` | REAL | Quantita' |
| `fee` | REAL | Commissione pagata |
| `fee_asset` | TEXT | Asset della fee (default: USDC) |
| `pnl` | REAL | Profitto/perdita netto (NULL se non calcolabile) |
| `strategy` | TEXT | mean_reversion, ai |
| `order_id` | TEXT | ID ordine Hyperliquid |
| `notes` | TEXT | Note aggiuntive (es. [PAPER]) |
| `direction` | TEXT | LONG o SHORT (default: LONG) |

### `positions` — Posizioni aperte/chiuse

| Colonna | Tipo | Descrizione |
|---|---|---|
| `id` | INTEGER PK | Auto-increment |
| `symbol` | TEXT | Coin |
| `entry_price` | REAL | Prezzo di entrata |
| `exit_price` | REAL | Prezzo di uscita (NULL se aperta) |
| `quantity` | REAL | Quantita' |
| `stop_loss` | REAL | Stop loss corrente |
| `take_profit` | REAL | Take profit |
| `trailing_sl` | REAL | Trailing stop loss (aggiornato dinamicamente) |
| `original_sl` | REAL | SL originale (prima del trailing) |
| `max_price_seen` | REAL | Prezzo massimo visto (per trailing LONG) |
| `min_price_seen` | REAL | Prezzo minimo visto (per trailing SHORT) |
| `strategy` | TEXT | Strategia che ha aperto la posizione |
| `status` | TEXT | OPEN o CLOSED |
| `pnl` | REAL | PnL netto (calcolato alla chiusura) |
| `close_reason` | TEXT | Motivo chiusura (stop_loss, take_profit, trailing_sl, time_stop, strategy_exit) |
| `opened_at` | TEXT | Timestamp apertura (UTC) |
| `closed_at` | TEXT | Timestamp chiusura (UTC) |
| `direction` | TEXT | LONG o SHORT (default: LONG) |
| `leverage` | INTEGER | Leva utilizzata (default: 1) |
| `liquidation_price` | REAL | Prezzo di liquidazione |
| `funding_paid` | REAL | Funding pagato (default: 0) |

### `balance_snapshots` — Storico bilancio

| Colonna | Tipo | Descrizione |
|---|---|---|
| `id` | INTEGER PK | Auto-increment |
| `timestamp` | TEXT | UTC ISO-8601 |
| `total_usdc` | REAL | Balance totale in USDC |
| `positions` | TEXT | JSON con posizioni aperte |
| `peak_balance` | REAL | Massimo storico del balance |

### `orders` — Ordini piazzati

| Colonna | Tipo | Descrizione |
|---|---|---|
| `id` | INTEGER PK | Auto-increment |
| `order_id` | TEXT UNIQUE | ID ordine Hyperliquid |
| `timestamp` | TEXT | UTC ISO-8601 |
| `symbol` | TEXT | Coin |
| `side` | TEXT | BUY o SELL |
| `order_type` | TEXT | LIMIT o MARKET |
| `price` | REAL | Prezzo ordine |
| `quantity` | REAL | Quantita' |
| `status` | TEXT | NEW, PARTIALLY_FILLED, FILLED, CANCELED |
| `strategy` | TEXT | Strategia |
| `filled_price` | REAL | Prezzo effettivo |
| `filled_quantity` | REAL | Quantita' eseguita |

### `ai_decisions` — Storico decisioni AI

| Colonna | Tipo | Descrizione |
|---|---|---|
| `id` | INTEGER PK | Auto-increment |
| `timestamp` | TEXT | UTC ISO-8601 |
| `snapshot_hash` | TEXT | Hash SHA-256 dello snapshot (dedup) |
| `action` | TEXT | BUY, SHORT, SELL, HOLD, CLOSE |
| `symbol` | TEXT | Coin target |
| `confidence` | REAL | Confidenza 0-1 |
| `reasoning` | TEXT | Motivazione dell'AI |
| `raw_response` | TEXT | JSON completo della risposta |
| `executed` | INTEGER | 1 se l'ordine e' stato eseguito |
| `tier` | TEXT | prescreen, haiku, opus, fallback |
| `cost_usd` | REAL | Costo stimato della chiamata API |

## Migrazioni

Le migrazioni vengono applicate automaticamente all'avvio tramite `ALTER TABLE ... ADD COLUMN`:

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
]
```

Le migrazioni sono idempotenti (il `try/except` ignora colonne gia' esistenti).

## Backward Compatibility

I vecchi record Binance hanno `symbol='ETHUSDC'`, i nuovi Hyperliquid hanno `symbol='ETH'`. Sono distinguibili e non conflittuano. Per pulire i vecchi dati:

```sql
DELETE FROM positions WHERE symbol LIKE '%USDC';
DELETE FROM trades WHERE symbol LIKE '%USDC';
```

## Query principali usate dal bot

```sql
-- Ultimi 50 trade
SELECT * FROM trades ORDER BY id DESC LIMIT 50

-- Trade stats (ultimi 100 con PnL)
SELECT pnl FROM trades WHERE pnl IS NOT NULL ORDER BY id DESC LIMIT 100

-- Posizioni aperte
SELECT * FROM positions WHERE status = 'OPEN' ORDER BY id

-- Ultimo balance snapshot
SELECT * FROM balance_snapshots ORDER BY id DESC LIMIT 1

-- Costi AI per tier
SELECT tier, COUNT(*) as cnt, COALESCE(SUM(cost_usd), 0) as total
FROM ai_decisions GROUP BY tier
```

## Dashboard queries

La dashboard Next.js (`web/`) legge il DB in read-only (WAL mode) tramite `better-sqlite3`. I dati vengono aggiornati automaticamente via SWR polling lato client.
