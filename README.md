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
ALPACA_DYNAMIC_UNIVERSE
ALPACA_UNIVERSE_MAX_SYMBOLS
ALPACA_UNIVERSE_REFRESH_INTERVAL_SECONDS
ALPACA_UNIVERSE_CHUNK_SIZE
ALPACA_TIMEFRAME
ALPACA_BAR_LIMIT
ALPACA_MARKET_DATA_LOOKBACK_DAYS
ALPACA_ALLOWED_EXCHANGES
ALPACA_MIN_PRICE
ALPACA_MAX_PRICE
ALPACA_MIN_AVG_DAILY_VOLUME_30D
ALPACA_MIN_AVG_DAILY_DOLLAR_VOLUME_30D
ALPACA_REQUIRE_ETB
ALPACA_REQUIRE_SHORTABLE
ALPACA_REQUIRE_FRACTIONABLE
ALPACA_ALLOWED_BORROW_STATUSES
ALPACA_MAX_SPREAD_PCT
ALPACA_USE_WEBSOCKET
ALPACA_MARKET_DATA_FEED
ALPACA_WEBSOCKET_BROAD_BARS
ALPACA_MAX_CANDIDATE_SYMBOLS
ALPACA_MAX_HIGH_PRIORITY_SYMBOLS
ALPACA_CANDIDATE_TTL_SECONDS
ALPACA_MIN_5M_GAIN_PCT
ALPACA_MIN_15M_GAIN_PCT
ALPACA_MIN_RELATIVE_VOLUME
ALPACA_MIN_INTRADAY_DOLLAR_VOLUME
ALPACA_STALE_QUOTE_SECONDS
ALPACA_MAX_SLIPPAGE_PCT
ALPACA_ORDER_TIMEOUT_SECONDS
ALPACA_MAX_REPRICE_ATTEMPTS
ALPACA_MAX_OPEN_POSITIONS
ALPACA_MAX_DAILY_LOSS
ALPACA_VWAP_ENTRY_DEVIATION_PCT
ALPACA_FIRST_ORDER_EQUITY_PCT
ALPACA_FIXED_ENTRY_NOTIONAL
ALPACA_ENABLE_SECOND_ENTRY
ALPACA_SECOND_ORDER_DISTANCE_PCT
ALPACA_SECOND_ORDER_EQUITY_PCT
ALPACA_MAX_LOSS_PER_SYMBOL_EQUITY_PCT
ALPACA_REQUIRE_HARD_STOP
ALPACA_HARD_STOP_DISTANCE_PCT
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

## Current Paper Strategy

The REST paper strategy uses session VWAP mean reversion. A first entry is considered when price is at least 4% above session VWAP for a short or at least 4% below session VWAP for a long. The submitted entry is a market order sized to approximately `ALPACA_FIXED_ENTRY_NOTIONAL`, currently 240 USD, rounded down to whole shares.

Second entry is enabled when `ALPACA_ENABLE_SECOND_ENTRY=true`. It does not place a resting limit order. After the first fill, the bot waits until price moves 4% against the first average fill price, then submits a second market order with the same approximate fixed notional.

Exits are market orders when price crosses back through session VWAP. New entries stop at `ALPACA_NO_NEW_ENTRIES_AFTER=15:30`; forced flatten starts at `ALPACA_FORCE_FLATTEN_TIME=15:55`.

## Universe

By default the bot scans only `ALPACA_SYMBOLS`. To build the scan list from Alpaca active US equity assets, enable:

```text
ALPACA_DYNAMIC_UNIVERSE=true
ALPACA_UNIVERSE_MAX_SYMBOLS=100
```

Dynamic universe mode uses a narrow first-stage eligibility filter:

1. Load all active Alpaca US equity assets.
2. Keep only `active=true`, `tradable=true`, and ETB symbols.
3. Do not filter this first-stage pool by ATR, market cap, volume, dollar volume, price, spread, shortable, or fractionable.
4. Apply `ALPACA_UNIVERSE_MAX_SYMBOLS` only as a safety cap after the active/tradable/ETB filter.

Set `ALPACA_UNIVERSE_MAX_SYMBOLS=0` to remove the post-filter symbol cap. This can still be slow if the filters leave too many symbols, so the default keeps a bounded candidate pool. `ALPACA_UNIVERSE_REFRESH_INTERVAL_SECONDS` controls how often the broad universe is rebuilt; between refreshes the monitor reuses the cached candidate list.

Borrow availability prefers Alpaca's newer `borrow_status` field when present and falls back to `easy_to_borrow` for compatibility. The acceptable borrow statuses are configured with `ALPACA_ALLOWED_BORROW_STATUSES`.

Shortable is not a first-stage universe filter. It is checked later before short entries. Final entry checks still include current spread, quote freshness, shortability for short entries, and the risk checks in `risk.py`.

## WebSocket Scanner

The REST monitor is useful for simple paper testing, but second-level reaction requires the WebSocket scanner:

```bash
python -m src.stream_monitor
```

`src.stream_monitor` opens two WebSocket connections:

1. Market data stream: subscribes to minute bars, using `bars:["*"]` when `ALPACA_WEBSOCKET_BROAD_BARS=true`.
2. Trading stream: listens to `trade_updates` so submitted, filled, canceled, and rejected orders update local state.

The real-time engine keeps local bar and quote caches. It loads or observes session open values for the active/tradable/ETB universe, then ranks that pool by:

- Top 20 current-day gainers.
- Top 20 current-day losers.

The ranking formula is `current_price / session_open - 1`. Symbols that enter the ranking remain in the realtime candidate pool for `ALPACA_CANDIDATE_RETENTION_SECONDS` so a temporary drop out of the top 20 does not immediately remove monitoring. Candidate symbols are then subscribed for quotes/trades. Before opening a position, the engine checks:

- WebSocket connectivity.
- Per-symbol trigger lock.
- Existing pending order lock.
- Stale quote age.
- Max spread.
- Max slippage via a marketable limit price.
- Existing positions and open orders.
- `client_order_id` de-duplication.

Unfilled realtime orders are canceled after `ALPACA_ORDER_TIMEOUT_SECONDS`. Keep `ALPACA_USE_WEBSOCKET=false` until this stream has been observed in paper mode; the existing systemd service still runs `src.monitor` unless you explicitly change it.

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
It also records `monitor_started`, `monitor_cycle_started`, `monitor_cycle_finished`, and `monitor_cycle_error` events to the trade journal so you can verify that the server process is actually alive.

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

For unattended VPS trading, install the systemd service. Running `python -m src.bot` manually is only one cycle; running `python -m src.monitor` in an SSH shell stops when that shell/session dies unless it is managed by a service.

Start and inspect the service:

```bash
sudo bash deploy/install_systemd_service.sh /opt/alpaca-bot alpaca-bot
sudo systemctl status alpaca-bot
journalctl -u alpaca-bot -f
```

Run the VPS health check:

```bash
bash deploy/check_vps_status.sh /opt/alpaca-bot alpaca-bot
```

See [docs/VPS_AUTOTRADE_RUNBOOK.md](docs/VPS_AUTOTRADE_RUNBOOK.md) for the full Chinese VPS deployment and troubleshooting checklist.

## Project Progress

See [PROJECT_PROGRESS.md](PROJECT_PROGRESS.md).
