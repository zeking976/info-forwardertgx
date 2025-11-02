#!/usr/bin/env python3
import asyncio
import sys
import os
from loguru import logger
from telethon import events
from telethon import TelegramClient
from telethon.sessions import StringSession
from config import load_config
from sniper import SniperBot
# === LOGGING ===
logger.remove()
logger.add("/root/ux-solsniper/sniper.log", level="INFO")
logger.add(sys.stdout, level="INFO", colorize=True)
async def main():
    logger.info("UX-SolSniper Bot STARTED")
    config = load_config()
    # === SESSION STRING ===
    session_file = "/root/ux-solsniper/session_string.txt"
    if not os.path.exists(session_file):
        logger.error("NO session_string.txt")
        sys.exit(1)
    with open(session_file, "r") as f:
        session_str = f.read().strip()
    # === BOT + CLIENT ===
    bot = SniperBot(config)
    bot.client = TelegramClient(
        StringSession(session_str),
        int(config["TELEGRAM_API_ID"]),
        config["TELEGRAM_API_HASH"]
    )
    # === EVENT HANDLER: fire ONLY + DEBUG + CA LOGIC ===
    @bot.client.on(events.NewMessage(chats=int(config["TARGET_CHANNEL_ID"])))
    async def handler(event):
        text = (event.message.message or "").strip()
        logger.info(f"CHANNEL MSG: '{text}' | ID: {event.message.id}")
        # === MUST START WITH fire EMOJI (after spaces) ===
        if not text.lstrip().startswith("🔥"):
            logger.info("SKIPPED: no fire at start")
            return
        # === SKIP BLOCKED EMOJIS: chart money trophy ===
        if any(e in text for e in ("📈", "💰", "🏆")):
            logger.info("SKIPPED: Because (📈/💰/🏆)")
            return
        # === EXTRACT CA ===
        ca = bot.extract_ca(event.message)
        if ca and ca not in bot.processed_cas:
            bot.processed_cas.add(ca)
            await bot.queue.put(ca)
            logger.info(f"ENQUEUED CA📃: {ca}")
        elif ca:
            logger.info(f"DUPLICATE CA: {ca}")
        else:
            logger.info("NO CA FOUND")
    # === START WORKER FIRST ===
    asyncio.create_task(bot.worker())
    # === LOGIN ===
    await bot.client.start()
    logger.info("Connected to Telegram")
    # === KEEP ALIVE ===
    await asyncio.Event().wait()
if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Shutting down🔁...")