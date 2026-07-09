# Alpaca Trading Bot

Alpaca Trading Bot is a paper-first automated trading prototype for Alpaca.

The current goal is to run a safe trading loop before any live trading:

1. Load configuration from `.env` or environment variables.
2. Read recent market bars from Alpaca Market Data.
3. Generate a VWAP mean-reversion strategy signal.
4. Pass the signal through risk checks.
5. Submit to Alpaca Paper Trading only when explicitly enabled.
6. Record every decision in JSONL logs.

This project is not financial advice. Automated trading can lose money, and live trading should only be enabled after extended paper testing.

## Requirements

The project currently uses only the Python standard library. `requirements.txt` is included so VPS setup can still run:

```bash
pip install -r requirements.txt
```

## Configuration

Create a local `.env` file from `.env.example`, then set your Alpaca credentials:

```bash
cp .env.example .env
nano .env
```

The code reads these environment variables:

```text
ALPACA_API_KEY_ID
ALPACA_API_SECRET_KEY
ALPACA_BASE_URL
ALPACA_DATA_URL
ALPACA_SYMBOLS
ALPACA_TIMEFRAME
ALPACA_BAR_LIMIT
ALPACA_MARKET_DATA_LOOKBACK_DAYS
ALPACA_MIN_PRICE
ALPACA_MIN_AVG_DAILY_VOLUME_30D
ALPACA_REQUIRE_ETB
ALPACA_MAX_SPREAD_PCT
ALPACA_VWAP_ENTRY_DEVIATION_PCT
ALPACA_FIRST_ORDER_EQUITY_PCT
ALPACA_SECOND_ORDER_DISTANCE_PCT
ALPACA_SECOND_ORDER_EQUITY_PCT
ALPACA_MAX_LOSS_PER_SYMBOL_EQUITY_PCT
ALPACA_ONE_TRADE_CYCLE_PER_SYMBOL_PER_DAY
ALPACA_MAX_ENTRIES_PER_CYCLE
ALPACA_NO_NEW_ENTRIES_AFTER
ALPACA_FORCE_FLATTEN_TIME
ALPACA_FINAL_POSITION_CHECK_TIME
ALPACA_REGULAR_SESSION_ONLY
ALPACA_NO_OVERNIGHT
ALPACA_MAX_NOTIONAL_PER_ORDER
ALPACA_SHORT_SPIKE_NOTIONAL
ALPACA_MIN_CASH_RESERVE
ALPACA_DRY_RUN
ALPACA_ENABLE_TRADING
ALPACA_ALLOW_LIVE_TRADING
ALPACA_JOURNAL_PATH
ALPACA_MONITOR_INTERVAL_SECONDS
```

## Safety Defaults

Default behavior is preview and logging only:

```text
ALPACA_DRY_RUN=true
ALPACA_ENABLE_TRADING=false
ALPACA_BASE_URL=https://paper-api.alpaca.markets
ALPACA_ALLOW_LIVE_TRADING=false
```

Paper orders are submitted only when both of these are set:

```text
ALPACA_DRY_RUN=false
ALPACA_ENABLE_TRADING=true
```

Live trading is blocked unless `ALPACA_ALLOW_LIVE_TRADING=true` is also set. Do not enable live trading until the bot has been reviewed, monitored, and tested in paper mode.

## Commands

Check configuration:

```bash
python -m src.doctor
```

Run one dry-run strategy cycle:

```bash
python -m src.bot
```

Preview one guarded paper market order:

```bash
python -m src.paper_order --symbol SPY --side buy --notional 25
```

Submit one real paper market order only after explicitly enabling paper trading in `.env`:

```text
ALPACA_DRY_RUN=false
ALPACA_ENABLE_TRADING=true
```

Then run:

```bash
python -m src.paper_order --symbol SPY --side buy --notional 25 --confirm
```

The command refuses non-paper endpoints, logs the risk decision, submits through the same `risk.py` checks as the strategy loop, and queries the submitted order status with `GET /v2/orders/{order_id}`. For real paper submission while the market is closed, add `--allow-queued` only when you intentionally want Alpaca to queue the day order.

Run the long-lived monitor loop:

```bash
python -m src.monitor
```

`src.monitor` loads `.env`, runs `src.bot.run_once()` every `ALPACA_MONITOR_INTERVAL_SECONDS` seconds, logs exceptions, skips normally when the market is closed, and continues after single-cycle failures.

## VPS Deployment

On Ubuntu, after cloning the repository to `/opt/alpaca-bot`:

```bash
cd /opt/alpaca-bot
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
nano .env
python -m src.doctor
python -m src.monitor
```

Set `ALPACA_JOURNAL_PATH=/opt/alpaca-bot/logs/trade_journal.jsonl` on the VPS. The bot creates the log directory automatically if it does not exist.

## systemd Service

Create `/etc/systemd/system/alpaca-bot.service`:

```ini
[Unit]
Description=Alpaca Trading Bot
After=network.target

[Service]
WorkingDirectory=/opt/alpaca-bot
ExecStart=/opt/alpaca-bot/venv/bin/python -m src.monitor
Restart=always
RestartSec=10
EnvironmentFile=/opt/alpaca-bot/.env

[Install]
WantedBy=multi-user.target
```

Start and inspect the service:

```bash
sudo systemctl daemon-reload
sudo systemctl enable alpaca-bot
sudo systemctl start alpaca-bot
sudo systemctl status alpaca-bot
journalctl -u alpaca-bot -f
```

## Project Progress

See [PROJECT_PROGRESS.md](PROJECT_PROGRESS.md).
