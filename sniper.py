# sniper.py
import asyncio
import aiohttp
from loguru import logger
from telethon import TelegramClient, events
from config import load_config
from buy import execute_jupiter_buy
from limit_order import create_jupiter_limit_order
from jupiter_price import get_mcap_and_price
from filters import passes_filters
from telegram import extract_ca
from reports import record_buy, record_limit_order
from utils import sleep_with_logging
from solders.keypair import Keypair


class SniperBot:
    def __init__(self, config):
        self.config = config
        self.wallet = Keypair.from_base58_string(config["PRIVATE_KEY"])
        self.queue = asyncio.Queue()
        self.daily_buys = 0
        self.cycle = 0
        self.processed_cas = set()

        # === TELETHON CLIENT WITH SESSION FILE ===
        session_file = f"{config['SESSION_NAME']}.session"
        self.client = TelegramClient(
            session_file,
            config["TELEGRAM_API_ID"],
            config["TELEGRAM_API_HASH"]
        )

    async def start(self):
        logger.info("UX-SolSniper Bot STARTED")

        # === EVENT HANDLER: LISTEN TO TARGET CHANNEL ===
        @self.client.on(events.NewMessage(chats=self.config["TARGET_CHANNEL_ID"]))
        async def on_message(event):
            text = event.message.message or ""
            if not text.lstrip().startswith("🔥"): return
            if any(emoji in text for emoji in ("💰", "🏆", "📈")): return
            if ca := extract_ca(event.message):
                if ca not in self.processed_cas:
                    self.processed_cas.add(ca)
                    await self.queue.put(ca)
                    logger.info(f"New CA enqueued: {ca}")

        await self.client.start()
        logger.info("Connected to Telegram")

        # Start worker
        asyncio.create_task(self.worker())
        await self.client.run_until_disconnected()

    async def worker(self):
        async with aiohttp.ClientSession() as session:
            while True:
                # Daily buy limit reset
                if self.daily_buys >= self.config["MAX_BUYS_PER_DAY"]:
                    logger.info("Daily buy limit reached. Sleeping 1h...")
                    await asyncio.sleep(3600)
                    self.daily_buys = 0

                ca = await self.queue.get()
                logger.info(f"Processing CA: {ca}")

                info = await get_mcap_and_price(session, ca)
                if not await passes_filters(info, self.config):
                    logger.info(f"CA {ca} failed filters")
                    continue

                # === BUY ===
                order_amount_lamports = int(self.config["DAILY_CAPITAL_USD"] * 1e9 * (1 - self.config["BUY_FEE_PERCENT"] / 100))
                sig = await execute_jupiter_buy(
                    session=session,
                    input_mint="So11111111111111111111111111111111111111112",
                    output_mint=ca,
                    amount=order_amount_lamports,
                    wallet=self.wallet,
                    config=self.config,
                    coin_name=f"TKN_{ca[-6:]}",
                    market_cap=info["marketCap"]
                )

                if sig:
                    target_price = info["priceUsd"] * (1 + self.config["TAKE_PROFIT"] / 100)

                    # === SELL LIMIT ORDER + RECORD ===
                    sell_sig = await create_jupiter_limit_order(
                        session=session,
                        token_mint=ca,
                        amount=order_amount_lamports,
                        target_price=target_price,
                        wallet=self.wallet,
                        config=self.config,
                        entry_price=info["priceUsd"],
                        take_profit_pct=self.config["TAKE_PROFIT"],
                        stop_loss_pct=abs(self.config["STOP_LOSS"])
                    )

                    if sell_sig:
                        self.daily_buys += 1
                        self.cycle += 1
                        logger.info(f"SUCCESS: BUY + SELL ORDER | CA: {ca} | Cycle: {self.cycle}")

                await sleep_with_logging(1.0, "1s polling")