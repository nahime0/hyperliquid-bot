# AI Advisor (Claude Code CLI)

## Overview

The bot uses an **optional AI advisor** based on Claude Code CLI. Decisions are generated autonomously by the strategies (Mean Reversion, RSI Divergence), and the AI intervenes as an **advisor** with the ability to:

- **Approve** trading opportunities (with possible adjustments)
- **Defer** opportunities (wait N cycles or a certain price)
- **Close** open positions
- **Adjust** SL/TP/leverage on open positions

The `--no-ai` flag completely disables the AI, leaving the bot in pure rule-based mode.

## Architecture

**A single CLI call per cycle** — simple and reliable.

```
Strategies generate candidates
        |
        v
Check deferred (conditions met?)
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
Risk validate -> Execute
```

If there are no open positions AND no opportunities, the AI call is skipped.
On error or timeout: log warning, the bot continues autonomously.

## CLI Invocation

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

- `--json-schema` enforces the output structure
- `--allowedTools ""` disables tools (pure inference, no file reads)
- `--max-turns 1` prevents agentic loops
- Timeout: 120s (configurable via `AI_TIMEOUT`)

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
    "open_position_count": 1, "max_positions": 15,
    "consecutive_losses": 0,
    "capital_utilization_pct": 7.0, "total_margin_used": 70.0,
    "available_margin": 930.0, "target_utilization_pct": 50.0
  },
  "recent_trades": [ ... ],
  "trade_stats": { "total_trades": 42, "win_rate": 0.62, "avg_win": 8.5, "avg_loss": -5.2 },
  "deferred": [
    { "symbol": "ETH", "action": "SHORT", "type": "opportunity", "deferred_cycles_ago": 3, "wait_cycles": 5 },
    { "symbol": "SOL", "action": "HOLD", "type": "position_hold", "deferred_cycles_ago": 8, "wait_until_price_above": 200 }
  ]
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

### Position Actions

| Action | Effect |
|---|---|
| `HOLD` | No action (trailing stop/time stop continue) |
| `CLOSE` | Immediate position closure |
| `ADJUST` | Modify SL, TP, or leverage via `adjustments` |
| `SCALE_UP` | Add margin to a profitable position (VWAP entry). Requires `size_pct` in `adjustments` |

### Opportunity Actions

| Action | Effect |
|---|---|
| `BUY`/`SHORT` | Approve entry (with optional `adjustments`) |
| `HOLD` | Defer entry. Use `defer` for conditions |

### Defer Conditions

```json
{
  "defer": {
    "wait_cycles": 5,
    "wait_until_price_above": 45500,
    "wait_until_price_below": 44000
  }
}
```

- `wait_cycles`: re-evaluate after N cycles (~60s each)
- `wait_until_price_above/below`: re-evaluate when price reaches the level

Deferred opportunities are persisted in the database.

## System Prompt

The file `prompts/ai_advisor.md` contains the instructions for the AI advisor, including:

- Role and responsibilities
- Guidelines for position and opportunity decisions
- Risk management rules
- Criteria for defer vs approve

## JSON Schema

The file `schemas/ai_advisor_output.json` defines the output structure enforced by `--json-schema`.

## Configuration

| Variable | Default | Description |
|---|---|---|
| `AI_DECISION_INTERVAL` | `60` | Seconds between cycles |
| `AI_MODEL` | `opus` | Claude Code model (`opus`, `sonnet`, `haiku`) |
| `AI_TIMEOUT` | `120` | CLI call timeout (seconds) |
| `AI_MIN_CONFIDENCE` | `0.6` | Below this threshold -> forced HOLD |
| `AI_FALLBACK_ON_ERROR` | `HOLD` | Default action if the AI doesn't respond |
| `AI_LOG_REASONING` | `true` | Log AI reasoning |

## Decision Dataclass

```python
@dataclass
class Decision:
    action: str           # BUY, SHORT, SELL, HOLD, CLOSE, SCALE_UP
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

Defined in `core/types.py`.

## Costs

The AI advisor uses **a single CLI call per cycle** to the configured model.
The call is skipped if there are no positions or opportunities.

With `--no-ai`, AI cost is zero (pure rule-based).
