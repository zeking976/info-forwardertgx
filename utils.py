import asyncio
from loguru import logger
import requests
import os

async def sleep_with_logging(sec, reason=""):
    logger.info(f"Sleeping {sec}s: {reason}")
    await asyncio.sleep(sec)

def format_ca(ca): 
    return f"`{ca}`"

# === TELEGRAM MESSAGE SENDER (MOVED HERE) ===
def send_telegram_message(text: str):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        logger.warning("TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set. Skipping message.")
        return
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True
        }
        requests.post(url, data=payload, timeout=10)
    except Exception as e:
        logger.error(f"Failed to send Telegram message: {e}")