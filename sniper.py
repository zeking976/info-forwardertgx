# sniper.py
import logging
import asyncio
import aiohttp
import random
import os
import re
from solana.rpc.async_api import AsyncClient
from solders.pubkey import Pubkey
from loguru import logger
from telethon import TelegramClient
from telethon.sessions import StringSession
from config import load_config
from buy import execute_jupiter_buy
from limit_order import create_jupiter_limit_order
from jupiter_price import get_mcap_and_price
from jupiter_price import get_sol_price_usd
from telegram import extract_ca
from reports import record_buy, record_limit_order, record_sell
from utils import sleep_with_logging
from solders.keypair import Keypair
from datetime import datetime, time, timedelta
# --- compute lamports from USD using live SOL price ---
logger = logging.getLogger(__name__)
async def get_token_balance(wallet: Keypair, token_mint: str) -> int:    wallet_address = str(wallet.pubkey())
    url = f"https://lite-api.jup.ag/ultra/v1/holdings/{wallet_address}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=10) as resp:
                if resp.status != 200:
                    logger.warning(f"Jupiter API error {resp.status}
for wallet {wallet_address}")
                    return 0
                data = await resp.json()
        if token_mint in data.get("tokens", {}):
            token_list = data["tokens"][token_mint]
            if token_list and len(token_list) > 0:
                token_data = token_list[0]
                amount_raw = token_data.get("amount")
                if isinstance(amount_raw, str) and amount_raw.isdigit():
                    return int(amount_raw)
                elif isinstance(amount_raw, (int, float)):
                    return int(amount_raw)
        logger.debug(f"Token {token_mint} not found in Jupiter holdings for {wallet_address}")
        return 0
    except Exception as e:
        logger.warning(f"Failed to fetch balance from Jupiter for {token_mint}: {e}")
        return 0
async def compute_amount_from_usd(session, config, ca=None):
    """
    Convert DAILY_CAPITAL_USD in config to lamports using live SOL/USD price.
    Retries up to 3 times if price fetch fails.
    Returns integer lamports or None on failure.
    """
    sol_price = 0.0
    for attempt in range(1, 4):
        try:
            sol_price = await get_sol_price_usd(session)
            if sol_price and sol_price > 0:
                break
            logger.warning("Attempt %d/3: invalid SOL price (%s)", attempt, sol_price)
        except Exception as e:
            logger.warning("Attempt %d/3: get_sol_price_usd() failed: %s", attempt, e)
        await asyncio.sleep(attempt * 1.5)
    if not sol_price or sol_price <= 0:
        logger.error("❌  Could not fetch SOL price after 3 attempts.
Skipping buy for safety.")
    # --- compute USD → SOL → lamports ---
    usd_capital = float(config.get("DAILY_CAPITAL_USD", 0.0))
    buy_fee_pct = float(config.get("BUY_FEE_PERCENT", 0.0))
    sol_equivalent = usd_capital / sol_price
    sol_equivalent_after_fee = sol_equivalent * (1.0 - buy_fee_pct /
100.0)
    lamports = int(round(sol_equivalent_after_fee * 1e9))
    if lamports <= 0:
        logger.warning("Computed lamports <= 0 (USD=%.2f | SOL=%.8f after fee). Skipping %s",
                       usd_capital, sol_equivalent_after_fee, ca)
    logger.info("💰 Buying: $%.2f → %.6f SOL → %d lamports (after %.2f%% fee, price=%.2f USD/SOL)",
                usd_capital, sol_equivalent_after_fee, lamports, buy_fee_pct, sol_price)
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
        Extract Solana contract address from ANYWHERE in message.
        ONLY returns valid 44-char base58 (Solana standard).
        Handles:
        - fire CA...
        - CA: ...
        - Links (dexscreener, etc.)
        - Backticks, Unicode, ZWSP
        - Markdown links
        """
        import re
        # === 1. Get raw text + entities (links) ===
        text = getattr(message, "text", "") or message.message or ""
        full_text = text
        if message.entities:
            for entity in message.entities:
                if hasattr(entity, "url"):
                    url = entity.url
                    if url:
                        full_text += " " + url
        # === 2. Clean Unicode junk ===
        full_text = re.sub(r'[\u200B-\u200D\uFEFF\r\n\t]', ' ', full_text)
        full_text = re.sub(r'\s+', ' ', full_text).strip()
        # === 3. CA: format ===
        if "CA:" in full_text.upper():
            match = re.search(r'CA:\s*([1-9A-HJ-NP-Za-km-z]{44})\b',
full_text, re.IGNORECASE)
            if match:
                ca = match.group(1)
                if self._is_valid_solana_ca(ca):
                    return ca
        # === 4. fire CA... format (strict: fire + space + 44-char) ===
        if full_text.startswith("fire"):
            rest = full_text[5:].strip()  # Remove "fire "
            match = re.match(r'^([1-9A-HJ-NP-Za-km-z]{44})\b', rest)
            if match:
                ca = match.group(1)
                if self._is_valid_solana_ca(ca):
                    return ca
        # === 5. ANY 44-char in text or links ===
        matches = re.findall(r'\b([1-9A-HJ-NP-Za-km-z]{44})\b', full_text)
        for ca in matches:
            if self._is_valid_solana_ca(ca):
                return ca
        return None
    def _is_valid_solana_ca(self, ca: str) -> bool:
        """Extra validation: 44 chars, base58, starts with valid char"""
        if len(ca) != 44:
            return False
        if not re.match(r'^[1-9A-HJ-NP-Za-km-z]{44}$', ca):
            return False
        return True
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
    async def monitor_jupiter_order(self, order_id: str, ca: str, entry_price: float, token_mint: str, token_amount: int, sol_spent: float, is_tp: bool, tx_sig: str):
        """Poll Jupiter until order is filled → then record_sell with Solscan link + full P&L"""
        url = f"https://lite-api.jup.ag/trigger/v1/order/{order_id}"
        async with aiohttp.ClientSession() as session:
            while True:
                try:
                    async with session.get(url, timeout=10) as r:
                        data = await r.json()
                    status = data.get("status")
                    if status == "filled":
                        sig = data.get("signature")
                        if not sig:
                            logger.warning(f"Jupiter order {order_id[:8]}... filled but no signature")
                            break
                        sol_received = int(data["outputAmount"]) / 1e9
                        gross_profit = sol_received - sol_spent
                        net_profit = gross_profit * (1 - self.config["SELL_FEE_PERCENT"] / 100)
                        profit_pct = (gross_profit / sol_spent) * 100 if sol_spent > 0 else 0
                        # === RECORD SELL WITH SOLSCAN LINK ===
                        record_sell(
                            ca=ca,
                            tx_sig=sig,
                            gross=sol_received,
                            net=sol_received * (1 - self.config["SELL_FEE_PERCENT"] / 100),
                            fee=sol_received * (self.config["SELL_FEE_PERCENT"] / 100),
                            pnl=gross_profit,
                            pnl_pct=profit_pct,
                            is_tp=is_tp,
                            buy_tx_sig=tx_sig,
                            solscan_link=f"https://solscan.io/tx/{sig}"
                        )
                        logger.info(
                            f"{'TAKE PROFIT' if is_tp else 'STOP LOSS'} FILLED | "
                            f"{sig[:8]}... | "
                            f"+{gross_profit:.4f} SOL | "
                            f"+{profit_pct:.1f}% | "
                            f"https://solscan.io/tx/{sig}"
                        )
                        break
                    elif status == "cancelled":
                        logger.info(f"Order cancelled: {order_id[:8]}... | CA: {ca}")
                        break
                    elif status == "expired":
                        logger.info(f"Order expired: {order_id[:8]}... | CA: {ca}")
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
                amount = await compute_amount_from_usd(session, self.config, ca)
                # === EXECUTE BUY ===
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
                    logger.error(f"Buy failed for {ca}")
                    continue
                logger.info(f"BUY submitted: {sig}")
                # === MEV PROTECTION DELAY ===
                delay = random.uniform(3, 5)
                logger.info(f"MEV delay {delay:.2f}s before limit orders...")
                await asyncio.sleep(delay)
                entry_price = info["priceUsd"]
                sol_spent = amount / 1e9
                # === RECORD BUY ===
                record_buy(
                    ca=ca,
                    name=f"TKN_{ca[-6:]}",
                    mcap=info["marketCap"],
                    gross=sol_spent,
                    net=sol_spent * (1 - self.config["BUY_FEE_PERCENT"] / 100),
                    fee=sol_spent * (self.config["BUY_FEE_PERCENT"] / 100),
                    tx_sig=sig
                )
                # === GET TOKEN BALANCE AFTER BUY ===
                token_balance = await get_token_balance(self.wallet,
ca)
                if token_balance <= 0:
                    logger.warning(f"No tokens received after buy for {ca}")
                    continue
                logger.info(f"Received {token_balance / 1e9:.6f} tokens")
                # === TAKE PROFIT ORDER ===
                tp_price = entry_price * (1 + self.config["TAKE_PROFIT"] / 100)
                tp_result = await create_jupiter_limit_order(
                    session=session,
                    token_mint=ca,
                    token_amount=token_balance,
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
                        token_amount=token_balance,
                        target_price=sl_price,
                        wallet=self.wallet,
                        config=self.config,
                        entry_price=entry_price,
                        take_profit_pct=0,
                        stop_loss_pct=abs(self.config["STOP_LOSS"])
                    )
                # === MONITOR ORDERS ===
                if tp_result and "orderId" in tp_result:
                    asyncio.create_task(self.monitor_jupiter_order(
                        order_id=tp_result["orderId"],
                        ca=ca,
                        entry_price=entry_price,
                        token_amount=token_balance,
                        sol_spent=sol_spent,
                        is_tp=True,
                        tx_sig=sig
                    ))
                if sl_result and "orderId" in sl_result:
                    asyncio.create_task(self.monitor_jupiter_order(
                        order_id=sl_result["orderId"],
                        ca=ca,
                        entry_price=entry_price,
                        token_amount=token_balance,
                        sol_spent=sol_spent,
                        is_tp=False,
                        tx_sig=sig
                    ))
                # === SUCCESS COUNT ===
                if tp_result or sl_result:
                    self.daily_buys += 1
                    self.cycle += 1
                    logger.info(f"SUCCESS: BUY + LIMIT ORDERS | CA: {ca} | Buys today: {self.daily_buys}")
                await sleep_with_logging(3.0, "polling")