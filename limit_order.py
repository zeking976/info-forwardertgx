import aiohttp
import json
from reports import record_limit_order

TRIGGER_URL = "https://lite-api.jup.ag/trigger/v1/createOrder"

async def create_jupiter_limit_order(
    session: aiohttp.ClientSession,
    token_mint: str,
    amount: int,
    target_price: float,
    wallet_pubkey: str,
    config: dict,
) -> str | None:
    making_amount = str(amount)
    taking_amount = str(int(target_price * amount / 1e9 * (1 - config["SELL_FEE_PERCENT"] / 100)))

    payload = {
        "inputMint": token_mint,
        "outputMint": "So11111111111111111111111111111111111111112",
        "maker": wallet_pubkey,
        "payer": wallet_pubkey,
        "params": {"makingAmount": making_amount, "takingAmount": taking_amount, "feeBps": "20"},
        "referralAccount": config["REFERRAL_ACCOUNT"],
        "referralFeeBps": config["REFERRAL_FEE_BPS"],
        "computeUnitPrice": "auto"
    }

    for attempt in range(1, 4):
        try:
            async with session.post(TRIGGER_URL, json=payload, timeout=15) as r:
                res = await r.json()
            order_id = res.get("orderId")
            if order_id:
                record_limit_order(token_mint, target_price, amount)
                return order_id
        except Exception as e:
            logging.warning(f"Limit order attempt {attempt}/3: {e}")
            await asyncio.sleep(attempt)
    return None