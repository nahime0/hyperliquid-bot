# Testing

## Overview

Il progetto usa **pytest** + **pytest-asyncio** per test unitari e di integrazione. Tutti i test async usano database SQLite in-memory (nessuna dipendenza esterna tranne `claude` CLI per i test di integrazione).

## Struttura

```
tests/
├── conftest.py                  # Fixtures condivise: settings, DB, positions, decisions
├── test_types.py                # Decision dataclass
├── test_settings.py             # AIConfig, RiskConfig, Settings, load_settings
├── test_ai_advisor.py           # AIAdvisor: deferred tracking + CLI (mocked e reale)
├── test_position_tracker.py     # PositionTracker: lifecycle, SL/TP, trailing, AI adjustments
├── test_risk_manager.py         # RiskManager: validation, kill switch, daily pause
└── test_integration.py          # Test con chiamata reale al CLI claude
```

## Esecuzione

```bash
# Solo test unitari (veloci, nessuna dipendenza esterna)
.venv/bin/python -m pytest tests/ -m "not integration" -v

# Solo test di integrazione (richiedono `claude` CLI)
.venv/bin/python -m pytest tests/ -m integration -v

# Tutti i test
.venv/bin/python -m pytest tests/ -v
```

## Fixtures condivise (`conftest.py`)

| Fixture | Tipo | Descrizione |
|---|---|---|
| `risk_config` | `RiskConfig` | Configurazione risk con valori di default |
| `ai_config` | `AIConfig` | Configurazione AI con valori di default |
| `settings` | `Settings` | Settings completo con default di test |
| `db` | `Database` | SQLite in-memory, auto-connect/close |
| `position_tracker` | `PositionTracker` | Tracker con DB in-memory e risk_config |
| `sample_positions` | `list[dict]` | Posizioni aperte simulate (ETH LONG + BTC SHORT) |
| `sample_opportunities` | `list[dict]` | Opportunita' di entry simulate |
| `sample_account` | `dict` | Account mock con balance e equity |
| `sample_decision_buy` | `Decision` | Decisione BUY SOL con SL/TP |
| `sample_decision_short` | `Decision` | Decisione SHORT DOGE con SL/TP |
| `sample_decision_close` | `Decision` | Decisione CLOSE ETH |

## Test per modulo

### `test_types.py` — Decision dataclass (7 test)

Verifica il dataclass `Decision` in `core/types.py`:

- Campi obbligatori (`action`, `confidence`, `reasoning`) impostati correttamente
- Campi opzionali (`symbol`, `size_pct`, `stop_loss`, etc.) default a `None`
- Mutabilita': SL/TP modificabili dopo creazione (non frozen)
- Tutte le azioni valide: BUY, SHORT, SELL, HOLD, CLOSE
- Creazione con tutti i campi popolati
- Limiti confidence (0.0 e 1.0)

### `test_settings.py` — Configurazione (7 test)

Verifica i dataclass di configurazione in `config/settings.py`:

- **AIConfig**: valori di default corretti (`model="opus"`, `timeout=120`, `decision_interval=60`)
- **AIConfig**: non ha piu' campi legacy (`haiku_model`, `opus_model`, `anthropic_api_key`)
- **AIConfig**: frozen (non modificabile dopo creazione)
- **RiskConfig**: tutti i default verificati (SL 1%, TP 1.5%, max drawdown 15%, etc.)
- **RiskConfig**: frozen
- **Settings**: frozen
- **load_settings()**: carica correttamente da environment, ritorna `Settings` valido

### `test_ai_advisor.py` — AI Advisor (24 test)

#### Deferred opportunity tracking (13 test, sync, no mock)

Verifica il meccanismo di opportunita' differite in `core/ai_advisor.py`:

- `defer()` aggiunge simbolo alla lista deferred
- `defer()` sovrascrive condizioni se stesso simbolo
- `remove_deferred()` rimuove correttamente
- `remove_deferred()` su simbolo inesistente non solleva errori
- `deferred_symbols` ritorna set completo
- `check_deferred()` con `wait_cycles` non pronto (cicli insufficienti)
- `check_deferred()` con `wait_cycles` pronto (cicli raggiunti, rimosso automaticamente)
- `check_deferred()` con `wait_until_price_above` non pronto
- `check_deferred()` con `wait_until_price_above` pronto
- `check_deferred()` con `wait_until_price_below` pronto
- `check_deferred()` senza condizioni: pronto immediatamente
- `check_deferred()` con simboli multipli: solo quelli pronti ritornati
- `set_cycle()` aggiorna contatore interno

#### CLI invocation mocked (11 test, async)

Verifica la chiamata CLI con subprocess mockato:

- Output JSON strutturato parsato correttamente (`structured_output`)
- Timeout gestito: ritorna liste vuote
- Errore CLI (returncode != 0): ritorna liste vuote
- Output vuoto: ritorna liste vuote
- JSON invalido: ritorna liste vuote
- Chiamata con posizioni e opportunita' vuote
- `recent_trades` incluso nel payload
- Parsing campo `structured_output` (wrapper `claude --output-format json`)
- Parsing campo `result` (dict diretto)
- Parsing `result` come JSON string
- Parsing dict raw (senza wrapper)

### `test_position_tracker.py` — Position Tracker (30 test)

#### Lifecycle (9 test)

- Apertura posizione LONG: ritorna ID, dati corretti in DB
- Apertura posizione SHORT: direzione salvata
- Apertura duplicata: solleva `ValueError`
- Chiusura LONG con profitto: PnL positivo
- Chiusura LONG con perdita: PnL negativo
- Chiusura SHORT con profitto: PnL positivo (prezzo scende)
- Chiusura SHORT con perdita: PnL negativo (prezzo sale)
- Fee incluse: PnL netto negativo anche a break-even (fee taker 0.045%/leg)
- Chiusura posizione inesistente: ritorna 0.0

#### Query (5 test)

- `get_open_positions()`: ritorna solo posizioni OPEN
- `get_position_for_symbol()`: ritorna posizione specifica
- `get_position_for_symbol()`: ritorna `None` se non esiste
- `get_open_symbols()`: ritorna set di simboli con posizioni aperte
- Posizione chiusa non appare in `get_open_positions()`

#### SL/TP checking (6 test)

Verifica `check_sl_tp()` direction-aware:

- LONG SL hit: prezzo scende sotto stop loss
- LONG TP hit: prezzo sale sopra take profit
- SHORT SL hit: prezzo sale sopra stop loss
- SHORT TP hit: prezzo scende sotto take profit
- Nessun trigger: prezzo nel range
- Prezzo mancante: posizione skippata

#### Trailing stop (6 test)

Verifica trailing stop direction-aware:

- LONG breakeven: gain >= 1.0% → SL sale a entry
- LONG trailing: gain >= 1.5% → SL segue a max - 1.0%
- LONG tight: gain >= 2.5% → SL stringe a max - 0.75%
- SHORT breakeven: gain >= 1.0% → SL scende a entry
- LONG SL non scende mai (solo su)
- SHORT SL non sale mai (solo giu')

#### AI adjustments (4 test)

- `update_sl_tp()`: aggiorna entrambi SL e TP
- `update_sl_tp()`: aggiorna solo SL, TP invariato
- `update_sl_tp()`: senza argomenti, no-op
- `update_leverage()`: aggiorna leverage in DB

### `test_risk_manager.py` — Risk Manager (16 test)

Usa `MockClient` (nessuna chiamata reale a Hyperliquid).

#### Validation (10 test)

- HOLD sempre approvato (pass-through)
- BUY approvato: confidence sufficiente, size calcolato
- Kill switch blocca tutto
- Daily pause blocca BUY/SHORT, permette CLOSE
- Max posizioni raggiunto: blocca nuove entry
- Balance sotto minimo: blocca
- Confidence troppo bassa (< 0.5): blocca
- Posizione duplicata sullo stesso simbolo: blocca
- Auto SL/TP per LONG: SL sotto prezzo, TP sopra
- Auto SL/TP per SHORT: SL sopra prezzo, TP sotto

#### Kill switch e daily pause (5 test)

- Drawdown 20% (> 15% max) → kill switch attivato
- Balance 30 USDC (< 50 min) → kill switch attivato
- Drawdown 10% (< 15%) → kill switch NON attivato
- 5 perdite consecutive → kill switch attivato
- Drawdown giornaliero 6% (> 5%) → daily pause attivato

#### Risk metrics (1 test)

- `get_risk_metrics()` ritorna tutte le chiavi attese con valori corretti

### `test_integration.py` — Integrazione (5 test)

Marcati `@pytest.mark.integration` — richiedono `claude` CLI installato.

- **Position review**: invia 1 posizione aperta → AI ritorna HOLD/CLOSE/ADJUST
- **Opportunity review**: invia 1 opportunita' → AI ritorna BUY/SHORT/HOLD
- **Mixed payload**: 1 posizione + 1 opportunita' → entrambi reviewed
- **Schema validation**: risposta conforme alla struttura JSON schema
- **Defer roundtrip**: defer con condizioni → `check_deferred()` ritorna pronto dopo condizioni soddisfatte

## Mock pattern

### MockClient (per RiskManager)

```python
class MockClient:
    async def get_account_balance(self) -> float: return 1000.0
    async def get_open_positions(self) -> list: return []
    async def get_price(self, coin: str) -> float: return 2000.0
    async def update_leverage(self, coin, lev, is_cross=True): pass
```

### Database in-memory

```python
@pytest_asyncio.fixture
async def db():
    d = Database(":memory:")
    await d.connect()
    yield d
    await d.close()
```

### Subprocess mock (per AIAdvisor CLI)

```python
proc = AsyncMock()
proc.communicate = AsyncMock(return_value=(stdout.encode(), stderr.encode()))
proc.returncode = 0

with patch("asyncio.create_subprocess_exec", return_value=proc):
    result = await advisor.consult(...)
```

## Marker personalizzato

```python
# conftest.py
def pytest_configure(config):
    config.addinivalue_line(
        "markers", "integration: tests that call external services (claude CLI)"
    )
```

Permette di filtrare con `-m integration` o `-m "not integration"`.
