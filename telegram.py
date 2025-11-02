# telegram.py
import re
from telethon import events

def extract_ca(msg) -> str | None:
    """
    Extract Solana contract address (CA).
    Priority: 1. Message text → 2. Button URLs
    Returns first valid CA found, or None.
    """
    text = getattr(msg, "text", "") or ""
    buttons = getattr(msg, "buttons", []) or []

    # 1. Check message text FIRST
    if m := re.search(r"([A-HJ-NP-Za-km-z]{32,44})", text):
        return m.group(1)

    # 2. Then check button URLs
    for row in buttons:
        for btn in row:
            url = getattr(btn, "url", "")
            if m := re.search(r"([A-HJ-NP-Za-km-z]{32,44})", url):
                return m.group(1)

    return None