import asyncio
from loguru import logger

async def sleep_with_logging(sec, reason=""): 
    logger.info(f"Sleeping {sec}s: {reason}")
    await asyncio.sleep(sec)

def format_ca(ca): return f"`{ca}`"