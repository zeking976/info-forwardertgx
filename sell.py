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

async def get_token_balance(wallet: Keypair, token_mint: str) -> int:
    wallet_address = str(wallet.pubkey())
    url = f"https://lite-api.jup.ag/ultra/v1/holdings/{wallet_address}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=10) as resp:
                if resp.status != 200:
                    logger.warning(f"Holdings API error {resp.status}")
                    return 0
                data = await resp.json()

        tokens = data.get("tokens", {}).get(token_mint, [])
        if not tokens:
            logger.debug(f"No {token_mint[:6]}... in holdings")
            return 0

        token = tokens[0]
        decimals = token.get("decimals", 6)  # pump.fun default = 6

        # PRIMARY: uiAmount (already decimal-adjusted)
        ui_amount = token.get("uiAmount")
        if ui_amount is not None and ui_amount > 0:
            # ROUND UP to avoid dust loss
            import math
            lamports = math.ceil(ui_amount * (10 ** decimals))
            logger.debug(f"BALANCE → {ui_amount:.6f} tokens → {lamports} lamports (rounded UP)")
            return lamports

        # FALLBACK: raw "amount" string/int
        amount_raw = token.get("amount")
        if amount_raw:
            lamports = int(amount_raw)
            logger.debug(f"FALLBACK → raw amount: {lamports} lamports")
            return lamports

        return 0

    except Exception as e:
        logger.warning(f"Balance fetch failed: {e}")
        return 0

async def get_token_price(mint: str, session: aiohttp.ClientSession) -> float:
    url = f"https://lite-api.jup.ag/tokens/v2/search?query={mint}"
    try:
        async with session.get(url, timeout=8) as r:
            if not r.ok:
                logger.debug(f"v2/search HTTP {r.status}")
                return 0.0
            data = await r.json()
            if data and len(data) > 0:
                price = data[0].get("usdPrice") or data[0].get("priceUsd")
                if price:
                    logger.debug(f"PRICE OK → ${float(price):.10f} | MCAP ${data[0].get('mcap',0):,}")
                    return float(price)
    except Exception as e:
        logger.debug(f"v2 error: {e}")
    logger.warning(f"PRICE FAILED → 0.0")
    return 0.0

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
    log_counter = 0

    logger.info(f"MONITOR STARTED | {ca[:6]}... | Entry ${entry_price:.8f} | TP ${tp_price:.8f} | SL ${sl_price:.8f}")

    while not sold:
        price = await get_token_price(ca, session)
        if not price or price <= 0:
            logger.debug(f"Price invalid ({price}) → retry")
            await asyncio.sleep(1)
            continue

        # GET FRESH BALANCE
        token_amount = await get_token_balance(wallet, ca)
        if token_amount == 0:
            logger.warning(f"ZERO BALANCE | {ca[:6]}... | Stopping")
            break

        # CALCULATE USD VALUE
        usd_value = (token_amount / 1e9) * price

        # LOG EVERY 5 POLLS (~5 seconds)
        log_counter += 1
        if log_counter >= 5:
            logger.info(
                f"POLLER | {ca[:6]}... | ${price:.8f} | Bal: {token_amount:,.2f} | ${usd_value:.2f}"
            )
            log_counter = 0

        # TP / SL CHECKS
        if price >= tp_price:
            logger.info(f"TP HIT @ ${price:.8f}")
            sig = await execute_ultra_sell(session, ca, wallet, config)
            if sig:
                profit_usd = (price - entry_price) * (token_amount / 1e9)
                record_sell(ca, sig, profit_usd, True, (price/entry_price-1)*100)
                sold = True
            break

        if price <= sl_price:
            logger.info(f"SL HIT @ ${price:.8f}")
            sig = await execute_ultra_sell(session, ca, wallet, config)
            if sig:
                profit_usd = (price - entry_price) * (token_amount / 1e9)
                record_sell(ca, sig, profit_usd, False, (price/entry_price-1)*100)
                sold = True
            break

        await asyncio.sleep(1)  # poll every ~1 second

    logger.info(f"MONITOR ENDED | {ca[:6]}... | {'SOLD' if sold else 'NO BALANCE'}")

async def execute_ultra_sell(
    session: aiohttp.ClientSession,
    token_mint: str,
    wallet: Keypair,
    config: dict
) -> str | None:
    # GET REAL BALANCE RIGHT BEFORE SELLING
    token_amount = await get_token_balance(wallet, token_mint)
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
