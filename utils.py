import asyncio
from loguru import logger
import aiohttp
import os

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
