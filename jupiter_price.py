import asyncio
import logging
import aiohttp
from loguru import logger
async def get_sol_price_usd(session):
    """Fetch SOL price in USD using CoinGecko only."""
    url = "https://api.coingecko.com/api/v3/simple/price?ids=solana&v
s_currencies=usd"
    for attempt in range(1, 2):
        try:
            async with session.get(url, timeout=10) as resp:
                if resp.status == 429 or resp.status != 200:
                    await asyncio.sleep(attempt)
                    continue
                data = await resp.json()
                price = data.get("solana", {}).get("usd")
                if price:
                    logger.info(f"SOL PRICE: ${price:.2f} (via CoinGe
cko)")
                    return float(price)
        except Exception:
            await asyncio.sleep(attempt)
    logger.error("Failed to fetch SOL price from CoinGecko after 3 at
tempts")
    return 0.0
async def get_mcap_and_price(session: aiohttp.ClientSession, ca: str)
 -> dict:
    result = {
        "priceUsd": None,
        "marketCap": None,
        "liquidity": None,
        "source": "failed"
    }
    # === DEXSCREENER PRIMARY ===
    ds_url = f"https://api.dexscreener.com/latest/dex/tokens/{ca}"
    try:
        async with session.get(ds_url, timeout=10) as resp:
            if resp.status == 200:
                data = await resp.json()
                pairs = data.get("pairs", [])
                if pairs:
                    # Prefer Raydium or PumpSwap
                    pair = next((p for p in pairs if p.get("dexId") i
n ["raydium", "pumpswap"]), pairs[0])
                    price_usd = pair.get("priceUsd")
                    if price_usd:
                        result["priceUsd"] = float(price_usd)
                    mcap = pair.get("marketCap")
                    if mcap:
                        result["marketCap"] = float(mcap)
                    liq_usd = pair.get("liquidity", {}).get("usd")
                    if liq_usd:
                        result["liquidity"] = float(liq_usd)
                    if result["priceUsd"] or result["marketCap"] or r
esult["liquidity"]:
                        result["source"] = "dexscreener"
                        logger.debug(f"DEXSCREENER → PRICE ${result['
priceUsd']:.10f} | MCAP ${result['marketCap']:,} | LIQ ${result['liqu
idity']:,}")
    except Exception as e:
        logger.debug(f"Dexscreener fetch error: {e}")
    # === JUPITER FALLBACK FOR MISSING FIELDS ONLY ===
    if None in (result["priceUsd"], result["marketCap"], result["liqu
idity"]):
        logger.info("Dexscreener missing fields → JUPITER FALLBACK")
        jup_url = f"https://lite-api.jup.ag/tokens/v2/search?query={c
a}"
        try:
            async with session.get(jup_url, timeout=8) as r:
                if r.ok:
                    data = await r.json()
                    if data and len(data) > 0:
                        t = data[0]
                        if result["priceUsd"] is None:
                            price = t.get("usdPrice") or t.get("price
Usd")
                            if price:
                                result["priceUsd"] = float(price)
                        if result["marketCap"] is None:
                            mcap = t.get("mcap")
                            if mcap:
                                result["marketCap"] = float(mcap)
                        if result["liquidity"] is None:
                            liq = t.get("liquidity")
                            if liq:
                                result["liquidity"] = float(liq)
                        result["source"] = result["source"].replace("
failed", "jupiter_fallback")
        except Exception as e:
            logger.warning(f"Jupiter fallback failed: {e}")
    # Final fallback
    if result["priceUsd"] is None and result["marketCap"] is None and
 result["liquidity"] is None:
        result["source"] = "failed"
    return result
async def get_token_price(mint: str, session: aiohttp.ClientSession)
-> float:
    # === DEXSCREENER PRIMARY ===
    ds_url = f"https://api.dexscreener.com/latest/dex/tokens/{mint}"
    try:
        async with session.get(ds_url, timeout=8) as resp:
            if resp.status == 200:
                data = await resp.json()
                pairs = data.get("pairs", [])
                if pairs:
                    # Prefer Raydium or PumpSwap
                    for pair in pairs:
                        if pair.get("dexId") in ["raydium", "pumpswap
"]:
                            price_usd = pair.get("priceUsd")
                            if price_usd:
                                logger.debug(f"DEXSCREENER PRICE → ${
float(price_usd):.10f} | MCAP ${pair.get('fdv', 0):,}")
                                return float(price_usd)
                    # Fallback to first pair
                    price_usd = pairs[0].get("priceUsd")
                    if price_usd:
                        logger.debug(f"DEXSCREENER PRICE (fallback) →
 ${float(price_usd):.10f}")
                        return float(price_usd)
    except Exception as e:
        logger.debug(f"Dexscreener error: {e}")
    # === JUPITER FALLBACK ===
    logger.info("Dexscreener failed → JUPITER FALLBACK")
    jup_url = f"https://lite-api.jup.ag/tokens/v2/search?query={mint}
"
    try:
        async with session.get(jup_url, timeout=8) as r:
            if r.ok:
                data = await r.json()
                if data and len(data) > 0:
                    price = data[0].get("usdPrice") or data[0].get("p
riceUsd")
                    if price:
                        logger.debug(f"JUPITER PRICE → ${float(price)
:.10f}")
                        return float(price)
    except Exception as e:
        logger.debug(f"Jupiter fallback error: {e}")
    logger.warning("ALL PRICE SOURCES FAILED → 0.0")
    return 0.0
# jupiter_price.py — FINAL & BULLETPROOF
async def get_token_balance(wallet, mint, session):
    wallet_address = str(wallet.pubkey())
    # === JUPITER PRIMARY (3 retries) ===
    for attempt in range(1, 4):
        try:
            url = f"https://lite-api.jup.ag/ultra/v1/holdings/{wallet
_address}"
            async with session.get(url, timeout=10) as resp:
                if resp.status == 429:
                    logger.warning(f"Jupiter 429 → retry {attempt}/3"
)
                    await asyncio.sleep(attempt)
                    continue
                if resp.status != 200:
                    logger.warning(f"Jupiter error {resp.status}")
                    continue  # retry on non-200
                data = await resp.json()
            token = next((t for t in data.get("tokens", {}).get(mint,
 [])), None)
            if token:
                ui_amount = token.get("uiAmount", 0.0)
                decimals = token.get("decimals", 6)
                if ui_amount > 0:
                    logger.debug(f"JUPITER UI: {ui_amount:.6f} tokens
")
                    return ui_amount, decimals
                amount_raw = token.get("amount")
                if amount_raw:
                    lamports = int(amount_raw)
                    tokens = lamports / (10 ** decimals)
                    logger.debug(f"JUPITER RAW → {tokens:.6f} tokens"
)
                    return tokens, decimals
        except Exception as e:
            logger.warning(f"Jupiter attempt {attempt}/3 failed: {e}"
)
            if attempt < 3:
                await asyncio.sleep(attempt)
    # === FALLBACK: RETURN 0.0, 6 (SAFE) ===
    logger.warning("Jupiter failed → balance = 0.0")
    return 0.0, 6