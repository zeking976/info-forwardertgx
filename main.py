#!/usr/bin/env python3
import asyncio
import sys
import os
from loguru import logger
from telethon import events
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
    logger.info("UX-SolSniper Bot STARTED🚀")
    config = load_config()
    bot = SniperBot(config)
    # === RESTORE EVENT HANDLER HERE ===
    @bot.client.on(events.NewMessage(chats=config["TARGET_CHANNEL_ID"]))
    async def handler(event):
        text = event.message.message or ""
        if not text.lstrip().startswith("🔥"):
            return
        if any(emoji in text for emoji in ("💰", "🏆", "📈")):
            return
        if ca := bot.extract_ca(event.message):
            if ca not in bot.processed_cas:
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
        logger.info("Shutting down gracefully🔁...")