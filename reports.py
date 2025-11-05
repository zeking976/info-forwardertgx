# /root/ux-solsniper/reports.py
import json
from datetime import datetime
import os
from utils import send_telegram_message, escape_md  # ← ESCAPE ADDED

# === CONFIG ===
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "").strip()

STATE_FILE = "position_state.json"
TRADE_FILE = "trades_history.json"

def _load(file):
    try:
        with open(file, "r") as f:
            return json.loads(f.read().strip() or "{}")
    except Exception:
        return {}

def _save(file, data):
    with open(file, "w") as f:
        json.dump(data, f, indent=2)

# === RECORD BUY ===
def record_buy(ca, name, mcap, gross, net, fee, tx_sig=None):
    trades = _load(TRADE_FILE)
    trades[ca] = {
        "buy": {
            "name": name,
            "mcap": mcap,
            "gross": gross,
            "net": net,
            "fee": fee,
            "time": datetime.utcnow().isoformat(),
            "tx_sig": tx_sig
        }
    }
    _save(TRADE_FILE, trades)

    msg = (
        f"BUY {escape_md(name)}\n"
        f"CA: `{ca}`\n"
        f"MCAP: ${mcap:,.0f}\n"
        f"Net: ${net:.2f}"
    )
    if tx_sig:
        short = tx_sig[:8]
        msg += f"\nTX: [{short}...](https://solscan.io/tx/{tx_sig})"

    asyncio.create_task(
        send_telegram_message(escape_md(msg), BOT_TOKEN, CHAT_ID)
    )

# === RECORD SELL ===
def record_sell(ca: str, signature: str, profit_usd: float, is_tp: bool, profit_pct: float):
    state  = _load(STATE_FILE)
    trades = _load(TRADE_FILE)
    name   = trades.get(ca, {}).get("buy", {}).get("name", "Unknown")
    order  = "TAKE PROFIT" if is_tp else "STOP LOSS"

    # COMPOUND
    state["balance"] = round(state.get("balance", 0) + profit_usd, 2)
    state["cycle"]   = state.get("cycle", 0) + 1
    _save(STATE_FILE, state)
    state.pop(ca, None)
    _save(STATE_FILE, state)

    msg = (
        f"**{order} HIT**\n"
        f"Coin: {escape_md(name)}\n"
        f"CA: `{ca}`\n"
        f"Profit: **${profit_usd:+.2f}** ({profit_pct:+.1f}%)\n"
        f"TX: [{signature[:8]}...](https://solscan.io/tx/{signature})"
    )

    asyncio.create_task(
        send_telegram_message(escape_md(msg), BOT_TOKEN, CHAT_ID)
    )

# === TRACKERS ===
def get_balance() -> float:
    return _load(STATE_FILE).get("balance", 0.0)

def get_cycle() -> int:
    return _load(STATE_FILE).get("cycle", 0)