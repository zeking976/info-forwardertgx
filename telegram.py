import re
import logging
from telethon import events
from typing import Optional

logging.basicConfig(
    filename="ca_filter.log",
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

CA_RE = re.compile(r"([A-HJ-NP-Za-km-z0-9]{32,44})")

def extract_ca(msg) -> Optional[str]:
    """
    Extract a contract address (base58-like 32-44 chars) from a Telethon message.
    Logs only 🔥 messages and their CA.
    """
    # Get text safely
    text = ""
    for attr in ("message", "text", "raw_text"):
        text = getattr(msg, attr, None) or text
    if not text:
        text = str(msg) if msg is not None else ""

    stripped = text.lstrip()

    # Only process if starts with 🔥
    if not stripped.startswith("🔥"):
        return None

    # Block if contains these emojis
    if any(e in text for e in ("🏆", "💰", "📈")):
        return None

    # Try extracting CA from buttons first
    buttons = getattr(msg, "buttons", None) or []
    if buttons:
        for row in buttons:
            for btn in row:
                url = getattr(btn, "url", "") or ""
                label = getattr(btn, "text", "") or getattr(btn, "label", "")
                for candidate in (url, label):
                    if not candidate:
                        continue
                    if m := CA_RE.search(candidate):
                        ca = m.group(1)
                        logging.info(f"🔥 {ca}")
                        return ca

    # Fallback to text
    if m := CA_RE.search(text):
        ca = m.group(1)
        logging.info(f"🔥 {ca}")
        return ca

    return None