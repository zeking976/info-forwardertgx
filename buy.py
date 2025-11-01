import aiohttp
import base64
import logging
from solders.keypair import Keypair
from solders.transaction import VersionedTransaction
from solders.message import to_bytes_versioned
from utils import sleep_with_logging
from reports import record_buy

ORDER_URL = "https://lite-api.jup.ag/ultra/v1/order"
EXEC_URL = "https://lite-api.jup.ag/ultra/v1/execute"

async def execute_jupiter_buy(
    session: aiohttp.ClientSession,
    quote: dict,
    wallet: Keypair,
    config: dict,
    coin_name: str,
    market_cap: float,
) -> str | None:
    in_amount = int(quote["inAmount"] * (1 - config["BUY_FEE_PERCENT"] / 100))
    usd_value = (in_amount / 1e9) * await _get_sol_price(session)
    fee_usd = usd_value * (config["BUY_FEE_PERCENT"] / 100)

    if config["DRY_RUN"]:
        record_buy(quote["outputMint"], coin_name, market_cap, usd_value, usd_value - fee_usd, fee_usd)
        return f"DRY_RUN_BUY_{int(asyncio.get_event_loop().time())}"

    params = {
        "inputMint": quote["inputMint"],
        "outputMint": quote["outputMint"],
        "amount": str(in_amount),
        "taker": str(wallet.pubkey()),
        "payer": str(wallet.pubkey()),
        "closeAuthority": str(wallet.pubkey()),
    }
    if config["REFERRAL_ACCOUNT"]:
        params.update({"referralAccount": config["REFERRAL_ACCOUNT"], "referralFee": config["REFERRAL_FEE_BPS"]})

    for attempt in range(1, 4):
        try:
            # === GET ORDER ===
            async with session.get(ORDER_URL, params=params, timeout=15) as r:
                order = await r.json()

            # === SIGN TX (YOUR WORKING CODE) ===
            tx_bytes = base64.b64decode(order["transaction"])
            tx = VersionedTransaction.from_bytes(tx_bytes)

            signed_tx_obj = VersionedTransaction.populate(
                tx.message,
                [wallet.sign_message(to_bytes_versioned(tx.message))]
            )

            signed_tx = base64.b64encode(bytes(signed_tx_obj)).decode("utf-8")

            payload = {"signedTransaction": signed_tx, "requestId": order["requestId"]}

            # === EXECUTE ===
            async with session.post(EXEC_URL, json=payload, timeout=20) as resp:
                res = await resp.json()

            if res.get("status") == "success":
                sig = res.get("signature")
                record_buy(quote["outputMint"], coin_name, market_cap, usd_value, usd_value - fee_usd, fee_usd, sig)

        except Exception as e:
            logging.warning(f"BUY attempt {attempt}/3 failed: {e}")
            await asyncio.sleep(attempt * 2)

    return None


async def _get_sol_price(session):
    try:
        async with session.get("https://lite-api.jup.ag/price/v3?ids=So11111111111111111111111111111111111111112", timeout=10) as r:
            data = await r.json()
            return float(data["So11111111111111111111111111111111111111112"]["usdPrice"])
    except:
        return 150.0