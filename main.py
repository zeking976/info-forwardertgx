#!/usr/bin/env python3
import asyncio
from loguru import logger
from config import load_config
from sniper import SniperBot

# === CONFIGURE LOGGING TO FILE + CONSOLE ===
logger.remove()  # Remove default stderr
logger.add(
    "/root/ux-solsniper/sniper.log",
    rotation="5 MB",
    retention="7 days",
    level="INFO",
    format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level}</level> | {message}"
)
logger.add(
    sys.stderr,
    level="DEBUG" if os.getenv("DEBUG") else "INFO"
)

async def main():
    config = load_config()
    logger.info("Starting UX-SolSniper Bot🚀📈")
    bot = SniperBot(config)
    await bot.start()

if __name__ == "__main__":
    import sys, os
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Shutting down gracefully🔁...")