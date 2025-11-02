# sniper.py
import asyncio
import aiohttp
import os
import re
from loguru import logger
from telethon import TelegramClient
from telethon.sessions import StringSession
from config import load_config
from buy import execute_jupiter_buy
from limit_order import create_jupiter_limit_order
from jupiter_price import get_mcap_and_price
from telegram import extract_ca
from reports import record_buy, record_limit_order, record_sell
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
        self.next_reset = None
        # === SESSION STRING ===
        session_file = "/root/ux-solsniper/session_string.txt"
        if not os.path.exists(session_file):
            raise FileNotFoundError("session_string.txt not found! Run setup first.")
        with open(session_file, "r") as f:
            session_str = f.read().strip()
        self.client = TelegramClient(
            StringSession(session_str),
            int(config["TELEGRAM_API_ID"]),
            config["TELEGRAM_API_HASH"]
        )
    def extract_ca(self, message):
        """
        Extract Solana contract address from anywhere in message.
        Supports:
        - CA: ABC...
        - `ABC...`
        - ABC... (raw)
        - Links, backticks, spaces
        """
        text = message.message.strip()
        # === 1. CA: format (any line) ===
        if "CA:" in text.upper():
            for line in text.split('\n'):
                if "CA:" in line.upper():
                    ca = line.split("CA:")[-1].strip().strip('`').strip()
                    if len(ca) == 44 and re.match(r'^[1-9A-HJ-NP-Za-km-z]{44}$', ca):
                        return ca
        # === 2. ANY 44-char Solana address (anywhere) ===
        matches = re.findall(r'[1-9A-HJ-NP-Za-km-z]{44}', text)
        if matches:
            return matches[0]  # First valid CA
        return None  # Explicit safe return
    async def start(self):
        logger.info("UX-SolSniper Bot STARTED")
        await self.client.start()
        logger.info("Connected to Telegram")
        # Schedule first midnight reset
        self._schedule_next_reset()
        # Start worker
        asyncio.create_task(self.worker())
        await self.client.run_until_disconnected()
    def _schedule_next_reset(self):
        now = datetime.now()
        midnight = datetime.combine(now.date() + timedelta(days=1), time(0, 0))
        self.next_reset = midnight
        logger.info(f"Daily buy limit resets at {midnight.strftime('%Y-%m-%d 00:00')}")
    async def monitor_jupiter_order(self, order_id: str, ca: str, entry_price: float, amount: int, is_tp: bool):
        """Poll Jupiter until order is filled → then record_sell with Solscan link"""
        url = f"https://lite-api.jup.ag/trigger/v1/order/{order_id}"
        async with aiohttp.ClientSession() as session:
            while True:
                try:
                    async with session.get(url, timeout=10) as r:
                        data = await r.json()
                    status = data.get("status")
                    if status == "filled":
                        sig = data.get("signature")
                        output_sol = int(data["outputAmount"]) / 1e9
                        profit_usd = output_sol - (amount / 1e9) * entry_price
                        profit_pct = (output_sol / ((amount / 1e9) * entry_price) - 1)
* 100
                        # === RECORD SELL WITH FULL SOLSCAN LINK ===
                        record_sell(ca, sig, profit_usd, is_tp, profit_pct)
                        logger.info(f"SELL FILLED: {'TP' if is_tp else 'SL'} | {sig[:8]}... | +${profit_usd:.2f}")
                        break
                    elif status == "cancelled":
                        logger.info(f"Order cancelled: {order_id[:8]}...")
                        break
                except Exception as e:
                    logger.debug(f"Polling order {order_id[:8]}... error: {e}")
                await asyncio.sleep(15)
    async def worker(self):
        async with aiohttp.ClientSession() as session:
            while True:
                # === WAIT UNTIL MIDNIGHT IF DAILY LIMIT HIT ===
                if self.daily_buys >= self.config["MAX_BUYS_PER_DAY"]:
                    now = datetime.now()
                    if now >= self.next_reset:
                        self.daily_buys = 0
                        self._schedule_next_reset()
                        logger.info("Daily limit RESET. Ready for new buys.")
                    else:
                        wait_sec = (self.next_reset - now).total_seconds()
                        logger.info(f"Daily limit reached. Sleeping {wait_sec/3600:.1f}h until midnight...")
                        await asyncio.sleep(wait_sec)
                        continue
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
                    entry_price = info["priceUsd"]
                    # === RECORD BUY ===
                    record_buy(
                        ca=ca,
                        name=f"TKN_{ca[-6:]}",
                        mcap=info["marketCap"],
                        gross=amount / 1e9,
                        net=amount / 1e9 * (1 - self.config["BUY_FEE_PERCENT"] / 100),
                        fee=amount / 1e9 * self.config["BUY_FEE_PERCENT"] / 100,
                        tx_sig=sig
                    )
                    # === TAKE PROFIT ORDER ===
                    tp_price = entry_price * (1 + self.config["TAKE_PROFIT"] / 100)
                    tp_result = await create_jupiter_limit_order(
                        session=session,
                        token_mint=ca,
                        amount=amount,
                        target_price=tp_price,
                        wallet=self.wallet,
                        config=self.config,
                        entry_price=entry_price,
                        take_profit_pct=self.config["TAKE_PROFIT"],
                        stop_loss_pct=0
                    )
                    # === STOP LOSS ORDER (if enabled) ===
                    sl_result = None
                    if self.config["STOP_LOSS"] != 0:
                        sl_price = entry_price * (1 - abs(self.config["STOP_LOSS"]) / 100)
                        sl_result = await create_jupiter_limit_order(
                            session=session,
                            token_mint=ca,
                            amount=amount,
                            target_price=sl_price,
                            wallet=self.wallet,
                            config=self.config,
                            entry_price=entry_price,
                            take_profit_pct=0,
                            stop_loss_pct=abs(self.config["STOP_LOSS"])
                        )
                    # === START MONITORING FOR FILLS ===
                    if tp_result and "orderId" in tp_result:
                        asyncio.create_task(self.monitor_jupiter_order(
                            order_id=tp_result["orderId"],
                            ca=ca,
                            entry_price=entry_price,
                            amount=amount,
                            is_tp=True
                        ))
                    if sl_result and "orderId" in sl_result:
                        asyncio.create_task(self.monitor_jupiter_order(
                            order_id=sl_result["orderId"],
                            ca=ca,
                            entry_price=entry_price,
                            amount=amount,
                            is_tp=False
                        ))
                    # === SUCCESS COUNT ===
                    if tp_result or sl_result:
                        self.daily_buys += 1
                        self.cycle += 1
                        logger.info(f"SUCCESS: BUY + LIMIT ORDERS | CA: {ca} | Buys today: {self.daily_buys}")
                await sleep_with_logging(1.0, "polling")