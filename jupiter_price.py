import asyncio
import logging
import aiohttp

SOL_MINT = "So11111111111111111111111111111111111111112"

async def get_mcap_and_price(session: aiohttp.ClientSession, ca: str) -> dict:
    url = f"https://lite-api.jup.ag/tokens/v2/search?query={ca}"
    for _ in range(3):
        try:
            async with session.get(url, timeout=10) as r:
                data = await r.json()
            if data and data[0]:
                t = data[0]
                return {
                    "priceUsd": float(t.get("usdPrice", 0)),
                    "marketCap": float(t.get("mcap", 0)),
                    "liquidity": float(t.get("liquidity", 0)),
                    "source": "jupiter"
                }
        except:
            await asyncio.sleep(0.6)
    return {"priceUsd": None, "marketCap": None, "liquidity": None, "source": "failed"}

async def get_sol_price_usd(session: aiohttp.ClientSession) -> float:
    """
    Fetch live SOL/USD price from Jupiter API using the SOL mint address.
    """
    url = f"https://lite-api.jup.ag/tokens/v2/search?query={SOL_MINT}"
    for _ in range(3):
        try:
            async with session.get(url, timeout=10) as r:
                data = await r.json()
            if data and data[0]:
                sol_data = data[0]
                price = float(sol_data.get("usdPrice", 0))
                if price > 0:
                    logging.info(f"✅ Live SOL/USD price fetched: {price}")
                    return price
        except Exception as e:
            logging.warning(f"⚠️ Error fetching SOL price: {e}")
            await asyncio.sleep(0.6)
    logging.error("❌ Failed to fetch SOL price from Jupiter API.")
    return 0.0
