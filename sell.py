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
from jupiter_price import get_token_price

ORDER_URL = "https://lite-api.jup.ag/ultra/v1/order"
EXEC_URL = "https://lite-api.jup.ag/ultra/v1/execute"

# sell.py — FINAL VERSION
async def monitor_and_sell(
    ca: str,
    entry_price: float,
    tp_pct: float,
    sl_pct: float,
    wallet: Keypair,
    config: dict,
    session: aiohttp.ClientSession
):
    tp_price = entry_price * (1 + tp_pct / 100)
    sl_price = entry_price * (1 - sl_pct / 100)
    sold = False
    logger.info(f"MONITOR STARTED | {ca[:6]}... | Entry ${entry_price:.8f} | TP ${tp_price:.8f} | SL ${sl_price:.8f}")

    while not sold:
        price = await get_token_price(ca, session)
        if not price or price <= 0:
            logger.debug(f"Price invalid ({price}) → retry")
            await asyncio.sleep(1)
            continue

        # === TP / SL CHECK ===
        if price >= tp_price:
            logger.info(f"TP HIT @ ${price:.8f}")
            sig = await execute_ultra_sell(session, ca, wallet, config)
            if sig:
                # Balance fetched inside execute_ultra_sell()
                profit_usd = (price - entry_price) * (await get_token_balance(wallet, ca, session))[0]
                record_sell(ca, sig, profit_usd, True, (price/entry_price-1)*100)
                sold = True
            break

        if price <= sl_price:
            logger.info(f"SL HIT @ ${price:.8f}")
            sig = await execute_ultra_sell(session, ca, wallet, config)
            if sig:
                profit_usd = (price - entry_price) * (await get_token_balance(wallet, ca, session))[0]
                record_sell(ca, sig, profit_usd, False, (price/entry_price-1)*100)
                sold = True
            break

        await asyncio.sleep(1)

    logger.info(f"MONITOR ENDED | {ca[:6]}... | {'SOLD' if sold else 'NO BALANCE'}")

async def execute_ultra_sell(
    session: aiohttp.ClientSession,
    token_mint: str,
    wallet: Keypair,
    config: dict
) -> str | None:
    # GET REAL BALANCE RIGHT BEFORE SELLING
    token_amount, decimals = await get_token_balance(wallet, token_mint, session)
    if token_amount <= 0:
        logger.warning(f"NO BALANCE TO SELL | {token_mint[:6]}...")
        return None

    logger.info(f"SELL ATTEMPT STARTED | {token_mint[:6]}... | Amount: {token_amount / 1e9:.6f} tokens")

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
                    logger.warning(f"Order failed | HTTP {r.status}")
                    continue
                order = await r.json()
                if not order.get("transaction"):
                    logger.warning("No transaction in order")
                    continue

            logger.info("Signing transaction...")
            tx = VersionedTransaction.from_bytes(base64.b64decode(order["transaction"]))
            signed_tx_obj = VersionedTransaction.populate(
                tx.message,
                [wallet.sign_message(to_bytes_versioned(tx.message))]
            )
            signed_tx = base64.b64encode(bytes(signed_tx_obj)).decode()

            payload = {
                "signedTransaction": signed_tx,
                "requestId": order.get("requestId", "")
            }

            logger.info("Executing sell...")
            async with session.post(EXEC_URL, json=payload, timeout=20) as resp:
                if not resp.ok:
                    logger.warning(f"Execute failed | HTTP {resp.status}")
                    continue
                res = await resp.json()

            if res.get("status", "").lower() == "success":
                sig = res.get("signature") or res.get("txid")
                logger.info(f"SELL SUCCESS | {token_mint[:6]}... | Sig: {sig[:8]}... | https://solscan.io/tx/{sig}")
                return sig
            else:
                logger.warning(f"Execute failed | Response: {res}")

        except Exception as e:
            logger.debug(f"Sell retry {attempt}/3 failed: {e}")
            await asyncio.sleep(attempt * 0.5)

    logger.error("SELL FAILED AFTER 3 ATTEMPTS")
    return None
