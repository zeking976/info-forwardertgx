# reports.py
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
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError:
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
        f"BUY {name}\n"
        f"CA: `{ca}`\n"
        f"MCAP: ${mcap:,.0f}\n"
        f"Net: ${net:.2f}"
    )
    if tx_sig:
        short = tx_sig[:8]
        msg += f"\nTX: [{short}...](https://solscan.io/tx/{tx_sig})"

    send_telegram_message(msg)

# === RECORD LIMIT ORDER (TP or SL) ===
def record_limit_order(ca, price, amount, entry_price, take_profit_pct, stop_loss_pct):
    state = _load(STATE_FILE)
    trades = _load(TRADE_FILE)

    # Determine order type
    is_tp = take_profit_pct > 0
    order_type = "TAKE PROFIT" if is_tp else "STOP LOSS"
    pct = take_profit_pct if is_tp else stop_loss_pct
    sign = "+" if is_tp else ""

    state[ca] = {
        "limit_price": price,
        "amount": amount,
        "entry_price": entry_price,
        "take_profit_pct": take_profit_pct,
        "stop_loss_pct": stop_loss_pct,
        "order_type": order_type
    }
    _save(STATE_FILE, state)

    name = trades.get(ca, {}).get("buy", {}).get("name", "TKN_???")
    send_telegram_message(
        f"{order_type} ORDER SET\n"
        f"Coin: {name}\n"
        f"CA: `{ca}`\n"
        f"Entry: ${entry_price:.8f}\n"
        f"Target: ${price:.8f} ({sign}{pct}%)\n"
        f"Value: ~${(amount / 1e9) * entry_price:.2f}"
    )

# === RECORD SELL (ONLY WHEN ACTUALLY FILLED) ===
def record_sell(ca: str, signature: str, profit_usd: float, is_tp: bool, profit_pct: float):
    state = _load(STATE_FILE)
    trades = _load(TRADE_FILE)
    name = trades.get(ca, {}).get("buy", {}).get("name", "Unknown")

    order_type = "TAKE PROFIT" if is_tp else "STOP LOSS"

    # Update compounding
    state["balance"] = round(state.get("balance", 0) + profit_usd, 2)
    state["cycle"] = state.get("cycle", 0) + 1
    _save(STATE_FILE, state)

    # Clean up position
    state.pop(ca, None)
    _save(STATE_FILE, state)

    send_telegram_message(
        f"{order_type} HIT\n"
        f"Coin: {name}\n"
        f"CA: `{ca}`\n"
        f"Profit: ${profit_usd:+.2f} ({profit_pct:+.1f}%)\n"
        f"TX: [{signature[:8]}...](https://solscan.io/tx/{signature})"
    )

# === COMPOUNDING TRACKER ===
def get_balance():
    state = _load(STATE_FILE)
    return state.get("balance", 0)

def get_cycle():
    state = _load(STATE_FILE)
    return state.get("cycle", 0)