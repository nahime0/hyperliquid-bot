# AI Decision Engine

## Overview

Il motore AI opera in **modalita' review**: le decisioni vengono generate autonomamente dalle regole codificate (Mean Reversion), e l'AI interviene come **reviewer opzionale** con potere di veto. Questo approccio ibrido bilancia velocita' (regole) e intelligenza (AI).

Il flag `--no-ai` disabilita completamente l'AI, lasciando il bot in modalita' pure rule-based.

## Architettura 3-Tier

### Tier 1: PreScreen (`core/ai_engine/prescreen.py`)

**Costo: $0 | Latenza: 0ms**

Regole deterministiche che filtrano candidati prima dell'AI:
- Rate limit (evita chiamate troppo frequenti)
- Snapshot hash identico (nessun cambiamento)
- Balance insufficiente
- Max posizioni raggiunte
- 5 perdite consecutive

### Tier 2: Haiku Screening (`core/ai_engine/engine.py`)

**Costo: ~$0.001/call | Latenza: ~2s**

Modello: `claude-haiku-4-5-20251001` (configurabile)

Riceve uno snapshot condensato del mercato e decide se l'opportunita' merita escalation:
- **HOLD** → veto (risparmia costi Tier 3)
- **BUY/SHORT con confidence < 0.7** → HOLD forzato
- **BUY/SHORT con confidence >= 0.7** → escalation a Tier 3

Max 3 escalation per ciclo (per controllare i costi).

### Tier 3: Sonnet Analysis (`core/ai_engine/engine.py`)

**Costo: ~$0.01-0.03/call | Latenza: ~10-15s**

Modello: `claude-sonnet-4-5-20250929` (configurabile)

Riceve il contesto completo:
- Market snapshot con indicatori per ogni coin
- Posizioni aperte con PnL
- Storico ultimi 20 trade
- Trade stats (win rate, profit factor)
- Risk metrics (drawdown, kill switch status)

Produce una decisione strutturata con:
- Action (BUY/SHORT/CLOSE/HOLD)
- Symbol, confidence, reasoning
- Stop loss, take profit suggeriti
- Size suggerita

## Review Mode

Quando l'AI e' abilitata, opera in **review mode**:

1. La strategia genera candidate decisions (BUY, SHORT, CLOSE)
2. L'AI Engine riceve le candidate + market snapshot
3. L'AI puo':
   - **Confermare** la decisione (pass-through)
   - **Modificare** parametri (SL, TP, size)
   - **Vetare** la decisione (convertirla in HOLD)
4. Le decisioni passano poi al Risk Manager

## Backend Pluggabili

L'engine supporta backend multipli per screening e analisi:

| Backend | Descrizione |
|---|---|
| `anthropic_sdk` | SDK Anthropic diretto (default) |
| `claude_cli` | Claude Code CLI (`claude -p`) |
| `gemini_cli` | Gemini CLI |
| `codex_cli` | Codex CLI |

Configurazione:
```env
AI_SCREENING_BACKEND=anthropic_sdk
AI_SCREENING_MODEL=claude-haiku-4-5-20251001
AI_ANALYSIS_BACKEND=anthropic_sdk
AI_ANALYSIS_MODEL=claude-sonnet-4-5-20250929
```

## Schema JSON

### Decisione singola (`schemas/decision.json`)

```json
{
  "action": "BUY | SHORT | SELL | HOLD | CLOSE",
  "symbol": "ETH",
  "size_pct": 5.0,
  "order_type": "MARKET",
  "limit_price": 2000.50,
  "stop_loss": 1980.00,
  "take_profit": 2030.75,
  "confidence": 0.85,
  "reasoning": "Spiegazione dettagliata...",
  "strategy_type": "mean_reversion | momentum | other"
}
```

Campi required: `action`, `reasoning`, `confidence`.

### Screening (`schemas/screening_decision.json`)

Schema semplificato per il Tier 2 (Haiku).

### Review (`schemas/review_decision.json`)

Schema per la modalita' review.

## Prompt Templates

### `prompts/trading_decision.md` (Tier 3)

Template completo per analisi profonda. Include:
1. Ruolo del trader quantitativo
2. Regole di risk management
3. Market snapshot completo
4. Storico trade recenti
5. Istruzioni per output strutturato

### `prompts/screening_decision.md` (Tier 2)

Template condensato per screening rapido. Focalizzato su:
- Identificazione rapida di opportunita'
- Filtro false signals
- Decisione escala/non-escala

### `prompts/review_decision.md` (Review)

Template per review di decisioni generate dalle regole.

## Decision Dataclass

```python
@dataclass
class Decision:
    action: str           # BUY, SHORT, SELL, HOLD, CLOSE
    confidence: float     # 0-1
    reasoning: str
    symbol: str | None
    size_pct: float | None
    order_type: str | None  # LIMIT, MARKET
    limit_price: float | None
    stop_loss: float | None
    take_profit: float | None
    strategy_type: str | None
    raw_response: str | None
    tier: Tier            # PRESCREEN, HAIKU, OPUS, FALLBACK
```

## Tier Enum

```python
class Tier(enum.Enum):
    PRESCREEN = "prescreen"
    HAIKU = "haiku"
    OPUS = "opus"
    FALLBACK = "fallback"
```

## Costi e ottimizzazione

| Strategia | Effetto |
|---|---|
| PreScreen (Tier 1) | Filtra ~70% dei cicli gratuitamente |
| Haiku (Tier 2) | Filtra ~90% dei candidati a ~$0.001 |
| Max escalation limit | Max 3 escalation a Tier 3 per ciclo |
| Snapshot hash dedup | Evita ri-analisi di snapshot identici |
| Rate limit | Impone intervallo minimo tra chiamate |

Stima costi giornalieri con intervallo 60s (1.440 cicli/giorno):
- Mercato calmo: $0.50-$1.00
- Mercato attivo: $2.00-$5.00
- Max teorico: $15-$40

Ogni decisione viene salvata nel DB con `cost_usd` per tracking.
