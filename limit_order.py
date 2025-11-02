# limit_order.py
import aiohttp
import base64
from loguru import logger
from solders.transaction import VersionedTransaction
from solders.keypair import Keypair
from solders.message import to_bytes_versioned
from reports import record_limit_order

TRIGGER_URL = "https://lite-api.jup.ag/trigger/v1/createOrder"
EXECUTE_URL = "https://lite-api.jup.ag/trigger/v1/execute"

async def create_jupiter_limit_order(
    session: aiohttp.ClientSession,
    token_mint: str,
    amount: int,
    target_price: float,
    wallet: Keypair,
    config: dict,
    entry_price: float,
    take_profit_pct: float,
    stop_loss_pct: float,
) -> dict | None:
    """
    Create and execute Jupiter limit order (TP or SL).
    Returns dict with:
        - signature: tx sig of order creation
        - orderId: Jupiter order ID (for polling/webhook)
    or None on failure.
    """
    making_amount = str(amount)
    taking_amount = str(int(target_price * amount / 1e9 * (1 - config["SELL_FEE_PERCENT"] / 100)))

    payload = {
        "inputMint": token_mint,
        "outputMint": "So11111111111111111111111111111111111111112",
        "maker": str(wallet.pubkey()),
        "payer": str(wallet.pubkey()),
        "params": {"makingAmount": making_amount, "takingAmount": taking_amount},
        "referralAccount": config["REFERRAL_ACCOUNT"],
        "referralFeeBps": config["REFERRAL_FEE_BPS"],
        "computeUnitPrice": "auto"
    }

    for attempt in range(1, 4):
        try:
            # === STEP 1: CREATE ORDER ===
            async with session.post(TRIGGER_URL, json=payload, timeout=15) as r:
                res = await r.json()
            order_id = res.get("orderId")
            if not order_id:
                logger.warning(f"createOrder failed: no orderId | attempt {attempt}")
                continue

            # === STEP 2: RECORD LIMIT ORDER (TP or SL) ===
            record_limit_order(
                ca=token_mint,
                price=target_price,
                amount=amount,
                entry_price=entry_price,
                take_profit_pct=take_profit_pct,
                stop_loss_pct=stop_loss_pct
            )

            # === STEP 3: SIGN & EXECUTE (submit to Jupiter) ===
            tx_bytes = base64.b64decode(res["transaction"])
            tx = VersionedTransaction.from_bytes(tx_bytes)
            signed_tx = VersionedTransaction.populate(
                tx.message,
                [wallet.sign_message(to_bytes_versioned(tx.message))]
            )
            signed_b64 = base64.b64encode(bytes(signed_tx)).decode()

            exec_payload = {"signedTransaction": signed_b64, "requestId": res["requestId"]}
            async with session.post(EXECUTE_URL, json=exec_payload, timeout=20) as exec_r:
                exec_res = await exec_r.json()

            if exec_res.get("status") == "success":
                sig = exec_res.get("signature")
                logger.info(f"LIMIT ORDER PLACED: {sig[:8]}... | OrderID: {order_id[:8]}... | Target: ${target_price:.8f}")

                return {
                    "signature": sig,
                    "orderId": order_id,
                    "target_price": target_price,
                    "amount": amount,
                    "entry_price": entry_price,
                    "take_profit_pct": take_profit_pct,
                    "stop_loss_pct": stop_loss_pct
                }

            else:
                logger.warning(f"execute failed: {exec_res}")

        except Exception as e:
            logger.warning(f"Limit order attempt {attempt}/3 failed: {e}")
            await asyncio.sleep(attempt * 2)

    logger.error(f"Failed to create limit order for {token_mint}")
    return None