# /root/ux-solsniper/reports.py
import json
from datetime import datetime
from utils import send_telegram_message

STATE_FILE = "position_state.json"
TRADE_FILE = "trades_history.json"

def _load(file):
    try:
        with open(file, "r") as f:
            content = f.read().strip()
            return json.loads(content) if content else {}
    except (FileNotFoundError, json.JSONDecodeError):
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
        f"✅BUY {name}\n"
        f"📃CA: `{ca}`\n"
        f"📊MCAP: ${mcap:,.0f}\n"
        f"💵Net: ${net:.2f}"
    )
    if tx_sig:
        msg += f"\nTX: [{tx_sig[:8]}...](https://solscan.io/tx/{tx_sig})"
    send_telegram_message(msg)

# === RECORD SELL (MARKET SELL ON TP/SL HIT) ===
def record_sell(ca: str, signature: str, profit_usd: float, is_tp: bool, profit_pct: float):
    state = _load(STATE_FILE)
    trades = _load(TRADE_FILE)
    name = trades.get(ca, {}).get("buy", {}).get("name", "Unknown")
    order_type = "TAKE PROFIT" if is_tp else "STOP LOSS"

    # Update compounding balance
    state["balance"] = round(state.get("balance", 0) + profit_usd, 2)
    state["cycle"] = state.get("cycle", 0) + 1
    _save(STATE_FILE, state)

    # Clean position
    state.pop(ca, None)
    _save(STATE_FILE, state)

    # Telegram alert
    send_telegram_message(
        f"📑**{order_type} HIT**\n"
        f"🪙Coin: {name}\n"
        f"📃CA: `{ca}`\n"
        f"💸Profit: **${profit_usd:+.2f}** ({profit_pct:+.1f}%)\n"
        f"🖋️TX: [{signature[:8]}...](https://solscan.io/tx/{signature})"
    )

# === COMPOUNDING TRACKERS ===
def get_balance() -> float:
    return _load(STATE_FILE).get("balance", 0.0)

def get_cycle() -> int:
    return _load(STATE_FILE).get("cycle", 0)