import aiohttp
import base64
import logging
from solders.transaction import VersionedTransaction
from solders.keypair import Keypair
from solders.message import to_bytes_versioned
from reports import record_limit_order, record_sell
from utils import send_telegram_message

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
) -> str | None:
    making_amount = str(amount)
    taking_amount = str(int(target_price * amount / 1e9 * (1 - config["SELL_FEE_PERCENT"] / 100)))

    payload = {
        "inputMint": token_mint,
        "outputMint": "So11111111111111111111111111111111111111112",
        "maker": str(wallet.pubkey()),
        "payer": str(wallet.pubkey()),
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
            if not order_id:
                continue

            # === RECORD LIMIT ORDER ===
            record_limit_order(token_mint, target_price, amount, entry_price, take_profit_pct, stop_loss_pct)

            # === SIGN & EXECUTE IMMEDIATELY ===
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
                # === DETECT TP/SL & RECORD SELL ===
                is_tp = target_price >= entry_price * (1 + take_profit_pct / 100)
                profit_pct = (target_price / entry_price - 1) * 100
                profit_usd = (amount / 1e9) * (target_price - entry_price)
                record_sell(token_mint, sig, profit_usd, is_tp, profit_pct)
                return sig

        except Exception as e:
            logging.warning(f"Limit order exec attempt {attempt}/3 failed: {e}")
            await asyncio.sleep(attempt * 2)

    return None