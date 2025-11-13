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
from jupiter_price import get_token_balance

ORDER_URL = "https://lite-api.jup.ag/ultra/v1/order"
EXEC_URL = "https://lite-api.jup.ag/ultra/v1/execute"

async def monitor_and_sell(
    ca: str,
    entry_price: float,
    tp_pct: float,
    sl_pct: float,
    wallet: Keypair,
    config: dict,
    token_name,
    session: aiohttp.ClientSession
):
    tp_price = entry_price * (1 + tp_pct / 100)
    sl_price = entry_price * (1 - sl_pct / 100)
    sold = False
    log_counter = 0

    logger.info(f"MONITOR STARTED | {ca[:6]}... | Entry ${entry_price:.8f} | TP ${tp_price:.8f} | SL ${sl_price:.8f}")

    while not sold:
        price = await get_token_price(ca, session)
        if not price or price <= 0:
            logger.debug(f"Price invalid ({price}) → retry")
            await asyncio.sleep(1)
            continue
        # === POLLING LOG (NO token_amount) ===
        log_counter += 1
        if log_counter >= 1:
            logger.info(f"POLLER | {ca[:6]}... | ${price:.8f}")
            log_counter = 0

        # === TP HIT ===
        if price >= tp_price:
            logger.info(f"TP HIT @ ${price:.8f}")
            sig = await execute_ultra_sell(
                session, ca, wallet, config,
                current_price=price,
                token_name=token_name,
                entry_price=entry_price,
                is_tp=True
            )
            if sig:
                sold = True
            break

        # === SL HIT ===
        if price <= sl_price:
            logger.info(f"SL HIT @ ${price:.8f}")
            sig = await execute_ultra_sell(
                session, ca, wallet, config,
                current_price=price,
                token_name=token_name,
                entry_price=entry_price,
                is_tp=False
            )
            if sig:
                sold = True
            break
        await asyncio.sleep(1)
    logger.info(f"MONITOR ENDED | {ca[:6]}... | {'SOLD' if sold else 'NO BALANCE'}")

async def execute_ultra_sell(
    session: aiohttp.ClientSession,
    token_mint: str,
    wallet: Keypair,
    config: dict,
    current_price: float,
    entry_price: float,
    token_name: str,
    is_tp: bool
) -> str | None:
    # === GET BALANCE ONCE ===
    logger.debug(f"DEBUG | Fetching balance for {token_mint}")
    token_amount, _ = await get_token_balance(wallet, token_mint, session)
    if token_amount <= 0:
        logger.warning(f"NO BALANCE TO SELL | {token_mint[:6]}...")
        return None

    lamports = int(token_amount * 1_000_000)  # assuming 6 decimals
    if lamports < 100_000:
        logger.warning(f"TOO SMALL: {lamports:,} lamports → SKIP")
        return None

    logger.info(f"SELL STARTED | {token_mint[:6]}... | {token_amount:,.2f} tokens ({lamports:,} lamports)")

    # === BUILD ORDER ===
    params = {
        "inputMint": token_mint,
        "outputMint": "So11111111111111111111111111111111111111112",
        "amount": str(lamports),
        "taker": str(wallet.pubkey()),
        "payer": str(wallet.pubkey()),
        "closeAuthority": str(wallet.pubkey()),
    }

    if config.get("REFERRAL_ACCOUNT"):
        params.update({
            "referralAccount": config["REFERRAL_ACCOUNT"],
            "referralFee": config["REFERRAL_FEE_BPS"]
        })

    logger.debug(f"DEBUG | Order params: {params}")

    for attempt in range(1, 4):
        try:
            logger.debug(f"DEBUG | Attempt {attempt}/3 → GET {ORDER_URL}")
            async with session.get(ORDER_URL, params=params, timeout=15) as r:
                logger.debug(f"DEBUG | Order response status: {r.status}")
                if not r.ok:
                    text = await r.text()
                    logger.warning(f"Order failed | HTTP {r.status} | {text[:200]}")
                    continue

                order = await r.json()
                logger.debug(f"DEBUG | Order response: {order}")

                if not order.get("transaction"):
                    logger.warning("No transaction in order")
                    continue

            logger.debug("DEBUG | Deserializing transaction...")
            tx = VersionedTransaction.from_bytes(base64.b64decode(order["transaction"]))
            signed_tx_obj = VersionedTransaction.populate(
                tx.message,
                [wallet.sign_message(to_bytes_versioned(tx.message))]
            )
            signed_tx = base64.b64encode(bytes(signed_tx_obj)).decode()
            logger.debug(f"DEBUG | Signed tx length: {len(signed_tx)}")

            payload = {
                "signedTransaction": signed_tx,
                "requestId": order.get("requestId", "")
            }
            logger.debug(f"DEBUG | Execute payload: requestId={payload['requestId']}")

            logger.debug(f"DEBUG | POST → {EXEC_URL}")
            async with session.post(EXEC_URL, json=payload, timeout=20) as resp:
                logger.debug(f"DEBUG | Execute response status: {resp.status}")
                if not resp.ok:
                    text = await resp.text()
                    logger.warning(f"Exec failed | HTTP {resp.status} | {text[:200]}")
                    continue

                res = await resp.json()
                logger.debug(f"DEBUG | Execute response: {res}")

            if res.get("status", "").lower() == "success":
                sig = res.get("signature") or res.get("txid")
                if sig:
                    profit_usd = (current_price - entry_price) * token_amount
                    profit_pct = (current_price / entry_price - 1) * 100
                    logger.debug(f"DEBUG | Profit calc: ${profit_usd:,.2f} | {profit_pct:+.2f}%")
                    record_sell(
                        ca=token_mint,
                        signature=sig,
                        profit_usd=profit_usd,
                        is_tp=is_tp,
                        name=token_name,
                        profit_pct=profit_pct
                    )
                    logger.info(f"SELL SUCCESS | {token_mint[:6]}... | Sig: {sig[:8]}... | Profit: ${profit_usd:,.2f}")
                    return sig
                else:
                    logger.warning("No signature in success response")
            else:
                logger.warning(f"Execute failed | status: {res.get('status')} | msg: {res.get('error')}")

        except Exception as e:
            logger.warning(f"Sell attempt {attempt}/3 failed: {type(e).__name__}: {e}")
            await asyncio.sleep(attempt * 0.5)

    logger.error("SELL FAILED AFTER 3 ATTEMPTS")
    return None
