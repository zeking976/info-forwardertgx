#!/usr/bin/env python3
import asyncio
import logging
from loguru import logger
from config import load_config
from sniper import SniperBot

async def main():
    config = load_config()
    bot = SniperBot(config)
    await bot.start()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Shutting down gracefully🔁...")