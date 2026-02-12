# Hyperliquid Perpetual Trading Bot

## Documentazione

**IMPORTANTE:** `README.md` e la cartella `docs/` devono essere sempre tenuti aggiornati quando si modificano funzionalita', architettura, configurazione, o schema del database. Ogni modifica al codice che cambia comportamenti documentati deve includere l'aggiornamento della documentazione corrispondente.

- `README.md` — Overview, quick start, configurazione
- `docs/DEVELOPMENT.md` — Guida tecnica, struttura progetto, convenzioni
- `docs/architecture.md` — Architettura del sistema, componenti, flusso dati
- `docs/strategies.md` — Strategie di trading, indicatori, condizioni entry/exit
- `docs/risk-management.md` — Risk management, trailing stop, Kelly sizing
- `docs/ai-engine.md` — AI Advisor (Claude Code CLI)
- `docs/configuration.md` — Tutte le variabili di configurazione
- `docs/database.md` — Schema database, migrazioni
- `docs/deployment.md` — Setup, installazione, deployment
- `docs/testing.md` — Test suite, fixtures, marker, esecuzione

## Obiettivo

Bot automatico in Python per trading su **Hyperliquid Perpetual Futures** con AI advisor opzionale (Claude Code CLI). Opera 24/7 con strategie Mean Reversion + RSI Divergence (LONG + SHORT), leva conservativa (2-3x), fee ultra-basse (0.06% RT).

## Contesto

- L'utente e' un full-stack developer (Laravel/PHP, React Native) italiano
- Migrato da Binance Spot (fee troppo alte) a Hyperliquid Perps
- Binance Futures bloccato per utenti EU (MiCA)
- Hyperliquid: 0.06% RT fees, shorts, leverage, no KYC
- Settlement nativo in USDC
- Home lab con Proxmox per deployment

## Riferimenti tecnici

Per dettagli su architettura, SDK, strategie, risk management, e convenzioni di codice, vedere `docs/DEVELOPMENT.md`.
