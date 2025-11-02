#!/usr/bin/env python3
import asyncio
import sys
import os
from loguru import logger
from telethon import events
from telethon.sessions import StringSession
from config import load_config
from sniper import SniperBot
# === LOGGING SETUP ===
logger.remove()
logger.add(
    "/root/ux-solsniper/sniper.log",
    rotation="5 MB",
    retention="7 days",
    level="INFO",
    format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {message}"
)
logger.add(
    sys.stdout,
    level="INFO",
    colorize=True,
    format="<green>{time:HH:mm:ss}</green> | <level>{level}</level> | {message}"
)
async def main():
    logger.info("UX-SolSniper Bot STARTED")
    config = load_config()
    # === SESSION STRING: LOAD FROM FILE ===
    session_file = "/root/ux-solsniper/session_string.txt"
    if not os.path.exists(session_file):
        logger.error("session_string.txt NOT FOUND! Run setup first.")
        sys.exit(1)
    with open(session_file, "r") as f:
        session_str = f.read().strip()
    # === OVERRIDE CLIENT TO USE STRING SESSION ===
    bot = SniperBot(config)
    bot.client = TelegramClient(
        StringSession(session_str),
        int(config["TELEGRAM_API_ID"]),
        config["TELEGRAM_API_HASH"]
    )
    # === EVENT HANDLER: FILTER BY EMOJI + CA ===
    @bot.client.on(events.NewMessage(chats=int(config["TARGET_CHANNEL_ID"])))
    async def handler(event):
        text = (event.message.message or "").strip()
        # === DEBUG: LOG EVERY MESSAGE FROM CHANNEL ===
        logger.debug(f"RAW MSG: {text[:100]}")
        # === FILTER 1: MUST START WITH fire OR contain trophy/gold/medal/chart ===
        if not (text.startswith("🔥") or any(e in text for e in ("trophy", "gold", "medal", "chart"))):
            return
        # === FILTER 2: MUST NOT contain money/gold/chart (avoid spam) ===
        if any(e in text for e in ("💰", "🏆", "📈")):
            return
        # === EXTRACT CA ===
        ca = bot.extract_ca(event.message)
        if ca and ca not in bot.processed_cas:
            bot.processed_cas.add(ca)
            await bot.queue.put(ca)
            logger.info(f"ENQUEUED CA📃: {ca}")
    await bot.client.start()
    logger.info("Connected to Telegram")
    # Start worker
    asyncio.create_task(bot.worker())
    # Keep alive
    await asyncio.Event().wait()
if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Shutting down🔁...")