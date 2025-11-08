import asyncio
import logging
from loguru import logger
import aiohttp
import os
from jupiter_price import get_sol_price_usd

async def compute_amount_from_usd(session, config, ca=None):
    sol_price = 0.0
    for attempt in range(1, 4):
        try:
            sol_price = await get_sol_price_usd(session)
            if sol_price and sol_price > 0:
                break
        except Exception as e:
            logger.warning("Attempt %d/3: get_sol_price_usd() failed: %s", attempt, e)
        await asyncio.sleep(attempt * 1.5)
    if not sol_price or sol_price <= 0:
        logger.error("Could not not fetch SOL price. Skipping buy.")
        return 0
    from reports import get_balance, _load, _save
    STATE_FILE = "position_state.json"
    current_balance_usd = get_balance()
    if current_balance_usd <= 0:
        current_balance_usd = float(config.get("DAILY_CAPITAL_USD", 0.0))
        # INITIALIZE STATE FILE
        state = _load(STATE_FILE)
        state["balance"] = current_balance_usd
        state["cycle"] = state.get("cycle", 0)
        _save(STATE_FILE, state)
        logger.info("COMPOUNDING: Initialized with DAILY_CAPITAL_USD: $%.2f", current_balance_usd)
    else:
        logger.info("COMPOUNDING: Using current balance: $%.2f", current_balance_usd)
    buy_usd = current_balance_usd
    buy_fee_pct = float(config.get("BUY_FEE_PERCENT", 0.0))
    sol_equivalent = buy_usd / sol_price
    sol_after_fee = sol_equivalent * (1.0 - buy_fee_pct / 100.0)
    lamports = int(round(sol_after_fee * 1e9))
    logger.info(
        "COMPOUND BUY | Balance: $%.2f → Using: $%.2f → %.6f SOL → %d lamports",
        current_balance_usd, buy_usd, sol_after_fee, lamports
    )
    return lamports

async def sleep_with_logging(sec: float, reason: str = ""):
    logger.info(f"Sleeping {sec}s: {reason}")
    await asyncio.sleep(sec)

def format_ca(ca: str) -> str:
    return f"`{ca}`"

# === TELEGRAM SENDER (ASYNC + ROBUST) ===
async def send_telegram_message(
    text: str,
    bot_token: str | None = None,
    chat_id: str | None = None
):
    token = bot_token or os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat = chat_id or os.getenv("TELEGRAM_CHAT_ID", "").strip()

    if not token or not chat:
        logger.warning("TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID missing!")
        return

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat,
        "text": text,
        "parse_mode": "MarkdownV2",  # ← ESCAPES _ * [ ] ( ) ~ > # + - = | { } . !
        "disable_web_page_preview": True
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, data=payload, timeout=10) as resp:
                if resp.status == 200:
                    logger.info(f"Telegram sent → {chat}")
                else:
                    txt = await resp.text()
                    logger.error(f"Telegram failed [{resp.status}]: {txt}")
    except Exception as e:
        logger.error(f"Telegram crash: {e}")

# === ESCAPE MARKDOWN CHARACTERS ===
def escape_md(text: str) -> str:
    escape_chars = r'\_*[]()~`>#+-=|{.}!'
    return ''.join('\\' + c if c in escape_chars else c for c in text)
# --- added via patch ---
import json, logging, aiohttp
logger = logging.getLogger(__name__)

