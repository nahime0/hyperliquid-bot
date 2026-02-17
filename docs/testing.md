# Testing

## Overview

The project uses **pytest** + **pytest-asyncio** for unit and integration tests. All async tests use in-memory SQLite databases (no external dependencies except `claude` CLI for integration tests).

## Structure

```
tests/
├── conftest.py                  # Shared fixtures: settings, DB, positions, decisions
├── test_types.py                # Decision dataclass
├── test_settings.py             # AIConfig, RiskConfig, Settings, load_settings
├── test_ai_advisor.py           # AIAdvisor: deferred tracking + CLI (mocked and real)
├── test_position_tracker.py     # PositionTracker: lifecycle, SL/TP, trailing, AI adjustments
├── test_risk_manager.py         # RiskManager: validation, kill switch, daily pause
└── test_integration.py          # Tests with real claude CLI calls
```

## Running

```bash
# Unit tests only (fast, no external dependencies)
.venv/bin/python -m pytest tests/ -m "not integration" -v

# Integration tests only (require `claude` CLI)
.venv/bin/python -m pytest tests/ -m integration -v

# All tests
.venv/bin/python -m pytest tests/ -v
```

## Shared Fixtures (`conftest.py`)

| Fixture | Type | Description |
|---|---|---|
| `risk_config` | `RiskConfig` | Risk configuration with default values |
| `ai_config` | `AIConfig` | AI configuration with default values |
| `settings` | `Settings` | Complete settings with test defaults |
| `db` | `Database` | In-memory SQLite, auto-connect/close |
| `position_tracker` | `PositionTracker` | Tracker with in-memory DB and risk_config |
| `sample_positions` | `list[dict]` | Simulated open positions (ETH LONG + BTC SHORT) |
| `sample_opportunities` | `list[dict]` | Simulated entry opportunities |
| `sample_account` | `dict` | Mock account with balance and equity |
| `sample_decision_buy` | `Decision` | BUY SOL decision with SL/TP |
| `sample_decision_short` | `Decision` | SHORT DOGE decision with SL/TP |
| `sample_decision_close` | `Decision` | CLOSE ETH decision |

## Tests by Module

### `test_types.py` — Decision dataclass (7 tests)

Verifies the `Decision` dataclass in `core/types.py`:

- Required fields (`action`, `confidence`, `reasoning`) set correctly
- Optional fields (`symbol`, `size_pct`, `stop_loss`, etc.) default to `None`
- Mutability: SL/TP modifiable after creation (not frozen)
- All valid actions: BUY, SHORT, SELL, HOLD, CLOSE
- Creation with all fields populated
- Confidence limits (0.0 and 1.0)

### `test_settings.py` — Configuration (7 tests)

Verifies the configuration dataclasses in `config/settings.py`:

- **AIConfig**: correct default values (`model="opus"`, `timeout=120`, `decision_interval=60`)
- **AIConfig**: no longer has legacy fields (`haiku_model`, `opus_model`, `anthropic_api_key`)
- **AIConfig**: frozen (immutable after creation)
- **RiskConfig**: all defaults verified (SL 1%, TP 1.5%, max drawdown 15%, etc.)
- **RiskConfig**: frozen
- **Settings**: frozen
- **load_settings()**: correctly loads from environment, returns valid `Settings`

### `test_ai_advisor.py` — AI Advisor (24 tests)

#### Deferred opportunity tracking (13 tests, sync, no mock)

Verifies the deferred opportunity mechanism in `core/ai_advisor.py`:

- `defer()` adds symbol to the deferred list
- `defer()` overwrites conditions if same symbol
- `remove_deferred()` removes correctly
- `remove_deferred()` on non-existent symbol doesn't raise errors
- `deferred_symbols` returns complete set
- `check_deferred()` with `wait_cycles` not ready (insufficient cycles)
- `check_deferred()` with `wait_cycles` ready (cycles reached, automatically removed)
- `check_deferred()` with `wait_until_price_above` not ready
- `check_deferred()` with `wait_until_price_above` ready
- `check_deferred()` with `wait_until_price_below` ready
- `check_deferred()` without conditions: ready immediately
- `check_deferred()` with multiple symbols: only ready ones returned
- `set_cycle()` updates internal counter

#### CLI invocation mocked (11 tests, async)

Verifies CLI call with mocked subprocess:

- Structured JSON output parsed correctly (`structured_output`)
- Timeout handled: returns empty lists
- CLI error (returncode != 0): returns empty lists
- Empty output: returns empty lists
- Invalid JSON: returns empty lists
- Call with empty positions and opportunities
- `recent_trades` included in payload
- Parsing `structured_output` field (`claude --output-format json` wrapper)
- Parsing `result` field (direct dict)
- Parsing `result` as JSON string
- Parsing raw dict (without wrapper)

### `test_position_tracker.py` — Position Tracker (30 tests)

#### Lifecycle (9 tests)

- Open LONG position: returns ID, correct data in DB
- Open SHORT position: direction saved
- Duplicate open: raises `ValueError`
- Close LONG with profit: positive PnL
- Close LONG with loss: negative PnL
- Close SHORT with profit: positive PnL (price drops)
- Close SHORT with loss: negative PnL (price rises)
- Fees included: net PnL negative even at break-even (taker fee 0.045%/leg)
- Close non-existent position: returns 0.0

#### Query (5 tests)

- `get_open_positions()`: returns only OPEN positions
- `get_position_for_symbol()`: returns specific position
- `get_position_for_symbol()`: returns `None` if not found
- `get_open_symbols()`: returns set of symbols with open positions
- Closed position doesn't appear in `get_open_positions()`

#### SL/TP checking (6 tests)

Verifies direction-aware `check_sl_tp()`:

- LONG SL hit: price drops below stop loss
- LONG TP hit: price rises above take profit
- SHORT SL hit: price rises above stop loss
- SHORT TP hit: price drops below take profit
- No trigger: price within range
- Missing price: position skipped

#### Trailing stop (6 tests)

Verifies direction-aware trailing stop:

- LONG breakeven: gain >= 1.0% -> SL moves to entry
- LONG trailing: gain >= 1.5% -> SL follows at max - 1.0%
- LONG tight: gain >= 2.5% -> SL tightens to max - 0.75%
- SHORT breakeven: gain >= 1.0% -> SL moves down to entry
- LONG SL never falls (only rises)
- SHORT SL never rises (only falls)

#### AI adjustments (4 tests)

- `update_sl_tp()`: updates both SL and TP
- `update_sl_tp()`: updates only SL, TP unchanged
- `update_sl_tp()`: without arguments, no-op
- `update_leverage()`: updates leverage in DB

### `test_risk_manager.py` — Risk Manager (16 tests)

Uses `MockClient` (no real Hyperliquid calls).

#### Validation (10 tests)

- HOLD always approved (pass-through)
- BUY approved: sufficient confidence, size calculated
- Kill switch blocks everything
- Daily pause blocks BUY/SHORT, allows CLOSE
- Max positions reached: blocks new entries
- Balance below minimum: blocks
- Confidence too low (< 0.5): blocks
- Duplicate position on same symbol: blocks
- Auto SL/TP for LONG: SL below price, TP above
- Auto SL/TP for SHORT: SL above price, TP below

#### Kill switch and daily pause (5 tests)

- 20% drawdown (> 15% max) -> kill switch activated
- 30 USDC balance (< 50 min) -> kill switch activated
- 10% drawdown (< 15%) -> kill switch NOT activated
- 5 consecutive losses -> kill switch activated
- 6% daily drawdown (> 5%) -> daily pause activated

#### Risk metrics (1 test)

- `get_risk_metrics()` returns all expected keys with correct values

### `test_integration.py` — Integration (5 tests)

Marked `@pytest.mark.integration` — require `claude` CLI installed.

- **Position review**: sends 1 open position -> AI returns HOLD/CLOSE/ADJUST
- **Opportunity review**: sends 1 opportunity -> AI returns BUY/SHORT/HOLD
- **Mixed payload**: 1 position + 1 opportunity -> both reviewed
- **Schema validation**: response conforms to JSON schema structure
- **Defer roundtrip**: defer with conditions -> `check_deferred()` returns ready after conditions met

## Mock Pattern

### MockClient (for RiskManager)

```python
class MockClient:
    async def get_account_balance(self) -> float: return 1000.0
    async def get_open_positions(self) -> list: return []
    async def get_price(self, coin: str) -> float: return 2000.0
    async def update_leverage(self, coin, lev, is_cross=True): pass
```

### In-memory Database

```python
@pytest_asyncio.fixture
async def db():
    d = Database(":memory:")
    await d.connect()
    yield d
    await d.close()
```

### Subprocess mock (for AIAdvisor CLI)

```python
proc = AsyncMock()
proc.communicate = AsyncMock(return_value=(stdout.encode(), stderr.encode()))
proc.returncode = 0

with patch("asyncio.create_subprocess_exec", return_value=proc):
    result = await advisor.consult(...)
```

## Custom Marker

```python
# conftest.py
def pytest_configure(config):
    config.addinivalue_line(
        "markers", "integration: tests that call external services (claude CLI)"
    )
```

Allows filtering with `-m integration` or `-m "not integration"`.
