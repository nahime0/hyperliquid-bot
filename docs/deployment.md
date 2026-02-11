# Deployment

## Requisiti

- **Python 3.11+** (testato con 3.14)
- **pip** per installazione dipendenze
- **Wallet Hyperliquid** con fondi su testnet o mainnet
- **API Key Anthropic** per il motore AI (opzionale con `--no-ai`)

## Installazione locale

```bash
# Clone
git clone <repo-url>
cd binance

# Virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Dipendenze
pip install -r requirements.txt

# Configurazione
cp .env.example .env
# Modifica .env con le tue credenziali
```

## Configurazione minima

Modifica `.env`:

```env
# Obbligatorio per trading
HL_PRIVATE_KEY=0x...your_private_key...
HL_ACCOUNT_ADDRESS=0x...your_address...
HL_TESTNET=true

# Obbligatorio per AI (opzionale con --no-ai)
ANTHROPIC_API_KEY=sk-ant-api03-...
```

## Verifica connessione

```bash
# Test connessione testnet
.venv/bin/python scripts/test_connection.py

# Oppure
.venv/bin/python -c "
from hyperliquid.info import Info
info = Info('https://api.hyperliquid-testnet.xyz', skip_ws=True)
print('Assets:', len(info.meta()['universe']))
print('BTC mid:', info.all_mids().get('BTC'))
"
```

## Primo avvio

```bash
# 1. Test veloce (paper + no AI + singolo ciclo + no dashboard)
.venv/bin/python main.py --paper --no-ai --once --no-dashboard

# 2. Test con AI review
.venv/bin/python main.py --paper --once

# 3. Paper trading continuo
.venv/bin/python main.py --paper

# 4. Testnet con ordini reali
.venv/bin/python main.py

# 5. MAINNET (solo dopo validazione!)
.venv/bin/python main.py --live
```

## Deployment su server (systemd)

### Creare il file di servizio

```ini
# /etc/systemd/system/trading-bot.service
[Unit]
Description=Hyperliquid Trading Bot
After=network.target

[Service]
Type=simple
User=trading
WorkingDirectory=/opt/trading-bot
ExecStart=/opt/trading-bot/.venv/bin/python main.py --paper
Restart=on-failure
RestartSec=30
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

### Comandi

```bash
# Abilitare e avviare
sudo systemctl enable trading-bot
sudo systemctl start trading-bot

# Verificare stato
sudo systemctl status trading-bot

# Logs
sudo journalctl -u trading-bot -f

# Fermare
sudo systemctl stop trading-bot
```

## Deployment con supervisor

```ini
# /etc/supervisor/conf.d/trading-bot.conf
[program:trading-bot]
command=/opt/trading-bot/.venv/bin/python main.py --paper
directory=/opt/trading-bot
user=trading
autostart=true
autorestart=true
startsecs=10
startretries=3
redirect_stderr=true
stdout_logfile=/var/log/trading-bot/output.log
stdout_logfile_maxbytes=10MB
stdout_logfile_backups=5
```

## Docker (opzionale)

```dockerfile
FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "main.py", "--paper"]
```

```bash
docker build -t trading-bot .
docker run -d --name trading-bot --env-file .env trading-bot
```

## Monitoring

### Dashboard web

La dashboard si avvia automaticamente con il bot su porta 8080. Accessibile via browser.

```bash
# Solo dashboard (senza bot)
.venv/bin/python -m dashboard.server --port 8080
```

### Log

I log vengono scritti in `logs/bot.log` con rotazione automatica (5MB, 5 file di backup).

```bash
# Seguire i log in tempo reale
tail -f logs/bot.log

# Filtrare errori
grep -i error logs/bot.log
```

### Telegram

Configura `TELEGRAM_BOT_TOKEN` e `TELEGRAM_CHAT_ID` per ricevere:
- Alert su trade eseguiti
- Notifiche kill switch e daily pause
- Start/stop del bot

### Database

Il database SQLite si trova in `data/trading_bot.db`. Puo' essere ispezionato con:

```bash
sqlite3 data/trading_bot.db
.tables
SELECT * FROM trades ORDER BY id DESC LIMIT 10;
SELECT * FROM positions WHERE status='OPEN';
```

## Sicurezza

- **Private key:** Non committare `.env` nel repository. Usa `.gitignore`.
- **API wallet:** Su Hyperliquid, crea un API wallet dedicato con limiti di prelievo.
- **Network:** Il bot necessita di connessione HTTPS verso `api.hyperliquid.xyz` o `api.hyperliquid-testnet.xyz`.
- **Firewall:** Se la dashboard e' esposta, limitare l'accesso alla rete locale o usare un reverse proxy con autenticazione.

## Upgrade

```bash
cd /opt/trading-bot
git pull
source .venv/bin/activate
pip install -r requirements.txt
sudo systemctl restart trading-bot
```

Le migrazioni del database vengono applicate automaticamente all'avvio.

## Troubleshooting

| Problema | Soluzione |
|---|---|
| `ConnectionError` su API | Verificare connessione internet e URL API |
| `RuntimeError: Client not connected` | Assicurarsi che `connect()` venga chiamato prima di operare |
| Kill switch attivato | Reset manuale necessario, verificare drawdown |
| `No mid price for X` | Il coin potrebbe non essere disponibile su Hyperliquid |
| Dashboard non si apre | Verificare che la porta 8080 non sia occupata |
| `szDecimals not found` | Il coin non e' nella universe di Hyperliquid |
