# /root/ux-solsniper/buy.py
import aiohttp
import asyncio
import base64
from loguru import logger
from utils import compute_amount_from_usd
from solders.keypair import Keypair
from solders.transaction import VersionedTransaction
from solders.message import to_bytes_versioned
from reports import record_buy
from jupiter_price import get_sol_price_usd

ORDER_URL = "https://lite-api.jup.ag/ultra/v1/order"
EXEC_URL  = "https://lite-api.jup.ag/ultra/v1/execute"

async def execute_jupiter_buy(
    session: aiohttp.ClientSession,
    *,
    input_mint: str = "So11111111111111111111111111111111111111112",
    output_mint: str | None = None,
    amount: float | int = 0.0,
    wallet: Keypair,
    config: dict,
    coin_name: str,
    market_cap: float,
) -> str | None:
    """Execute a Jupiter buy transaction via Ultra API."""
    try:
        # === USE COMPOUNDING LOGIC ===
        amount = await compute_amount_from_usd(session, config, output_mint)
        if amount <= 0:
            logger.info("Buy skipped: amount = 0")
            return None

        usd_value = (amount / 1e9) * await get_sol_price_usd(session)
        fee_usd = usd_value * (config["BUY_FEE_PERCENT"] / 100)

        if config["DRY_RUN"]:
            record_buy(output_mint, coin_name, market_cap, usd_value, usd_value - fee_usd, fee_usd)
            return f"DRY_RUN_BUY_{int(asyncio.get_running_loop().time())}"

        params = {
            "inputMint": input_mint,
            "outputMint": output_mint,
            "amount": str(amount),
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
                        logger.info(f"❌  /order HTTP {r.status}")
                        continue
                    order = await r.json()
                if not order.get("transaction"):
                    logger.info(f"❌  Invalid order: {order}")
                    continue
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
                async with session.post(EXEC_URL, json=payload, timeout=20) as resp:
                    res = await resp.json()
                if res.get("status", "").lower() == "success":
                    sig = res.get("signature") or res.get("txid")
                    record_buy(output_mint, coin_name, market_cap, usd_value, usd_value - fee_usd, fee_usd, sig)
                    logger.info(f"🚀 BOUGHT {sig[:8]}... | https://solscan.io/tx/{sig}")
                    return sig
            except Exception as e:
                logger.info(f"BUY attempt {attempt}/3 → {e}")
                await asyncio.sleep(1)
        logger.info(f"❌  BUY failed after 3 retries")
        return None