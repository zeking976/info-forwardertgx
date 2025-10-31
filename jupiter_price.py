import aiohttp

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