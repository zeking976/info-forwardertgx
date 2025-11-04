# sell_.py
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

async def get_token_price(mint: str) -> float:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{PRICE_API}?ids={mint}") as r:
                data = await r.json()
                return float(data["data"][mint]["price"])
    except Exception as e:
        logger.warning(f"Price fetch failed for {mint[:6]}...: {e}")
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
                    logger.error(f"/order HTTP {r.status}")
                    continue
                order = await r.json()
                if not order.get("transaction"):
                    logger.error(f"No tx in order: {order}")
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
                    logger.error(f"/execute HTTP {resp.status}")
                    continue
                res = await resp.json()

            if res.get("status", "").lower() == "success":
                sig = res.get("signature") or res.get("txid")
                logger.info(f"SELL EXECUTED | Sig: {sig}")
                return sig
        except Exception as e:
            logger.warning(f"Sell attempt {attempt}/3 failed: {e}")
            await asyncio.sleep(attempt * 2)
    return None

async def monitor_and_sell(
    ca: str,
    amount: int,
    entry_price: float,
    tp_pct: float,
    sl_pct: float,
    wallet: Keypair,
    config: dict
):
    tp_price = entry_price * (1 + tp_pct / 100)
    sl_price = entry_price * (1 - sl_pct / 100)
    sold = False

    while not sold:
        price = await get_token_price(ca)
        if price <= 0:
            await sleep_with_logging(1, f"Price error for {ca[:6]}...")
            continue

        logger.info(
            f"POLLER | {ca[:6]}... | Price: ${price:.8f} | "
            f"TP: ${tp_price:.8f} | SL: ${sl_price:.8f}"
        )

        async with aiohttp.ClientSession() as session:
            if price >= tp_price:
                logger.info(f"TP HIT @ ${price:.8f}")
                sig = await execute_ultra_sell(session, ca, amount, wallet, config)
                if sig:
                    profit_usd = (price - entry_price) * (amount / 1e9)
                    profit_pct = (price / entry_price - 1) * 100
                    record_sell(ca, sig, profit_usd, True, profit_pct)
                    sold = True

            elif price <= sl_price:
                logger.info(f"SL HIT @ ${price:.8f}")
                sig = await execute_ultra_sell(session, ca, amount, wallet, config)
                if sig:
                    profit_usd = (price - entry_price) * (amount / 1e9)
                    profit_pct = (price / entry_price - 1) * 100
                    record_sell(ca, sig, profit_usd, False, profit_pct)
                    sold = True

            if not sold:
                await sleep_with_logging(1, "Waiting for TP/SL...")

    logger.info(f"MONITOR EXITED🏃‍♂️ | {ca[:6]}... SOLD")