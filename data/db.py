from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite

from utils.logger import get_logger

logger = get_logger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    symbol TEXT NOT NULL,
    side TEXT NOT NULL CHECK (side IN ('BUY', 'SELL', 'SHORT', 'CLOSE')),
    price REAL NOT NULL,
    quantity REAL NOT NULL,
    fee REAL NOT NULL DEFAULT 0,
    fee_asset TEXT DEFAULT 'USDC',
    pnl REAL,
    strategy TEXT,
    order_id TEXT,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS balance_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    total_usdc REAL NOT NULL,
    positions TEXT NOT NULL DEFAULT '{}',
    peak_balance REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id TEXT UNIQUE NOT NULL,
    timestamp TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    symbol TEXT NOT NULL,
    side TEXT NOT NULL CHECK (side IN ('BUY', 'SELL', 'SHORT', 'CLOSE')),
    order_type TEXT NOT NULL CHECK (order_type IN ('LIMIT', 'MARKET')),
    price REAL,
    quantity REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'NEW',
    strategy TEXT,
    filled_price REAL,
    filled_quantity REAL,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS ai_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    snapshot_hash TEXT,
    action TEXT NOT NULL,
    symbol TEXT,
    confidence REAL NOT NULL,
    reasoning TEXT,
    raw_response TEXT,
    executed INTEGER NOT NULL DEFAULT 0,
    execution_result TEXT
);

CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades(symbol);
CREATE INDEX IF NOT EXISTS idx_trades_timestamp ON trades(timestamp);
CREATE INDEX IF NOT EXISTS idx_orders_symbol ON orders(symbol);
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);
CREATE INDEX IF NOT EXISTS idx_ai_decisions_timestamp ON ai_decisions(timestamp);

CREATE TABLE IF NOT EXISTS positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    entry_price REAL NOT NULL,
    quantity REAL NOT NULL,
    stop_loss REAL,
    take_profit REAL,
    strategy TEXT DEFAULT 'ai',
    status TEXT NOT NULL DEFAULT 'OPEN',
    opened_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    closed_at TEXT,
    exit_price REAL,
    pnl REAL,
    close_reason TEXT
);

CREATE INDEX IF NOT EXISTS idx_positions_status ON positions(status);
CREATE INDEX IF NOT EXISTS idx_positions_symbol ON positions(symbol);

CREATE TABLE IF NOT EXISTS coins (
    symbol TEXT PRIMARY KEY,
    first_seen TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    last_seen TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    is_active INTEGER NOT NULL DEFAULT 1,
    sz_decimals INTEGER,
    avg_volume_24h REAL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    cycle INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    event_type TEXT NOT NULL,
    source TEXT NOT NULL,
    action TEXT,
    confidence REAL,
    reasoning TEXT,
    details TEXT DEFAULT '{}',
    position_id INTEGER,
    trade_id INTEGER
);

CREATE INDEX IF NOT EXISTS idx_events_symbol ON events(symbol);
CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp);
CREATE INDEX IF NOT EXISTS idx_events_event_type ON events(event_type);
CREATE INDEX IF NOT EXISTS idx_events_cycle ON events(cycle);

CREATE TABLE IF NOT EXISTS cycle_summaries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    cycle INTEGER UNIQUE NOT NULL,
    duration_sec REAL,
    balance_usdc REAL,
    drawdown_pct REAL,
    daily_drawdown_pct REAL,
    open_positions INTEGER,
    capital_utilization REAL,
    coins_monitored INTEGER,
    signals_generated INTEGER DEFAULT 0,
    decisions_approved INTEGER DEFAULT 0,
    decisions_blocked INTEGER DEFAULT 0,
    trades_executed INTEGER DEFAULT 0,
    kill_switch INTEGER DEFAULT 0,
    daily_paused INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_cycle_summaries_cycle ON cycle_summaries(cycle);
CREATE INDEX IF NOT EXISTS idx_cycle_summaries_timestamp ON cycle_summaries(timestamp);

CREATE TABLE IF NOT EXISTS deferred_opportunities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    original_action TEXT NOT NULL,
    deferred_at_cycle INTEGER NOT NULL,
    conditions TEXT NOT NULL DEFAULT '{}',
    type TEXT NOT NULL DEFAULT 'opportunity',
    strategy_type TEXT NOT NULL DEFAULT 'unknown',
    confidence REAL NOT NULL DEFAULT 0.0,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    UNIQUE(symbol, strategy_type, type)
);

CREATE TABLE IF NOT EXISTS bot_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE TABLE IF NOT EXISTS bot_config (
    id INTEGER PRIMARY KEY CHECK (id = 1),

    -- main_ : General settings
    main_trading_active INTEGER NOT NULL DEFAULT 1,
    main_log_level TEXT NOT NULL DEFAULT 'INFO',
    main_db_path TEXT NOT NULL DEFAULT 'data/trading_bot.db',

    -- hl_ : Hyperliquid connection
    hl_testnet INTEGER NOT NULL DEFAULT 1,
    hl_default_leverage INTEGER NOT NULL DEFAULT 2,
    hl_margin_mode TEXT NOT NULL DEFAULT 'cross',
    hl_max_funding_rate REAL NOT NULL DEFAULT 0.0005,

    -- ai_ : AI advisor
    ai_advisor TEXT NOT NULL DEFAULT 'claude',
    ai_decision_interval INTEGER NOT NULL DEFAULT 60,
    ai_min_confidence REAL NOT NULL DEFAULT 0.6,
    ai_fallback_on_error TEXT NOT NULL DEFAULT 'HOLD',
    ai_log_reasoning INTEGER NOT NULL DEFAULT 1,
    ai_model TEXT NOT NULL DEFAULT 'opus',
    ai_timeout INTEGER NOT NULL DEFAULT 180,

    -- risk_ : Risk management
    risk_max_trade_pct REAL NOT NULL DEFAULT 15.0,
    risk_stop_loss_pct REAL NOT NULL DEFAULT 2.0,
    risk_take_profit_pct REAL NOT NULL DEFAULT 1.5,
    risk_auto_take_profit INTEGER NOT NULL DEFAULT 0,
    risk_max_daily_drawdown_pct REAL NOT NULL DEFAULT 5.0,
    risk_max_total_drawdown_pct REAL NOT NULL DEFAULT 15.0,
    risk_max_open_positions INTEGER NOT NULL DEFAULT 15,
    risk_dynamic_positions INTEGER NOT NULL DEFAULT 1,
    risk_usdc_per_position REAL NOT NULL DEFAULT 25.0,
    risk_target_utilization REAL NOT NULL DEFAULT 0.50,
    risk_max_size_boost REAL NOT NULL DEFAULT 2.5,
    risk_min_balance_usdc REAL NOT NULL DEFAULT 50.0,
    risk_min_holding_minutes INTEGER NOT NULL DEFAULT 15,
    risk_max_leverage INTEGER NOT NULL DEFAULT 3,
    risk_liquidation_buffer_pct REAL NOT NULL DEFAULT 5.0,
    risk_sl_tp_grace_seconds INTEGER NOT NULL DEFAULT 30,
    risk_trailing_half_pct REAL NOT NULL DEFAULT 0.5,
    risk_trailing_breakeven_pct REAL NOT NULL DEFAULT 0.6,
    risk_trailing_start_pct REAL NOT NULL DEFAULT 0.8,
    risk_trailing_distance_pct REAL NOT NULL DEFAULT 1.0,
    risk_trailing_tight_pct REAL NOT NULL DEFAULT 2.5,
    risk_trailing_tight_distance_pct REAL NOT NULL DEFAULT 0.75,
    risk_time_stop_hours REAL NOT NULL DEFAULT 4.0,
    risk_time_stop_min_pnl_pct REAL NOT NULL DEFAULT 0.5,
    risk_symbol_cooldown_sec INTEGER NOT NULL DEFAULT 1800,
    risk_global_cooldown_sec INTEGER NOT NULL DEFAULT 900,
    risk_global_cooldown_losses INTEGER NOT NULL DEFAULT 3,
    risk_use_atr_sl INTEGER NOT NULL DEFAULT 1,
    risk_atr_sl_multiplier REAL NOT NULL DEFAULT 2.5,
    risk_atr_sl_min_pct REAL NOT NULL DEFAULT 0.8,
    risk_atr_sl_max_pct REAL NOT NULL DEFAULT 2.5,
    risk_risk_per_trade_pct REAL NOT NULL DEFAULT 1.0,
    risk_partial_tp_enabled INTEGER NOT NULL DEFAULT 0,
    risk_partial_tp_pct REAL NOT NULL DEFAULT 50.0,
    risk_partial_tp_trigger_pct REAL NOT NULL DEFAULT 2.0,
    risk_min_rr_ratio REAL NOT NULL DEFAULT 1.5,
    risk_use_atr_trailing INTEGER NOT NULL DEFAULT 1,
    risk_atr_trailing_multiplier REAL NOT NULL DEFAULT 1.5,
    risk_atr_trailing_tight_multiplier REAL NOT NULL DEFAULT 1.0,
    risk_atr_trailing_min_pct REAL NOT NULL DEFAULT 0.5,
    risk_atr_trailing_max_pct REAL NOT NULL DEFAULT 2.0,
    risk_min_expected_move_ratio REAL NOT NULL DEFAULT 1.5,
    risk_slippage_estimate REAL NOT NULL DEFAULT 0.0003,

    -- market_ : Market/discovery
    market_min_pair_volume REAL NOT NULL DEFAULT 50000.0,
    market_max_coins INTEGER NOT NULL DEFAULT 60,
    market_max_spread_pct REAL NOT NULL DEFAULT 0.5,
    market_coin_blacklist TEXT NOT NULL DEFAULT 'AXS,MOODENG,CC,HYPE',

    -- strat_ : Strategy general
    strat_active_strategies TEXT NOT NULL DEFAULT 'mean_reversion,rsi_divergence,trend_following,bb_squeeze,breakout,btc_correlation,buy_the_dip,ema_crossover,funding_rate,macd_divergence,mtf_confluence,session_momentum,volume_spike',
    strat_min_candle_volume_usdc REAL NOT NULL DEFAULT 10000.0,
    strat_primary_interval TEXT NOT NULL DEFAULT '5m',
    strat_mr_interval TEXT NOT NULL DEFAULT '15m',
    strat_trend_interval TEXT NOT NULL DEFAULT '1h',

    -- mr_ : Mean Reversion
    -- (no specific settings beyond shared ones)

    -- rsidiv_ : RSI Divergence
    rsidiv_period INTEGER NOT NULL DEFAULT 14,
    rsidiv_swing_window INTEGER NOT NULL DEFAULT 5,
    rsidiv_long_exit REAL NOT NULL DEFAULT 65.0,
    rsidiv_short_exit REAL NOT NULL DEFAULT 25.0,

    -- tf_ : Trend Following
    tf_allow_short INTEGER NOT NULL DEFAULT 0,
    tf_change_threshold REAL NOT NULL DEFAULT 4.0,

    -- btd_ : Buy the Dip
    btd_dip_min_pct REAL NOT NULL DEFAULT 0.5,
    btd_dip_max_pct REAL NOT NULL DEFAULT 3.0,
    btd_volume_spike REAL NOT NULL DEFAULT 1.5,

    -- ema_ : EMA Crossover
    ema_cross_fast INTEGER NOT NULL DEFAULT 9,
    ema_cross_slow INTEGER NOT NULL DEFAULT 21,
    ema_cross_max_bars INTEGER NOT NULL DEFAULT 2,

    -- bbs_ : BB Squeeze
    bbs_squeeze_percentile REAL NOT NULL DEFAULT 20.0,
    bbs_lookback_bars INTEGER NOT NULL DEFAULT 100,
    bbs_min_volume_spike REAL NOT NULL DEFAULT 1.5,

    -- macd_ : MACD Divergence
    macd_div_swing_window INTEGER NOT NULL DEFAULT 5,
    macd_div_recency INTEGER NOT NULL DEFAULT 40,

    -- vs_ : Volume Spike Reversal
    vs_spike_threshold REAL NOT NULL DEFAULT 3.0,
    vs_wick_ratio REAL NOT NULL DEFAULT 2.0,

    -- bo_ : Support/Resistance Breakout
    bo_lookback_hours INTEGER NOT NULL DEFAULT 24,
    bo_min_breakout_pct REAL NOT NULL DEFAULT 0.1,
    bo_confirm_bars INTEGER NOT NULL DEFAULT 2,

    -- fr_ : Funding Rate Contrarian
    fr_extreme_negative REAL NOT NULL DEFAULT -0.0005,
    fr_extreme_positive REAL NOT NULL DEFAULT 0.0005,
    fr_normalize_threshold REAL NOT NULL DEFAULT 0.0001,

    -- btc_ : BTC Correlation Lag
    btc_min_move_pct REAL NOT NULL DEFAULT 1.0,
    btc_min_lag_pct REAL NOT NULL DEFAULT 0.5,
    btc_catch_up_pct REAL NOT NULL DEFAULT 0.2,

    -- sm_ : Session Momentum
    sm_min_session_change REAL NOT NULL DEFAULT 0.5,
    sm_entry_window_hours INTEGER NOT NULL DEFAULT 2,
    sm_exit_hours REAL NOT NULL DEFAULT 4.0,

    -- mtf_ : Multi-Timeframe Confluence
    mtf_rsi_oversold REAL NOT NULL DEFAULT 35.0,
    mtf_rsi_overbought REAL NOT NULL DEFAULT 65.0,
    mtf_rsi_slope_bars INTEGER NOT NULL DEFAULT 3,

    -- telegram_ : Notifications
    telegram_bot_token TEXT NOT NULL DEFAULT '',
    telegram_chat_id TEXT NOT NULL DEFAULT '',

    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

-- Seed the single config row if it doesn't exist
INSERT OR IGNORE INTO bot_config (id) VALUES (1);

CREATE TABLE IF NOT EXISTS config_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    field_name TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'dashboard'
);

CREATE INDEX IF NOT EXISTS idx_config_history_timestamp ON config_history(timestamp);
CREATE INDEX IF NOT EXISTS idx_config_history_field ON config_history(field_name);
"""

_MIGRATIONS = [
    "ALTER TABLE ai_decisions ADD COLUMN tier TEXT DEFAULT 'cli'",
    "ALTER TABLE ai_decisions ADD COLUMN cost_usd REAL DEFAULT 0.0",
    "ALTER TABLE positions ADD COLUMN max_price_seen REAL",
    "ALTER TABLE positions ADD COLUMN trailing_sl REAL",
    "ALTER TABLE positions ADD COLUMN original_sl REAL",
    # Hyperliquid perpetuals: direction, leverage, liquidation, funding, min_price
    "ALTER TABLE positions ADD COLUMN direction TEXT DEFAULT 'LONG'",
    "ALTER TABLE positions ADD COLUMN leverage INTEGER DEFAULT 1",
    "ALTER TABLE positions ADD COLUMN liquidation_price REAL",
    "ALTER TABLE positions ADD COLUMN funding_paid REAL DEFAULT 0",
    "ALTER TABLE positions ADD COLUMN min_price_seen REAL",
    "ALTER TABLE trades ADD COLUMN direction TEXT DEFAULT 'LONG'",
    "ALTER TABLE positions ADD COLUMN partial_closed INTEGER DEFAULT 0",
    "ALTER TABLE positions ADD COLUMN ask_close INTEGER DEFAULT 0",
    "ALTER TABLE deferred_opportunities ADD COLUMN strategy_type TEXT NOT NULL DEFAULT 'unknown'",
    "ALTER TABLE deferred_opportunities ADD COLUMN confidence REAL NOT NULL DEFAULT 0.0",
    # Phase 24: new config columns
    "ALTER TABLE bot_config ADD COLUMN risk_min_expected_move_ratio REAL NOT NULL DEFAULT 1.5",
    "ALTER TABLE bot_config ADD COLUMN risk_slippage_estimate REAL NOT NULL DEFAULT 0.0003",
    # Trading active flag
    "ALTER TABLE bot_config ADD COLUMN main_trading_active INTEGER NOT NULL DEFAULT 1",
]

# Multi-statement migration: rebuild deferred_opportunities with correct UNIQUE constraint
# (old table had UNIQUE(symbol, type), need UNIQUE(symbol, strategy_type, type))
_DEFERRED_REBUILD = """
CREATE TABLE IF NOT EXISTS deferred_opportunities_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    original_action TEXT NOT NULL,
    deferred_at_cycle INTEGER NOT NULL,
    conditions TEXT NOT NULL DEFAULT '{}',
    type TEXT NOT NULL DEFAULT 'opportunity',
    strategy_type TEXT NOT NULL DEFAULT 'unknown',
    confidence REAL NOT NULL DEFAULT 0.0,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    UNIQUE(symbol, strategy_type, type)
);
INSERT OR IGNORE INTO deferred_opportunities_new
    (symbol, original_action, deferred_at_cycle, conditions, type, strategy_type, confidence, created_at)
    SELECT symbol, original_action, deferred_at_cycle, conditions, type, strategy_type, confidence, created_at
    FROM deferred_opportunities;
DROP TABLE deferred_opportunities;
ALTER TABLE deferred_opportunities_new RENAME TO deferred_opportunities;
"""


class Database:
    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)
        self._db: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(str(self._db_path), timeout=30)
        self._db.row_factory = aiosqlite.Row
        # WAL mode: allows concurrent reads while writing
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA busy_timeout=5000")
        await self._db.executescript(_SCHEMA)
        await self._db.commit()
        # Run migrations (safe to re-run — duplicate column errors are silenced)
        for migration in _MIGRATIONS:
            try:
                await self._db.execute(migration)
                await self._db.commit()
            except Exception:
                pass  # Column already exists
        # Rebuild deferred_opportunities if UNIQUE constraint is stale (missing strategy_type)
        try:
            rows = await self._db.execute_fetchall(
                "SELECT sql FROM sqlite_master WHERE name='deferred_opportunities' AND type='table'"
            )
            if rows:
                ddl = rows[0][0] or ""
                if "UNIQUE" in ddl and "strategy_type" not in ddl.split("UNIQUE")[1]:
                    await self._db.executescript(_DEFERRED_REBUILD)
                    logger.info("Rebuilt deferred_opportunities with UNIQUE(symbol, strategy_type, type)")
        except Exception:
            pass  # Fresh DB or already correct
        logger.info("Database connected: %s", self._db_path)

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None
            logger.info("Database closed")

    @property
    def db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("Database not connected. Call connect() first.")
        return self._db

    # ── Trades ──────────────────────────────────────────────

    async def insert_trade(
        self,
        symbol: str,
        side: str,
        price: float,
        quantity: float,
        fee: float = 0,
        fee_asset: str = "USDC",
        pnl: float | None = None,
        strategy: str | None = None,
        order_id: str | None = None,
        notes: str | None = None,
    ) -> int:
        cursor = await self.db.execute(
            """INSERT INTO trades (symbol, side, price, quantity, fee, fee_asset, pnl, strategy, order_id, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (symbol, side, price, quantity, fee, fee_asset, pnl, strategy, order_id, notes),
        )
        await self.db.commit()
        return cursor.lastrowid  # type: ignore[return-value]

    async def get_recent_trades(self, limit: int = 20) -> list[dict[str, Any]]:
        cursor = await self.db.execute(
            "SELECT * FROM trades ORDER BY id DESC LIMIT ?", (limit,)
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def get_trades_by_symbol(self, symbol: str, limit: int = 50) -> list[dict[str, Any]]:
        cursor = await self.db.execute(
            "SELECT * FROM trades WHERE symbol = ? ORDER BY id DESC LIMIT ?",
            (symbol, limit),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    # ── Balance snapshots ───────────────────────────────────

    async def insert_balance_snapshot(
        self,
        total_usdc: float,
        positions: dict[str, Any] | None = None,
        peak_balance: float = 0,
    ) -> int:
        cursor = await self.db.execute(
            """INSERT INTO balance_snapshots (total_usdc, positions, peak_balance)
               VALUES (?, ?, ?)""",
            (total_usdc, json.dumps(positions or {}), peak_balance),
        )
        await self.db.commit()
        return cursor.lastrowid  # type: ignore[return-value]

    async def get_latest_balance(self) -> dict[str, Any] | None:
        cursor = await self.db.execute(
            "SELECT * FROM balance_snapshots ORDER BY id DESC LIMIT 1"
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        result = dict(row)
        result["positions"] = json.loads(result["positions"])
        return result

    # ── Orders ──────────────────────────────────────────────

    async def upsert_order(
        self,
        order_id: str,
        symbol: str,
        side: str,
        order_type: str,
        quantity: float,
        price: float | None = None,
        status: str = "NEW",
        strategy: str | None = None,
        filled_price: float | None = None,
        filled_quantity: float | None = None,
    ) -> None:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        await self.db.execute(
            """INSERT INTO orders (order_id, symbol, side, order_type, price, quantity, status, strategy, filled_price, filled_quantity, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(order_id) DO UPDATE SET
                   status = excluded.status,
                   filled_price = excluded.filled_price,
                   filled_quantity = excluded.filled_quantity,
                   updated_at = excluded.updated_at""",
            (order_id, symbol, side, order_type, price, quantity, status, strategy, filled_price, filled_quantity, now),
        )
        await self.db.commit()

    async def get_open_orders_db(self, symbol: str | None = None) -> list[dict[str, Any]]:
        if symbol:
            cursor = await self.db.execute(
                "SELECT * FROM orders WHERE status IN ('NEW', 'PARTIALLY_FILLED') AND symbol = ? ORDER BY id",
                (symbol,),
            )
        else:
            cursor = await self.db.execute(
                "SELECT * FROM orders WHERE status IN ('NEW', 'PARTIALLY_FILLED') ORDER BY id"
            )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def update_order_status(self, order_id: str, status: str, **kwargs: Any) -> None:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        sets = ["status = ?", "updated_at = ?"]
        vals: list[Any] = [status, now]
        for key in ("filled_price", "filled_quantity"):
            if key in kwargs:
                sets.append(f"{key} = ?")
                vals.append(kwargs[key])
        vals.append(order_id)
        await self.db.execute(
            f"UPDATE orders SET {', '.join(sets)} WHERE order_id = ?", vals
        )
        await self.db.commit()

    # ── AI Decisions ────────────────────────────────────────

    async def insert_ai_decision(
        self,
        action: str,
        confidence: float,
        reasoning: str | None = None,
        symbol: str | None = None,
        snapshot_hash: str | None = None,
        raw_response: str | None = None,
        executed: bool = False,
        execution_result: str | None = None,
        tier: str | None = None,
        cost_usd: float = 0.0,
    ) -> int:
        cursor = await self.db.execute(
            """INSERT INTO ai_decisions (action, confidence, reasoning, symbol, snapshot_hash, raw_response, executed, execution_result, tier, cost_usd)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (action, confidence, reasoning, symbol, snapshot_hash, raw_response, int(executed), execution_result, tier, cost_usd),
        )
        await self.db.commit()
        return cursor.lastrowid  # type: ignore[return-value]

    async def get_recent_decisions(self, limit: int = 20) -> list[dict[str, Any]]:
        cursor = await self.db.execute(
            "SELECT * FROM ai_decisions ORDER BY id DESC LIMIT ?", (limit,)
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    # ── Coins ────────────────────────────────────────────────

    async def upsert_coin(
        self,
        symbol: str,
        sz_decimals: int | None = None,
        avg_volume_24h: float = 0,
    ) -> None:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        await self.db.execute(
            """INSERT INTO coins (symbol, first_seen, last_seen, is_active, sz_decimals, avg_volume_24h)
               VALUES (?, ?, ?, 1, ?, ?)
               ON CONFLICT(symbol) DO UPDATE SET
                   last_seen = excluded.last_seen,
                   is_active = 1,
                   sz_decimals = COALESCE(excluded.sz_decimals, coins.sz_decimals),
                   avg_volume_24h = excluded.avg_volume_24h""",
            (symbol, now, now, sz_decimals, avg_volume_24h),
        )
        await self.db.commit()

    # ── Events ───────────────────────────────────────────────

    async def insert_event(
        self,
        cycle: int,
        symbol: str,
        event_type: str,
        source: str,
        action: str | None = None,
        confidence: float | None = None,
        reasoning: str | None = None,
        details: dict[str, Any] | None = None,
        position_id: int | None = None,
        trade_id: int | None = None,
    ) -> int:
        cursor = await self.db.execute(
            """INSERT INTO events
               (cycle, symbol, event_type, source, action, confidence, reasoning, details, position_id, trade_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (cycle, symbol, event_type, source, action, confidence, reasoning,
             json.dumps(details or {}), position_id, trade_id),
        )
        await self.db.commit()
        return cursor.lastrowid  # type: ignore[return-value]

    async def get_events_by_symbol(self, symbol: str, limit: int = 100) -> list[dict[str, Any]]:
        cursor = await self.db.execute(
            "SELECT * FROM events WHERE symbol = ? ORDER BY id DESC LIMIT ?",
            (symbol, limit),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def get_events_by_cycle(self, cycle: int) -> list[dict[str, Any]]:
        cursor = await self.db.execute(
            "SELECT * FROM events WHERE cycle = ? ORDER BY id",
            (cycle,),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    # ── Cycle Summaries ──────────────────────────────────────

    async def insert_cycle_summary(
        self,
        cycle: int,
        duration_sec: float | None = None,
        balance_usdc: float | None = None,
        drawdown_pct: float | None = None,
        daily_drawdown_pct: float | None = None,
        open_positions: int | None = None,
        capital_utilization: float | None = None,
        coins_monitored: int | None = None,
        signals_generated: int = 0,
        decisions_approved: int = 0,
        decisions_blocked: int = 0,
        trades_executed: int = 0,
        kill_switch: bool = False,
        daily_paused: bool = False,
    ) -> int:
        cursor = await self.db.execute(
            """INSERT INTO cycle_summaries
               (cycle, duration_sec, balance_usdc, drawdown_pct, daily_drawdown_pct,
                open_positions, capital_utilization, coins_monitored,
                signals_generated, decisions_approved, decisions_blocked,
                trades_executed, kill_switch, daily_paused)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (cycle, duration_sec, balance_usdc, drawdown_pct, daily_drawdown_pct,
             open_positions, capital_utilization, coins_monitored,
             signals_generated, decisions_approved, decisions_blocked,
             trades_executed, int(kill_switch), int(daily_paused)),
        )
        await self.db.commit()
        return cursor.lastrowid  # type: ignore[return-value]

    # ── Stats helpers ───────────────────────────────────────

    # ── Deferred Opportunities ────────────────────────────────

    async def upsert_deferred(
        self,
        symbol: str,
        action: str,
        cycle: int,
        conditions: dict[str, Any] | None = None,
        type_: str = "opportunity",
        strategy_type: str = "unknown",
        confidence: float = 0.0,
    ) -> None:
        await self.db.execute(
            """INSERT INTO deferred_opportunities (symbol, original_action, deferred_at_cycle, conditions, type, strategy_type, confidence)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(symbol, strategy_type, type) DO UPDATE SET
                   original_action = excluded.original_action,
                   deferred_at_cycle = excluded.deferred_at_cycle,
                   conditions = excluded.conditions,
                   confidence = excluded.confidence,
                   created_at = strftime('%Y-%m-%dT%H:%M:%SZ','now')""",
            (symbol, action, cycle, json.dumps(conditions or {}), type_, strategy_type, confidence),
        )
        await self.db.commit()

    async def delete_deferred(self, symbol: str, type_: str = "opportunity", strategy_type: str | None = None) -> None:
        if strategy_type is not None:
            await self.db.execute(
                "DELETE FROM deferred_opportunities WHERE symbol = ? AND type = ? AND strategy_type = ?",
                (symbol, type_, strategy_type),
            )
        else:
            await self.db.execute(
                "DELETE FROM deferred_opportunities WHERE symbol = ? AND type = ?",
                (symbol, type_),
            )
        await self.db.commit()

    async def get_all_deferred(self, type_: str | None = None) -> list[dict[str, Any]]:
        if type_:
            cursor = await self.db.execute(
                "SELECT * FROM deferred_opportunities WHERE type = ? ORDER BY id",
                (type_,),
            )
        else:
            cursor = await self.db.execute(
                "SELECT * FROM deferred_opportunities ORDER BY id"
            )
        rows = await cursor.fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["conditions"] = json.loads(d["conditions"])
            result.append(d)
        return result

    # ── Bot State (key-value) ─────────────────────────────────

    async def set_state(self, key: str, value: str) -> None:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        await self.db.execute(
            """INSERT INTO bot_state (key, value, updated_at)
               VALUES (?, ?, ?)
               ON CONFLICT(key) DO UPDATE SET
                   value = excluded.value,
                   updated_at = excluded.updated_at""",
            (key, value, now),
        )
        await self.db.commit()

    async def get_state(self, key: str) -> str | None:
        cursor = await self.db.execute(
            "SELECT value FROM bot_state WHERE key = ?", (key,)
        )
        row = await cursor.fetchone()
        return row["value"] if row else None

    async def delete_state(self, key: str) -> None:
        await self.db.execute("DELETE FROM bot_state WHERE key = ?", (key,))
        await self.db.commit()

    # ── Ask Close ──────────────────────────────────────────

    async def set_ask_close(self, position_id: int) -> bool:
        """Mark an open position for manual close. Returns True if updated."""
        cursor = await self.db.execute(
            "UPDATE positions SET ask_close = 1 WHERE id = ? AND status = 'OPEN'",
            (position_id,),
        )
        await self.db.commit()
        return cursor.rowcount > 0

    async def get_ask_close_positions(self) -> list[dict[str, Any]]:
        """Get open positions flagged for manual close."""
        cursor = await self.db.execute(
            "SELECT * FROM positions WHERE status = 'OPEN' AND ask_close = 1 ORDER BY id"
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    # ── Bot Config ─────────────────────────────────────────

    async def get_config(self) -> dict[str, Any]:
        """Get the single bot_config row as a dict."""
        cursor = await self.db.execute("SELECT * FROM bot_config WHERE id = 1")
        row = await cursor.fetchone()
        if row is None:
            # Seed default row
            await self.db.execute("INSERT OR IGNORE INTO bot_config (id) VALUES (1)")
            await self.db.commit()
            cursor = await self.db.execute("SELECT * FROM bot_config WHERE id = 1")
            row = await cursor.fetchone()
        result = dict(row)
        result.pop("id", None)
        return result

    async def update_config(
        self,
        changes: dict[str, Any],
        source: str = "dashboard",
    ) -> dict[str, str]:
        """Update config fields and log changes to config_history.
        Returns dict of {field: old_value} for fields that actually changed.
        """
        current = await self.get_config()
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        changed: dict[str, str] = {}

        for field, new_val in changes.items():
            if field in ("id", "updated_at"):
                continue
            if field not in current:
                continue
            old_val = current[field]
            old_str = str(old_val) if old_val is not None else ""
            new_str = str(new_val)
            if old_str != new_str:
                changed[field] = old_str
                await self.db.execute(
                    "INSERT INTO config_history (timestamp, field_name, old_value, new_value, source) VALUES (?, ?, ?, ?, ?)",
                    (now, field, old_str, new_str, source),
                )

        if changed:
            sets = ", ".join(f"{f} = ?" for f in changes if f in current and f not in ("id", "updated_at"))
            vals = [changes[f] for f in changes if f in current and f not in ("id", "updated_at")]
            vals.append(now)
            await self.db.execute(
                f"UPDATE bot_config SET {sets}, updated_at = ? WHERE id = 1",
                vals,
            )
            await self.db.commit()

        return changed

    async def get_config_history(
        self,
        limit: int = 100,
        offset: int = 0,
        field_name: str | None = None,
    ) -> list[dict[str, Any]]:
        """Get config change history, newest first."""
        if field_name:
            cursor = await self.db.execute(
                "SELECT * FROM config_history WHERE field_name = ? ORDER BY id DESC LIMIT ? OFFSET ?",
                (field_name, limit, offset),
            )
        else:
            cursor = await self.db.execute(
                "SELECT * FROM config_history ORDER BY id DESC LIMIT ? OFFSET ?",
                (limit, offset),
            )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def get_config_history_count(self, field_name: str | None = None) -> int:
        if field_name:
            cursor = await self.db.execute(
                "SELECT COUNT(*) as cnt FROM config_history WHERE field_name = ?",
                (field_name,),
            )
        else:
            cursor = await self.db.execute("SELECT COUNT(*) as cnt FROM config_history")
        row = await cursor.fetchone()
        return row["cnt"]

    # ── Stats helpers ───────────────────────────────────────

    async def get_trade_stats(self) -> dict[str, Any]:
        """Basic win/loss stats for Kelly criterion and risk management."""
        cursor = await self.db.execute(
            "SELECT pnl FROM trades WHERE pnl IS NOT NULL ORDER BY id DESC LIMIT 100"
        )
        rows = await cursor.fetchall()
        pnls = [r["pnl"] for r in rows]
        if not pnls:
            return {"total_trades": 0, "win_rate": 0.0, "avg_win": 0.0, "avg_loss": 0.0}

        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        return {
            "total_trades": len(pnls),
            "win_rate": len(wins) / len(pnls) if pnls else 0.0,
            "avg_win": sum(wins) / len(wins) if wins else 0.0,
            "avg_loss": sum(losses) / len(losses) if losses else 0.0,
            "consecutive_losses": _count_consecutive_losses(pnls),
        }


def _count_consecutive_losses(pnls: list[float]) -> int:
    """Count consecutive losses from most recent trade."""
    count = 0
    for pnl in pnls:
        if pnl <= 0:
            count += 1
        else:
            break
    return count
