# Crypto Pump and Dump Telegram Signal Bot

This Python bot monitors Binance spot aggregate trades over WebSocket and sends Telegram alerts when price movement, volume acceleration, and traded notional cross configurable thresholds.

## Signal logic

A signal triggers only when all three conditions are met:

1. Absolute price move over the configured window exceeds `MIN_PRICE_CHANGE_PCT`.
2. Current quote-volume rate exceeds the prior baseline by `MIN_VOLUME_MULTIPLIER`.
3. Current traded notional exceeds `MIN_NOTIONAL_USDT`.

Positive moves are labeled `PUMP`. Negative moves are labeled `DUMP`.

Each alert includes:

- Trading pair
- Current price
- Percentage move
- Volume acceleration ratio
- Recent traded notional
- Heuristic 0 to 100 signal score
- PNG price chart

This is a market-monitoring alert system, not a prediction engine and not financial advice.

## 1. Create your Telegram bot

Open Telegram and message `@BotFather`.

Run:

`/newbot`

Follow the prompts and copy the token.

Start a chat with your new bot and send `/start`.

## 2. Get your chat ID

One simple method is to temporarily run a bot API `getUpdates` request after messaging your bot, then read `message.chat.id`.

For a group or channel, add the bot to the destination first and use the corresponding chat ID.

## 3. Install

Python 3.11 or newer is recommended.

```bash
python -m venv .venv
source .venv/bin/activate
```

Windows PowerShell:

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
```

Install dependencies:

```bash
pip install -r requirements.txt
```

## 4. Configure

Copy `.env.example` to `.env`.

```bash
cp .env.example .env
```

Set:

- `TELEGRAM_BOT_TOKEN`
- `ALERT_CHAT_ID`

Then tune the signal thresholds.

Example:

```env
SYMBOLS=BTCUSDT,ETHUSDT,SOLUSDT,DOGEUSDT
WINDOW_SECONDS=300
MIN_PRICE_CHANGE_PCT=2.0
MIN_VOLUME_MULTIPLIER=2.5
MIN_NOTIONAL_USDT=250000
ALERT_COOLDOWN_SECONDS=900
CHART_MINUTES=30
```

For large-cap coins, 2 percent in 5 minutes is relatively selective. For volatile small-cap coins, consider increasing the percentage threshold and minimum notional to reduce noise.

## 5. Run

```bash
python bot.py
```

Telegram commands:

- `/start`
- `/status`
- `/symbols`
- `/testsignal`

## Production deployment

Run the bot on a VPS, Docker host, Railway, Render, Fly.io, or another always-on Python environment.

A lightweight Ubuntu VPS is enough for a modest symbol list.

Use a process supervisor such as `systemd` or Docker restart policies so the process restarts after crashes or server reboots.

## Recommended next upgrades

For a stronger signal engine, add:

- 1 minute OHLCV candle aggregation
- RSI and ATR
- Relative volume against the same time of day
- Order book imbalance
- Buy versus sell aggressor volume
- Open interest and funding rate for futures
- Liquidation streams
- Multi-exchange confirmation
- SQLite or PostgreSQL alert history
- Telegram inline buttons for 5 minute, 15 minute, 1 hour charts
- Per-user watchlists and thresholds
- Backtesting to calibrate false-positive rates

## Important operational detail

Exchange WebSocket sessions can disconnect, so the bot reconnects automatically with exponential backoff. Production systems should also record heartbeat, reconnect, and missed-data metrics.
