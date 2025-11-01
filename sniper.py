import asyncio
import aiohttp
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
        self.client = TelegramClient(config["SESSION_NAME"], config["TELEGRAM_API_ID"], config["TELEGRAM_API_HASH"])
        self.queue = asyncio.Queue()
        self.daily_buys = 0
        self.cycle = 0
        self.wallet = Keypair.from_base58_string(config["PRIVATE_KEY"])  # Load once

    async def start(self):
        self.client.add_event_handler(self.on_message, events.NewMessage(chats=self.config["TARGET_CHANNEL_ID"]))
        await self.client.start()
        asyncio.create_task(self.worker())
        await self.client.run_until_disconnected()

    async def on_message(self, event):
        text = event.raw_text or ""
        if not text.lstrip().startswith("🔥"): return
        if any(emoji in text for emoji in ("💰", "🏆", "📈")): return
        if ca := extract_ca(event.message):
            if ca not in await self.get_processed():
                await self.queue.put(ca)

    async def get_processed(self):
        # Optional: load from file or cache
        return set()

    async def worker(self):
        async with aiohttp.ClientSession() as session:
            while True:
                # Daily reset
                if self.daily_buys >= self.config["MAX_BUYS_PER_DAY"]:
                    await asyncio.sleep(3600)
                    self.daily_buys = 0

                ca = await self.queue.get()
                info = await get_mcap_and_price(session, ca)
                if not await passes_filters(info, self.config):
                    continue

                # === ULTRA API: NO QUOTE, DIRECT ORDER ===
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
                    
                    # === CREATE LIMIT ORDER + RECORD + SEND TELEGRAM ===
                    await create_jupiter_limit_order(
                        session=session,
                        token_mint=ca,
                        amount=order_amount_lamports,
                        target_price=target_price,
                        wallet_pubkey=str(self.wallet.pubkey()),
                        config=self.config
                    )

                    await record_limit_order(
                        ca=ca,
                        price=target_price,
                        amount=order_amount_lamports,
                        entry_price=info["priceUsd"],
                        take_profit_pct=self.config["TAKE_PROFIT"],
                        stop_loss_pct=abs(self.config["STOP_LOSS"])
                    )

                    self.daily_buys += 1
                    self.cycle += 1

                await sleep_with_logging(1.0, "1s polling")