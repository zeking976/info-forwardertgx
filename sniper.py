# sniper.py
import asyncio
import aiohttp
from loguru import logger
from telethon import TelegramClient
from config import load_config
from buy import execute_jupiter_buy
from limit_order import create_jupiter_limit_order
from jupiter_price import get_mcap_and_price
from telegram import extract_ca
from reports import record_buy, record_limit_order
from utils import sleep_with_logging
from solders.keypair import Keypair
from datetime import datetime, time, timedelta


class SniperBot:
    def __init__(self, config):
        self.config = config
        self.wallet = Keypair.from_base58_string(config["PRIVATE_KEY"])
        self.queue = asyncio.Queue()
        self.daily_buys = 0
        self.cycle = 0
        self.processed_cas = set()
        self.next_reset = None  # When daily limit resets (midnight)

        session_file = f"{config['SESSION_NAME']}.session"
        self.client = TelegramClient(
            session_file,
            config["TELEGRAM_API_ID"],
            config["TELEGRAM_API_HASH"]
        )

    async def start(self):
        logger.info("UX-SolSniper Bot STARTED")
        await self.client.start()
        logger.info("Connected to Telegram")

        # Initialize reset time
        self._schedule_next_reset()

        asyncio.create_task(self.worker())
        await self.client.run_until_disconnected()

    def _schedule_next_reset(self):
        """Set next reset to midnight (00:00) of next day"""
        now = datetime.now()
        midnight = datetime.combine(now.date() + timedelta(days=1), time(0, 0))
        self.next_reset = midnight
        logger.info(f"Daily buy limit will reset at {midnight.strftime('%Y-%m-%d 00:00')}")

    async def worker(self):
        async with aiohttp.ClientSession() as session:
            while True:
                # === WAIT UNTIL MIDNIGHT IF LIMIT REACHED ===
                if self.daily_buys >= self.config["MAX_BUYS_PER_DAY"]:
                    now = datetime.now()
                    if now >= self.next_reset:
                        self.daily_buys = 0
                        self._schedule_next_reset()
                        logger.info("Daily limit RESET. Ready for new buys.")
                    else:
                        wait_sec = (self.next_reset - now).total_seconds()
                        logger.info(f"Daily limit reached. Waiting {wait_sec/3600:.1f}h until midnight...")
                        await asyncio.sleep(wait_sec)
                        continue  # Recheck

                ca = await self.queue.get()
                logger.info(f"Processing CA: {ca}")

                info = await get_mcap_and_price(session, ca)
                if not info:
                    logger.warning(f"Failed to fetch info for {ca}")
                    continue

                amount = int(self.config["DAILY_CAPITAL_USD"] * 1e9 * (1 - self.config["BUY_FEE_PERCENT"] / 100))
                sig = await execute_jupiter_buy(
                    session=session,
                    input_mint="So11111111111111111111111111111111111111112",
                    output_mint=ca,
                    amount=amount,
                    wallet=self.wallet,
                    config=self.config,
                    coin_name=f"TKN_{ca[-6:]}",
                    market_cap=info["marketCap"]
                )

                if sig:
                    target_price = info["priceUsd"] * (1 + self.config["TAKE_PROFIT"] / 100)
                    sell_sig = await create_jupiter_limit_order(
                        session=session,
                        token_mint=ca,
                        amount=amount,
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
                        logger.info(f"SUCCESS: BUY + SELL | CA: {ca} | Buys today: {self.daily_buys}")

                await sleep_with_logging(1.0, "polling")