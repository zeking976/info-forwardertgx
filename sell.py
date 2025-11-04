# /root/ux-solsniper/sell.py
import asyncio
import aiohttp
import base64
import logging
from loguru import logger
from solders.transaction import VersionedTransaction
from solders.keypair import Keypair
from solders.message import to_bytes_versioned
from reports import record_sell
from utils import sleep_with_logging

ORDER_URL = "https://lite-api.jup.ag/ultra/v1/order"
EXEC_URL = "https://lite-api.jup.ag/ultra/v1/execute"
PRICE_API = "https://price.jup.ag/v1/price"
HOLDINGS_URL_TEMPLATE = "https://lite-api.jup.ag/ultra/v1/holdings/{}"

async def get_token_balance(wallet: Keypair, token_mint: str) -> int:
    wallet_address = str(wallet.pubkey())
    url = HOLDINGS_URL_TEMPLATE.format(wallet_address)
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=10) as resp:
                if resp.status != 200:
                    logger.warning(f"Jupiter holdings API error {resp.status}")
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

async def get_token_price(mint: str) -> float:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{PRICE_API}?ids={mint}") as r:
                data = await r.json()
                return float(data["data"][mint]["price"])
    except Exception as e:
        logger.debug(f"Price fetch failed: {e}")
        return 0.0

async def execute_ultra_sell(
    session: aiohttp.ClientSession,
    token_mint: str,
    token_amount: int,
    wallet: Keypair,
    config: dict
) -> str | None:
    params = {
        "inputMint": token_mint,
        "outputMint": "So11111111111111111111111111111111111111112",
        "amount": str(token_amount),
        "taker": str(wallet.pubkey()),
        "payer": str(wallet.pubkey()),
        "closeAuthority": str(wallet.pubkey()),
    }
    if config.get("REFERRAL_ACCOUNT"):
        params.update({
            "referralAccount": config["REFERRAL_ACCOUNT"],
            "referralFee": config["REFERRAL_FEE_BPS"]
        })

    for attempt in range(1, 4):
        try:
            async with session.get(ORDER_URL, params=params, timeout=15) as r:
                if not r.ok:
                    continue
                order = await r.json()
                if not order.get("transaction"):
                    continue

            tx_bytes = base64.b64decode(order["transaction"])
            tx = VersionedTransaction.from_bytes(tx_bytes)
            signed_tx_obj = VersionedTransaction.populate(
                tx.message,
                [wallet.sign_message(to_bytes_versioned(tx.message))]
            )
            signed_tx = base64.b64encode(bytes(signed_tx_obj)).decode()

            payload = {
                "signedTransaction": signed_tx,
                "requestId": order.get("requestId", "")
            }

            async with session.post(EXEC_URL, json=payload, timeout=20) as resp:
                if not resp.ok:
                    continue
                res = await resp.json()

            if res.get("status", "").lower() == "success":
                sig = res.get("signature") or res.get("txid")
                logger.info(f"SELL SUCCESS | {token_mint[:6]}... | Sig: {sig[:8]}...")
                return sig
        except Exception as e:
            logger.debug(f"Sell retry {attempt}/3: {e}")
            await asyncio.sleep(attempt * 0.5)
    return None

async def monitor_and_sell(
    ca: str,
    entry_price: float,
    tp_pct: float,
    sl_pct: float,
    wallet: Keypair,
    config: dict
):
    tp_price = entry_price * (1 + tp_pct / 100)
    sl_price = entry_price * (1 - sl_pct / 100)
    sold = False
    log_counter = 0

    logger.info(f"MONITOR STARTED | {ca[:6]}... | Entry: ${entry_price:.8f} | TP: ${tp_price:.8f} | SL: ${sl_price:.8f}")

    while not sold:
        # === POLL PRICE EVERY 1 SECOND ===
        price = await get_token_price(ca)
        if price <= 0:
            await asyncio.sleep(1)
            continue

        # === GET REAL BALANCE ===
        current_balance = await get_token_balance(wallet, ca)
        if current_balance == 0:
            logger.warning(f"ZERO BALANCE | {ca[:6]}... | Stopping monitor")
            break

        # === LOG ONLY EVERY 5 SECONDS ===
        log_counter += 1
        if log_counter >= 5:
            logger.info(
                f"POLLER | {ca[:6]}... | "
                f"Price: ${price:.8f} | "
                f"Balance: {current_balance / 1e9:.6f} | "
                f"TP: ${tp_price:.8f} | SL: ${sl_price:.8f}"
            )
            log_counter = 0

        # === CHECK TP / SL ===
        async with aiohttp.ClientSession() as session:
            if price >= tp_price:
                logger.info(f"TP HIT @ ${price:.8f}")
                sig = await execute_ultra_sell(session, ca, current_balance, wallet, config)
                if sig:
                    profit_usd = (price - entry_price) * (current_balance / 1e9)
                    profit_pct = (price / entry_price - 1) * 100
                    record_sell(ca, sig, profit_usd, True, profit_pct)
                    sold = True

            elif price <= sl_price:
                logger.info(f"SL HIT @ ${price:.8f}")
                sig = await execute_ultra_sell(session, ca, current_balance, wallet, config)
                if sig:
                    profit_usd = (price - entry_price) * (current_balance / 1e9)
                    profit_pct = (price / entry_price - 1) * 100
                    record_sell(ca, sig, profit_usd, False, profit_pct)
                    sold = True

        if not sold:
            await asyncio.sleep(1)  # POLL EVERY 1 SECOND

    logger.info(f"MONITOR EXITED | {ca[:6]}... | {'SOLD' if sold else 'NO BALANCE'}")