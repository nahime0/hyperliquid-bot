# Risk Management

## Architettura

Il sistema di risk management e' composto da tre componenti cooperanti:

1. **RiskManager** (`risk/risk_manager.py`) — Validatore centrale con potere di veto
2. **PositionTracker** (`risk/position_tracker.py`) — Gestione posizioni, SL/TP, trailing stop
3. **PositionSizer** (`risk/position_sizer.py`) — Calcolo dimensione posizione (Kelly Criterion)

Il Risk Manager ha **potere di veto assoluto** su ogni decisione. Anche se la strategia e l'AI dicono BUY, il Risk Manager puo' bloccare.

## Kill Switch

Il bot si **ferma completamente** se una di queste condizioni e' vera:

| Condizione | Soglia | Azione |
|---|---|---|
| Drawdown totale | >= 15% dal peak | Kill switch (ferma tutto) |
| Balance | < 50 USDC | Kill switch |
| Perdite consecutive | >= 5 | Kill switch |

Una volta attivato, il kill switch richiede un **reset manuale**.

Il drawdown e' calcolato dal peak balance storico:
```
drawdown_pct = (peak_balance - current_balance) / peak_balance * 100
```

## Daily Pause

Se il drawdown intra-giornaliero supera il **5%**, le nuove entry (BUY e SHORT) vengono bloccate fino al giorno successivo (reset a mezzanotte UTC). CLOSE e' sempre permesso.

```
daily_drawdown = (start_of_day_balance - current_balance) / start_of_day_balance * 100
```

## Validation Flow

Ogni decisione passa attraverso `validate_decision()`:

1. **HOLD** → pass-through (sempre approvato)
2. **CLOSE/SELL** → check holding period minimo (15 min)
3. **Kill switch** → bloccato se attivo
4. **Daily pause** → bloccato se attivo (solo BUY/SHORT)
5. **Symbol required** → bloccato se non specificato
6. **Max positions** → bloccato se >= max posizioni (dinamico o statico)
7. **Duplicate** → bloccato se esiste gia' posizione sullo stesso coin
8. **Min balance** → bloccato se balance < 50 USDC
9. **Min confidence** → bloccato se confidence < 0.5
10. **Position sizing** → calcolo Kelly Criterion
11. **Auto SL/TP** → applicato automaticamente se non specificato

### SL automatico (direction-aware)

Se la decisione non include SL, viene calcolato automaticamente:

| Direzione | Stop Loss |
|---|---|
| BUY (LONG) | price * (1 - stop_loss_pct / 100) |
| SHORT | price * (1 + stop_loss_pct / 100) |

Default: SL = 1%.

### Take Profit (opzionale)

Di default `AUTO_TAKE_PROFIT=false`: il trailing stop e' il meccanismo primario di profit-taking. Con `AUTO_TAKE_PROFIT=true`, viene generato anche un TP automatico:

| Direzione | Take Profit |
|---|---|
| BUY (LONG) | price * (1 + take_profit_pct / 100) |
| SHORT | price * (1 - take_profit_pct / 100) |

L'AI advisor puo' comunque specificare un TP esplicito indipendentemente da questa impostazione.

### Max Positions (dinamico)

Con `DYNAMIC_POSITIONS=true` (default), il numero massimo di posizioni scala col balance:

```
max_positions = min(MAX_OPEN_POSITIONS, balance // USDC_PER_POSITION)
max_positions = max(1, max_positions)  # floor a 1
```

Esempio con `USDC_PER_POSITION=25`, `MAX_OPEN_POSITIONS=5`:
| Balance | Max Positions |
|---|---|
| 10 USDC | 1 (floor) |
| 50 USDC | 2 |
| 100 USDC | 4 |
| 125 USDC | 5 (cap) |
| 1000 USDC | 5 (cap) |

## Position Sizing (Kelly Criterion)

### Formula

```
f = (win_rate * payoff_ratio - (1 - win_rate)) / payoff_ratio
size = bankroll * (f / 4)  # quarter-Kelly (conservativo)
```

### Cold Start (< 10 trade)

Quando non c'e' abbastanza storico per Kelly:
- Size fissa: **5% del bankroll**
- AI hint come soft cap (prende il minimo tra 5% e AI suggestion)
- Hard cap: `max_trade_pct` (default 10%)

### Post Cold Start

- Kelly fraction calcolata da win_rate, avg_win, avg_loss storici
- Quarter-Kelly per conservativismo
- Scalata per confidence dell'AI
- AI suggestion come soft cap
- Hard cap da config
- Floor: min 10 USDC (ordini piu' piccoli vengono scartati)

### Calcolo notional

```python
margin = size_usdc  # dall'output Kelly
notional = margin * leverage  # es. margin 10 USDC * 2x = 20 USDC notional
qty = notional / price  # quantita' dell'asset
qty = round_size(coin, qty)  # arrotondato a szDecimals
```

## Position Tracking

### Open Position

Ogni BUY o SHORT crea una posizione nel DB con:
- `entry_price`, `quantity`, `stop_loss`, `take_profit`
- `direction` (LONG o SHORT)
- `leverage`
- `max_price_seen` (per trailing LONG)
- `min_price_seen` (per trailing SHORT)
- `original_sl` (SL originale, non modificato dal trailing)

### Close Position

PnL direction-aware:
```python
if direction == "LONG":
    pnl = (exit_price - entry_price) * quantity
else:  # SHORT
    pnl = (entry_price - exit_price) * quantity

# Fee estimate (Hyperliquid taker: 0.045% per leg)
fee = (entry_price + exit_price) * quantity * 0.00045
pnl_net = pnl - fee
```

## Trailing Stop

### LONG Trailing

Traccia il prezzo massimo visto (`max_price_seen`). Il SL puo' solo salire, mai scendere.

| Fase | Trigger (gain_pct) | SL |
|---|---|---|
| Fisso | < 1.0% | SL originale (non si muove) |
| Break-even | >= 1.0% | SL = entry price |
| Trailing | >= 1.5% | SL = max_price * (1 - 1.0%) |
| Tight trailing | >= 2.5% | SL = max_price * (1 - 0.75%) |

```
gain_pct = (max_price_seen - entry_price) / entry_price * 100
```

### SHORT Trailing

Traccia il prezzo minimo visto (`min_price_seen`). Il SL puo' solo scendere, mai salire.

| Fase | Trigger (gain_pct) | SL |
|---|---|---|
| Fisso | < 1.0% | SL originale |
| Break-even | >= 1.0% | SL = entry price |
| Trailing | >= 1.5% | SL = min_price * (1 + 1.0%) |
| Tight trailing | >= 2.5% | SL = min_price * (1 + 0.75%) |

```
gain_pct = (entry_price - min_price_seen) / entry_price * 100
```

## Time Stop

Posizioni aperte da piu' di `time_stop_hours` (default 4h) con PnL inferiore a `time_stop_min_pnl_pct` (default 0.5%) vengono chiuse automaticamente.

PnL direction-aware:
```python
if direction == "LONG":
    pnl_pct = (current_price - entry) / entry * 100
else:
    pnl_pct = (entry - current_price) / entry * 100
```

## Liquidation Monitoring

Ad ogni refresh, il Risk Manager controlla la distanza dal prezzo di liquidazione per ogni posizione aperta:

```python
distance_pct = abs(entry_price - liquidation_price) / entry_price * 100
if distance_pct < liquidation_buffer_pct:  # default 5%
    logger.warning("LIQUIDATION WARNING: ...")
```

Con leva 2x e SL 1%, il rischio di liquidazione e' praticamente zero (servirebbe un movimento del 50%).

## Cooldown

| Tipo | Durata | Trigger |
|---|---|---|
| Per-symbol | 30 min | Dopo una perdita sullo stesso coin |
| Global | 15 min | Dopo 3 perdite consecutive |

Il cooldown previene il "revenge trading" dopo le perdite.

## Anti-Churning

Il main loop implementa due protezioni anti-churning:

1. **Cross-decision:** Se nello stesso ciclo ci sono CLOSE e BUY/SHORT sullo stesso coin, l'entry viene scartata
2. **Intra-cycle:** Se un coin viene chiuso durante il ciclo, non puo' essere riaperto nello stesso ciclo
