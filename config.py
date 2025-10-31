import os
from pathlib import Path
from dotenv import load_dotenv

env_path = Path(__file__).resolve().parent / "t.env"
load_dotenv(dotenv_path=env_path)

def load_config():
    return {
        "TELEGRAM_API_ID": int(os.getenv("TELEGRAM_API_ID", "0")),
        "TELEGRAM_API_HASH": os.getenv("TELEGRAM_API_HASH", ""),
        "TELEGRAM_BOT_TOKEN": os.getenv("TELEGRAM_BOT_TOKEN", ""),
        "TELEGRAM_CHAT_ID": os.getenv("TELEGRAM_CHAT_ID", ""),
        "TARGET_CHANNEL_ID": int(os.getenv("TARGET_CHANNEL_ID", "0")),
        "RPC_URL": os.getenv("RPC_URL", "https://api.mainnet-beta.solana.com"),
        "PRIVATE_KEY": os.getenv("PRIVATE_KEY", ""),
        "PUBLIC_KEY": os.getenv("PUBLIC_KEY", ""),
        "DAILY_CAPITAL_USD": float(os.getenv("DAILY_CAPITAL_USD", "10.8")),
        "MAX_BUYS_PER_DAY": int(os.getenv("MAX_BUYS_PER_DAY", "50")),
        "BUY_FEE_PERCENT": float(os.getenv("BUY_FEE_PERCENT", "1.0")),
        "SELL_FEE_PERCENT": float(os.getenv("SELL_FEE_PERCENT", "1.0")),
        "STOP_LOSS": float(os.getenv("STOP_LOSS", "-20")),
        "TAKE_PROFIT": float(os.getenv("TAKE_PROFIT", "40")),
        "DRY_RUN": int(os.getenv("DRY_RUN", "1")),
        "SESSION_NAME": os.getenv("SESSION_NAME", "sniper_session"),
        "CYCLE_LIMIT": [int(x) for x in os.getenv("CYCLE_LIMIT", "50,50").split(",") if x.strip()],
        "REFERRAL_ACCOUNT": os.getenv("REFERRAL_ACCOUNT", "").strip(),
        "REFERRAL_FEE_BPS": int(os.getenv("REFERRAL_FEE_BPS", "50")),
        "TRADE_SLEEP_SEC": float(os.getenv("TRADE_SLEEP_SEC", "5.0")),
    }