# /root/ux-solsniper/sniper.py
import logging
import asyncio
import aiohttp
import random
import os
import re
from loguru import logger
from telethon import TelegramClient
from telethon.sessions import StringSession
from config import load_config
from buy import execute_jupiter_buy
from sell import monitor_and_sell
from jupiter_price import get_mcap_and_price
from jupiter_price import get_sol_price_usd
from reports import record_buy
from reports import get_balance
from utils import sleep_with_logging
from solders.keypair import Keypair
from datetime import datetime, time, timedelta

logger = logging.getLogger(__name__)

async def get_token_balance(wallet: Keypair, token_mint: str) -> int:
    wallet_address = str(wallet.pubkey())
    url = f"https://lite-api.jup.ag/ultra/v1/holdings/{wallet_address}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=10) as resp:
                if resp.status != 200:
                    logger.warning(f"Jupiter API error {resp.status} for wallet {wallet_address}")
                    return 0
                data = await resp.json()
        if token_mint in data.get("tokens", {}):
            token_list = data["tokens"][token_mint]
            if token_list and len(token_list) > 0:
                amount_raw = token_list[0].get("amount")
                if isinstance(amount_raw, str) and amount_raw.isdigit():
                    return int(amount_raw)
                elif isinstance(amount_raw, (int, float)):
                    return int(amount_raw)
        return 0
    except Exception as e:
        logger.warning(f"Failed to fetch balance for {token_mint[:6]}...: {e}")
        return 0

# sniper.py — UPDATED compute_amount_from_usd() with COMPOUNDING
async def compute_amount_from_usd(session, config, ca=None):
    sol_price = 0.0
    for attempt in range(1, 4):
        try:
            sol_price = await get_sol_price_usd(session)
            if sol_price and sol_price > 0:
                break
        except Exception as e:
            logger.warning("Attempt %d/3: get_sol_price_usd() failed: %s", attempt, e)
        await asyncio.sleep(attempt * 1.5)

    if not sol_price or sol_price <= 0:
        logger.error("Could not fetch SOL price. Skipping buy.")
        return 0

    # === COMPOUNDING: Use current balance from reports, not fixed DAILY_CAPITAL_USD ===
    current_balance_usd = get_balance()

    # Fallback to DAILY_CAPITAL_USD only if balance is 0 (first buy or reset)
    if current_balance_usd <= 0:
        current_balance_usd = float(config.get("DAILY_CAPITAL_USD", 0.0))
        logger.info("COMPOUNDING: Starting with initial capital: $%.2f", current_balance_usd)
    else:
        logger.info("COMPOUNDING: Using current balance: $%.2f", current_balance_usd)

    # Use 80% of current balance per buy (configurable later)
    buy_usd = current_balance_usd * 0.8
    buy_fee_pct = float(config.get("BUY_FEE_PERCENT", 0.0))
    sol_equivalent = buy_usd / sol_price
    sol_after_fee = sol_equivalent * (1.0 - buy_fee_pct / 100.0)
    lamports = int(round(sol_after_fee * 1e9))

    logger.info(
        "COMPOUND BUY | Balance: $%.2f → Using: $%.2f → %.6f SOL → %d lamports",
        current_balance_usd, buy_usd, sol_after_fee, lamports
    )
    return lamports

class SniperBot:
    def __init__(self, config):
        self.config = config
        self.wallet = Keypair.from_base58_string(config["PRIVATE_KEY"])
        self.queue = asyncio.Queue()
        self.daily_buys = 0
        self.cycle = 0
        self.processed_cas = set()
        self.next_reset = None

        session_file = "/root/ux-solsniper/session_string.txt"
        if not os.path.exists(session_file):
            raise FileNotFoundError("session_string.txt not found!")
        with open(session_file, "r") as f:
            session_str = f.read().strip()

        self.client = TelegramClient(
            StringSession(session_str),
            int(config["TELEGRAM_API_ID"]),
            config["TELEGRAM_API_HASH"]
        )

    def _is_valid_solana_ca(self, ca: str) -> bool:
        return len(ca) == 44 and bool(re.match(r'^[1-9A-HJ-NP-Za-km-z]{44}$', ca))

    def extract_ca(self, message) -> str | None:
        text = getattr(message, "text", "") or message.message or ""
        full_text = text
        if message.entities:
            for entity in message.entities:
                if hasattr(entity, "url"):
                    full_text += " " + (entity.url or "")

        full_text = re.sub(r'[\u200B-\u200D\uFEFF\r\n\t]', ' ', full_text)
        full_text = re.sub(r'\s+', ' ', full_text).strip()

        if "CA:" in full_text.upper():
            match = re.search(r'CA:\s*([1-9A-HJ-NP-Za-km-z]{44})\b', full_text, re.IGNORECASE)
            if match and self._is_valid_solana_ca(match.group(1)):
                return match.group(1)

        if full_text.lower().startswith("fire"):
            rest = full_text[5:].strip()
            match = re.match(r'^([1-9A-HJ-NP-Za-km-z]{44})\b', rest)
            if match and self._is_valid_solana_ca(match.group(1)):
                return match.group(1)

        for ca in re.findall(r'\b([1-9A-HJ-NP-Za-km-z]{44})\b', full_text):
            if self._is_valid_solana_ca(ca):
                return ca
        return None

    async def start(self):
        logger.info("UX-SolSniper Bot STARTED")
        await self.client.start()
        logger.info("Connected to Telegram")
        self._schedule_next_reset()
        asyncio.create_task(self.worker())
        await self.client.run_until_disconnected()

    def _schedule_next_reset(self):
        now = datetime.now()
        midnight = datetime.combine(now.date() + timedelta(days=1), time(0, 0))
        self.next_reset = midnight
        logger.info(f"Daily reset at {midnight.strftime('%Y-%m-%d 00:00')}")

    async def worker(self):
        async with aiohttp.ClientSession() as session:
            while True:
                # DAILY LIMIT LOGIC
                if self.daily_buys >= self.config["MAX_BUYS_PER_DAY"]:
                    now = datetime.now()
                    if now >= self.next_reset:
                        self.daily_buys = 0
                        self._schedule_next_reset()
                        logger.info("Daily limit RESET")
                    else:
                        wait = (self.next_reset - now).total_seconds()
                        logger.info(f"Daily limit hit. Sleeping {wait/3600:.1f}h")
                        await asyncio.sleep(wait)
                        continue

                ca = await self.queue.get()
                logger.info(f"Processing CA: {ca}")

                info = await get_mcap_and_price(session, ca)
                if not info:
                    logger.warning(f"No price/mcap for {ca}")
                    continue

                amount = await compute_amount_from_usd(session, self.config, ca)
                if amount <= 0:
                    continue

                # EXECUTE BUY
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

                if not sig:
                    logger.error(f"BUY FAILED: {ca}")
                    continue

                logger.info(f"BOUGHT {sig[:8]}... → STARTING MONITOR")

                # MEV DELAY
                await asyncio.sleep(random.uniform(2.5, 4.0))

                entry_price = info["priceUsd"]
                sol_spent = amount / 1e9

                # RECORD BUY
                record_buy(
                    ca=ca,
                    name=f"TKN_{ca[-6:]}",
                    mcap=info["marketCap"],
                    gross=sol_spent,
                    net=sol_spent * (1 - self.config["BUY_FEE_PERCENT"] / 100),
                    fee=sol_spent * (self.config["BUY_FEE_PERCENT"] / 100),
                    tx_sig=sig
                )

                # GET REAL BALANCE
                token_balance = await get_token_balance(self.wallet, ca)
                if token_balance <= 0:
                    logger.warning(f"No tokens received: {ca}")
                    continue

                logger.info(f"Received {token_balance / 1e9:.6f} tokens")

                # START TP/SL MONITOR (INSTANT)
                asyncio.create_task(
                    monitor_and_sell(
                        ca=ca,
                        entry_price=entry_price,
                        tp_pct=self.config["TAKE_PROFIT"],
                        sl_pct=abs(float(self.config["STOP_LOSS"])),
                        wallet=self.wallet,
                        config=self.config,
                        session=session
                    )
                )

                # COUNT SUCCESS
                self.daily_buys += 1
                self.cycle += 1
                logger.info(f"SUCCESS | CA: {ca} | Buys today: {self.daily_buys} | Cycle: {self.cycle}")
