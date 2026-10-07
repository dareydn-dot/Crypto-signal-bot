import asyncio
import io
import logging
import os
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Deque, Dict

import aiohttp
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

load_dotenv()

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
ALERT_CHAT_ID = int(os.environ["ALERT_CHAT_ID"])

SYMBOLS = [s.strip().upper() for s in os.getenv(
    "SYMBOLS", "BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,XRPUSDT"
).split(",") if s.strip()]

WINDOW_SECONDS = int(os.getenv("WINDOW_SECONDS", "300"))
MIN_PRICE_CHANGE_PCT = float(os.getenv("MIN_PRICE_CHANGE_PCT", "2.0"))
MIN_VOLUME_MULTIPLIER = float(os.getenv("MIN_VOLUME_MULTIPLIER", "2.5"))
MIN_NOTIONAL_USDT = float(os.getenv("MIN_NOTIONAL_USDT", "250000"))
ALERT_COOLDOWN_SECONDS = int(os.getenv("ALERT_COOLDOWN_SECONDS", "900"))
CHART_MINUTES = int(os.getenv("CHART_MINUTES", "30"))

BINANCE_WS = "wss://stream.binance.com:9443/stream"
BINANCE_REST = "https://api.binance.com"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
log = logging.getLogger("crypto-signal-bot")


@dataclass
class Tick:
    ts: float
    price: float
    quote_volume: float


@dataclass
class Signal:
    symbol: str
    direction: str
    price: float
    change_pct: float
    recent_notional: float
    volume_multiplier: float
    score: float


class MarketState:
    def __init__(self):
        self.ticks: Dict[str, Deque[Tick]] = defaultdict(lambda: deque(maxlen=5000))
        self.last_alert: Dict[str, float] = defaultdict(float)

    def add(self, symbol: str, tick: Tick):
        q = self.ticks[symbol]
        q.append(tick)
        cutoff = tick.ts - max(WINDOW_SECONDS * 6, CHART_MINUTES * 60)
        while q and q[0].ts < cutoff:
            q.popleft()

    def evaluate(self, symbol: str) -> Signal | None:
        q = self.ticks[symbol]
        if len(q) < 20:
            return None

        now = q[-1].ts
        recent_cutoff = now - WINDOW_SECONDS
        baseline_cutoff = now - WINDOW_SECONDS * 5

        recent = [x for x in q if x.ts >= recent_cutoff]
        baseline = [x for x in q if baseline_cutoff <= x.ts < recent_cutoff]

        if len(recent) < 5 or len(baseline) < 10:
            return None

        start_price = recent[0].price
        end_price = recent[-1].price
        change_pct = ((end_price / start_price) - 1.0) * 100.0

        recent_notional = sum(x.quote_volume for x in recent)

        baseline_duration = max(WINDOW_SECONDS * 4, 1)
        baseline_rate = sum(x.quote_volume for x in baseline) / baseline_duration
        recent_rate = recent_notional / max(WINDOW_SECONDS, 1)

        if baseline_rate <= 0:
            return None

        volume_multiplier = recent_rate / baseline_rate

        if abs(change_pct) < MIN_PRICE_CHANGE_PCT:
            return None
        if volume_multiplier < MIN_VOLUME_MULTIPLIER:
            return None
        if recent_notional < MIN_NOTIONAL_USDT:
            return None
        if time.time() - self.last_alert[symbol] < ALERT_COOLDOWN_SECONDS:
            return None

        direction = "PUMP" if change_pct > 0 else "DUMP"

        # Simple composite score. This is an alert-ranking heuristic, not a trading model.
        score = min(
            100.0,
            abs(change_pct) * 12.0
            + min(volume_multiplier, 10.0) * 5.0
            + min(recent_notional / 1_000_000, 10.0) * 2.0
        )

        self.last_alert[symbol] = time.time()

        return Signal(
            symbol=symbol,
            direction=direction,
            price=end_price,
            change_pct=change_pct,
            recent_notional=recent_notional,
            volume_multiplier=volume_multiplier,
            score=score,
        )


state = MarketState()


def stream_url() -> str:
    streams = [f"{s.lower()}@aggTrade" for s in SYMBOLS]
    return BINANCE_WS + "?streams=" + "/".join(streams)


def make_chart(symbol: str, signal: Signal) -> io.BytesIO:
    q = state.ticks[symbol]
    cutoff = q[-1].ts - CHART_MINUTES * 60
    data = [x for x in q if x.ts >= cutoff]

    times = [(x.ts - data[0].ts) / 60 for x in data]
    prices = [x.price for x in data]

    fig, ax = plt.subplots(figsize=(10, 5))
    ax.plot(times, prices, linewidth=1.8)
    ax.set_title(
        f"{symbol} {signal.direction} | "
        f"{signal.change_pct:+.2f}% in {WINDOW_SECONDS // 60}m | "
        f"Vol x{signal.volume_multiplier:.2f}"
    )
    ax.set_xlabel("Minutes")
    ax.set_ylabel("Price")
    ax.grid(alpha=0.25)
    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150)
    plt.close(fig)
    buf.seek(0)
    buf.name = f"{symbol}_{signal.direction.lower()}.png"
    return buf


async def send_signal(application: Application, signal: Signal):
    chart = make_chart(signal.symbol, signal)
    caption = (
        f"🚨 {signal.direction} SIGNAL\n\n"
        f"Pair: {signal.symbol}\n"
        f"Price: {signal.price:,.8f}\n"
        f"Move: {signal.change_pct:+.2f}% / {WINDOW_SECONDS // 60} min\n"
        f"Volume acceleration: {signal.volume_multiplier:.2f}x\n"
        f"Recent notional: ${signal.recent_notional:,.0f}\n"
        f"Signal score: {signal.score:.0f}/100\n\n"
        f"Thresholds: ±{MIN_PRICE_CHANGE_PCT:.2f}%, "
        f"{MIN_VOLUME_MULTIPLIER:.2f}x volume, "
        f"${MIN_NOTIONAL_USDT:,.0f} notional.\n\n"
        f"Market alert only. This is not financial advice."
    )
    await application.bot.send_photo(
        chat_id=ALERT_CHAT_ID,
        photo=chart,
        caption=caption,
    )


async def websocket_worker(application: Application):
    url = stream_url()
    retry = 1

    while True:
        try:
            log.info("Connecting to Binance stream for %s", ", ".join(SYMBOLS))
            timeout = aiohttp.ClientTimeout(total=None, sock_read=90)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.ws_connect(url, heartbeat=30) as ws:
                    retry = 1
                    log.info("Connected.")
                    async for msg in ws:
                        if msg.type != aiohttp.WSMsgType.TEXT:
                            continue

                        payload = msg.json()
                        data = payload.get("data", payload)

                        if data.get("e") != "aggTrade":
                            continue

                        symbol = data["s"]
                        price = float(data["p"])
                        qty = float(data["q"])
                        quote_volume = price * qty
                        ts = float(data["T"]) / 1000.0

                        state.add(
                            symbol,
                            Tick(
                                ts=ts,
                                price=price,
                                quote_volume=quote_volume,
                            ),
                        )

                        signal = state.evaluate(symbol)
                        if signal:
                            log.info(
                                "%s %s %.2f%% vol x%.2f",
                                signal.symbol,
                                signal.direction,
                                signal.change_pct,
                                signal.volume_multiplier,
                            )
                            try:
                                await send_signal(application, signal)
                            except Exception:
                                log.exception("Failed to send Telegram alert")

        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("WebSocket disconnected. Reconnecting.")
            await asyncio.sleep(retry)
            retry = min(retry * 2, 30)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Crypto Signal Bot is online.\n\n"
        "Commands:\n"
        "/status - show configuration\n"
        "/symbols - show tracked pairs\n"
        "/testsignal - send a test message"
    )


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"Tracking {len(SYMBOLS)} pairs.\n"
        f"Window: {WINDOW_SECONDS // 60} min\n"
        f"Price trigger: ±{MIN_PRICE_CHANGE_PCT:.2f}%\n"
        f"Volume trigger: {MIN_VOLUME_MULTIPLIER:.2f}x\n"
        f"Min notional: ${MIN_NOTIONAL_USDT:,.0f}\n"
        f"Cooldown: {ALERT_COOLDOWN_SECONDS // 60} min"
    )


async def symbols(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(", ".join(SYMBOLS))


async def testsignal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "✅ Test signal. Telegram delivery is working."
    )


async def post_init(application: Application):
    application.create_task(websocket_worker(application))


def main():
    app = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("symbols", symbols))
    app.add_handler(CommandHandler("testsignal", testsignal))

    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
