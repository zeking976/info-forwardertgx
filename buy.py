# /root/ux-solsniper/buy.py
import aiohttp
import asyncio
import base64
from loguru import logger
from solders.keypair import Keypair
from solders.transaction import VersionedTransaction
from solders.message import to_bytes_versioned
from utils import sleep_with_logging
from reports import record_buy
from jupiter_price import get_sol_price_usd

ORDER_URL = "https://lite-api.jup.ag/ultra/v1/order"
EXEC_URL  = "https://lite-api.jup.ag/ultra/v1/execute"

# === CHECK SOL BEFORE BUY ===
async def get_sol_balance(wallet: Keypair, session: aiohttp.ClientSession) -> float:
    try:
        url = f"https://lite-api.jup.ag/ultra/v1/holdings/{wallet.pubkey()}"
        async with session.get(url, timeout=8) as r:
            data = await r.json()
            return float(data["sol"]["uiAmount"])
    except:
        return 0.0

# === MAIN BUY FUNCTION (FIXED SIGNATURE) ===
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
        sol_price_usd = await get_sol_price_usd(session)
        sol_amount = config["DAILY_CAPITAL_USD"] / sol_price_usd
        amount = int(sol_amount * 1e9 * (1 - config["BUY_FEE_PERCENT"] / 100))
        usd_value = (amount / 1e9) * sol_price_usd
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
    except Exception as e:
        logger.info(f"Fatal BUY error: {e}")
        return None

async def _get_sol_price(session):
    try:
        async with session.get("https://lite-api.jup.ag/price/v3?ids=So11111111111111111111111111111111111111112", timeout=10) as r:
            data = await r.json()
            return float(data["So11111111111111111111111111111111111111112"]["usdPrice"])
    except:
        return 150.0
