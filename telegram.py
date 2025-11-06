import re
from telethon import events

def extract_ca(msg) -> str | None:
    text = getattr(msg, "text", "") or ""
    buttons = getattr(msg, "buttons", []) or []
    candidates = []

    # 1. TEXT: raw mint
    if m := re.search(r"\b([1-9A-HJ-NP-Za-km-z]{32,44})\b", text):
        candidates.append(m.group(1))

    # 2. TEXT: known platforms
    patterns = [
        r"dexscreener\.com/solana/([A-HJ-NP-Za-km-z]{32,44})",
        r"pump\.fun/([A-HJ-NP-Za-km-z]{32,44})",
        r"raydium\.io/swap.*?mint=([A-HJ-NP-Za-km-z]{32,44})",
        r"solscan\.io/token/([A-HJ-NP-Za-km-z]{32,44})",
    ]
    for p in patterns:
        if m := re.search(p, text, re.IGNORECASE):
            candidates.append(m.group(1))

    # 3. BUTTONS
    for row in buttons:
        for btn in row:
            url = getattr(btn, "url", "")
            if m := re.search(r"([A-HJ-NP-Za-km-z]{32,44})", url):
                candidates.append(m.group(1))
            for p in patterns:
                if m := re.search(p, url, re.IGNORECASE):
                    candidates.append(m.group(1))

    for ca in candidates:
        if 32 <= len(ca) <= 44 and re.match(r"^[1-9A-HJ-NP-Za-km-z]+$", ca):
            return ca
    return None