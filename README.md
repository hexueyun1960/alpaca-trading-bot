# Alpaca

Alpaca is a paper-first automated trading prototype for Alpaca.

The current goal is to build a safe trading loop before any live trading:

1. Load configuration from environment variables.
2. Read recent market bars from Alpaca Market Data.
3. Generate a conservative strategy signal.
4. Pass the signal through risk checks.
5. Submit to Alpaca Paper Trading only when explicitly enabled.
6. Record every decision in logs.

This project is not financial advice. Automated trading can lose money, and live trading should only be enabled after extended paper testing.

## Quick Start

Create a local `.env` file from `.env.example`, then set your Alpaca paper credentials:

```powershell
Copy-Item .env.example .env
```

Run a dry-run cycle:

```powershell
python -m src.bot
```

Run the monitor loop:

```powershell
python -m src.monitor
```

By default, Alpaca does not place real paper orders. It prints the order it would submit.

Check configuration before running:

```powershell
python -m src.doctor
```

Trading decisions are written to `logs/trade_journal.jsonl` by default.

## Safety Defaults

- `ALPACA_DRY_RUN=true`
- `ALPACA_ENABLE_TRADING=false`
- `ALPACA_BASE_URL=https://paper-api.alpaca.markets`
- Symbol whitelist is required through `ALPACA_SYMBOLS`
- Monitor loop interval defaults to `ALPACA_MONITOR_INTERVAL_SECONDS=60`
- Short VWAP spike orders use regular-hours market orders only

To allow paper orders, both of these must be set:

```text
ALPACA_DRY_RUN=false
ALPACA_ENABLE_TRADING=true
```

Keep these disabled until paper credentials and risk limits are confirmed.

## Project Progress

See [PROJECT_PROGRESS.md](PROJECT_PROGRESS.md).
