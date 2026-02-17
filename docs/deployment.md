# Deployment

## Requirements

- **Python 3.11+** (tested with 3.14)
- **pip** for dependency installation
- **Hyperliquid wallet** with funds on testnet or mainnet
- **Node.js 18+** for the Next.js dashboard (optional)
- **Anthropic API Key** for the AI engine (optional with `--no-ai`)

## Local Installation

```bash
# Clone
git clone <repo-url>
cd binance

# Virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Dependencies
pip install -r requirements.txt

# Configuration
cp .env.example .env
# Edit .env with your credentials
```

## Minimum Configuration

Edit `.env`:

```env
# Required for trading
HL_PRIVATE_KEY=0x...your_private_key...
HL_ACCOUNT_ADDRESS=0x...your_address...
HL_TESTNET=true

# AI Advisor (optional with --no-ai)
AI_ADVISOR=claude
```

## Connection Verification

```bash
# Test testnet connection
.venv/bin/python scripts/test_connection.py

# Or
.venv/bin/python -c "
from hyperliquid.info import Info
info = Info('https://api.hyperliquid-testnet.xyz', skip_ws=True)
print('Assets:', len(info.meta()['universe']))
print('BTC mid:', info.all_mids().get('BTC'))
"
```

## First Run

```bash
# 1. Quick test (paper + no AI + single cycle)
.venv/bin/python main.py --paper --no-ai --once

# 2. Test with AI review
.venv/bin/python main.py --paper --once

# 3. Continuous paper trading
.venv/bin/python main.py --paper

# 4. Testnet with real orders
.venv/bin/python main.py

# 5. MAINNET (only after validation!)
.venv/bin/python main.py --live
```

## Server Deployment (systemd)

### Create the service file

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

### Commands

```bash
# Enable and start
sudo systemctl enable trading-bot
sudo systemctl start trading-bot

# Check status
sudo systemctl status trading-bot

# Logs
sudo journalctl -u trading-bot -f

# Stop
sudo systemctl stop trading-bot
```

## Deployment with supervisor

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

## Docker (optional)

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

### Web Dashboard (Next.js)

The dashboard is a separate Next.js application in the `web/` folder. It reads the SQLite database in read-only mode (WAL mode) via `better-sqlite3` and the `data/bot_status.json` file for live state. Auto-refresh via SWR polling (no WebSocket needed).

```bash
# Development (port 3000)
cd web && npm install && npm run dev

# Production
cd web && npm run build && npm start
```

### Logs

Logs are written to `logs/bot.log` with automatic rotation (5MB, 5 backup files).

```bash
# Follow logs in real-time
tail -f logs/bot.log

# Filter errors
grep -i error logs/bot.log
```

### Telegram

Configure `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` to receive:
- Alerts on executed trades
- Kill switch and daily pause notifications
- Bot start/stop

### Database

The SQLite database is located at `data/trading_bot.db`. It can be inspected with:

```bash
sqlite3 data/trading_bot.db
.tables
SELECT * FROM trades ORDER BY id DESC LIMIT 10;
SELECT * FROM positions WHERE status='OPEN';
```

## Security

- **Private key:** Do not commit `.env` to the repository. Use `.gitignore`.
- **API wallet:** On Hyperliquid, create a dedicated API wallet with withdrawal limits.
- **Network:** The bot requires HTTPS connectivity to `api.hyperliquid.xyz` or `api.hyperliquid-testnet.xyz`.
- **Firewall:** If the Next.js dashboard is exposed, restrict access to the local network or use a reverse proxy with authentication.

## Upgrade

```bash
cd /opt/trading-bot
git pull
source .venv/bin/activate
pip install -r requirements.txt
sudo systemctl restart trading-bot
```

Database migrations are applied automatically at startup.

## Troubleshooting

| Problem | Solution |
|---|---|
| `ConnectionError` on API | Check internet connection and API URL |
| `RuntimeError: Client not connected` | Ensure `connect()` is called before operating |
| Kill switch activated | Manual reset needed, check drawdown |
| `No mid price for X` | The coin may not be available on Hyperliquid |
| Dashboard won't open | Check that port 3000 is not in use (`lsof -i :3000`) |
| `szDecimals not found` | The coin is not in the Hyperliquid universe |
