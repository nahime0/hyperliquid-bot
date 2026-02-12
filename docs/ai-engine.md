# AI Advisor (Claude Code CLI)

## Overview

Il bot usa un **AI advisor opzionale** basato su Claude Code CLI. Le decisioni vengono generate autonomamente dalle strategie (Mean Reversion, RSI Divergence), e l'AI interviene come **advisor** con capacita' di:

- **Approvare** opportunita' di trading (con possibili aggiustamenti)
- **Deferire** opportunita' (aspettare N cicli o un certo prezzo)
- **Chiudere** posizioni aperte
- **Aggiustare** SL/TP/leverage su posizioni aperte

Il flag `--no-ai` disabilita completamente l'AI, lasciando il bot in modalita' pure rule-based.

## Architettura

**Una singola chiamata CLI per ciclo** — semplice e affidabile.

```
Strategie generano candidates
        |
        v
Check deferred (condizioni soddisfatte?)
        |
        v
Build JSON payload (positions + opportunities + account + history)
        |
        v
claude -p <payload> --json-schema <schema> --output-format json --model opus
        |
        v
Parse structured_output
        |
        v
Process: CLOSE/ADJUST positions, approve/defer opportunities
        |
        v
Risk validate → Execute
```

Se non ci sono posizioni aperte E nessuna opportunita', la chiamata AI viene saltata.
Su errore o timeout: log warning, il bot continua autonomamente.

## Invocazione CLI

```bash
claude -p "<json_payload>" \
    --no-session-persistence \
    --model opus \
    --output-format json \
    --json-schema '<schema>' \
    --system-prompt-file prompts/ai_advisor.md \
    --allowedTools "" \
    --max-turns 1
```

- `--json-schema` enforce la struttura dell'output
- `--allowedTools ""` disabilita tool (pura inferenza, nessun file read)
- `--max-turns 1` previene loop agentici
- Timeout: 120s (configurabile via `AI_TIMEOUT`)

## Input JSON

```json
{
  "positions": [
    {
      "symbol": "ETH", "direction": "SHORT",
      "entry_price": 1976.65, "current_price": 1980.00,
      "pnl_pct": -0.17, "unrealized_pnl": -4.55,
      "stop_loss": 1996.42, "take_profit": 1947.00,
      "age_minutes": 120, "strategy": "mean_reversion",
      "leverage": 2, "quantity": 1.36,
      "indicators": { "rsi_15m": 45.2, "rsi_1h": 52.1, "trend": "BEARISH", "bb_upper": 2010, "bb_lower": 1950, "volume_ratio": 1.3 }
    }
  ],
  "opportunities": [
    {
      "symbol": "BTC", "proposed_action": "SHORT",
      "confidence": 0.65, "strategy": "mean_reversion",
      "reasoning": "RSI(14)=78 overbought, price at upper BB, bearish trend",
      "current_price": 45000, "proposed_stop_loss": 45450,
      "proposed_take_profit": 44325, "proposed_size_pct": 8.0,
      "indicators": { "rsi_15m": 78.3, "rsi_1h": 65.0, "trend": "BEARISH" }
    }
  ],
  "account": {
    "balance_usdc": 1000, "daily_pnl_pct": -0.5,
    "total_pnl": 50, "win_rate": 0.65,
    "open_position_count": 1, "max_positions": 5,
    "consecutive_losses": 0
  },
  "recent_trades": [ ... ],
  "trade_stats": { "total_trades": 42, "win_rate": 0.62, "avg_win": 8.5, "avg_loss": -5.2 }
}
```

## Output JSON (enforced by `--json-schema`)

```json
{
  "positions": [
    {
      "symbol": "ETH",
      "action": "HOLD",
      "reasoning": "Short in profit territory, trailing stop will manage exit"
    }
  ],
  "opportunities": [
    {
      "symbol": "BTC",
      "action": "SHORT",
      "adjustments": { "stop_loss": 45200, "size_pct": 6.0 },
      "reasoning": "Good setup but reducing size due to existing ETH short exposure"
    }
  ]
}
```

### Azioni per posizioni

| Azione | Effetto |
|---|---|
| `HOLD` | Nessuna azione (trailing stop/time stop continuano) |
| `CLOSE` | Chiusura immediata della posizione |
| `ADJUST` | Modifica SL, TP, o leverage tramite `adjustments` |

### Azioni per opportunita'

| Azione | Effetto |
|---|---|
| `BUY`/`SHORT` | Approva l'entry (con eventuali `adjustments`) |
| `HOLD` | Deferisce l'entry. Usa `defer` per condizioni |

### Condizioni di defer

```json
{
  "defer": {
    "wait_cycles": 5,
    "wait_until_price_above": 45500,
    "wait_until_price_below": 44000
  }
}
```

- `wait_cycles`: rievaluta dopo N cicli (~60s ciascuno)
- `wait_until_price_above/below`: rievaluta quando il prezzo raggiunge il livello

Le opportunita' deferred sono in-memory (cancellate al restart).

## System Prompt

Il file `prompts/ai_advisor.md` contiene le istruzioni per l'AI advisor, tra cui:

- Ruolo e responsabilita'
- Linee guida per decisioni su posizioni e opportunita'
- Regole di risk management
- Criteri per defer vs approva

## Schema JSON

Il file `schemas/ai_advisor_output.json` definisce la struttura dell'output enforced da `--json-schema`.

## Configurazione

| Variabile | Default | Descrizione |
|---|---|---|
| `AI_DECISION_INTERVAL` | `60` | Secondi tra un ciclo e l'altro |
| `AI_MODEL` | `opus` | Modello Claude Code (`opus`, `sonnet`, `haiku`) |
| `AI_TIMEOUT` | `120` | Timeout per la chiamata CLI (secondi) |
| `AI_MIN_CONFIDENCE` | `0.6` | Sotto questa soglia → HOLD forzato |
| `AI_FALLBACK_ON_ERROR` | `HOLD` | Azione di default se l'AI non risponde |
| `AI_LOG_REASONING` | `true` | Logga il reasoning dell'AI |
| `AI_MAX_SPREAD_PCT` | `0.5` | Spread massimo per screening |

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
```

Definita in `core/types.py`.

## Costi

L'AI advisor usa **una singola chiamata CLI per ciclo** al modello configurato.
La chiamata viene saltata se non ci sono posizioni ne' opportunita'.

Con `--no-ai`, il costo AI e' zero (pure rule-based).
