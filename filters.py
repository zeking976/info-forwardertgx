async def passes_filters(info: dict, config: dict) -> bool:
    price = info.get("priceUsd")
    mcap = info.get("marketCap")
    liq = info.get("liquidity")
    if not mcap or mcap < 7000: return False
    if not liq or liq < 4000: return False
    if mcap / liq > 10: return False
    return True