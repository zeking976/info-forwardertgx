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
    except:
        return {}

def _save(file, data): 
    with open(file, "w") as f:
        json.dump(data, f, indent=2)


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
        short = tx_sig[:8]
        msg += f"\nTX: [{short}...](https://solscan.io/tx/{tx_sig})"

    send_telegram_message(msg)


def record_limit_order(ca, price, amount, entry_price, take_profit_pct, stop_loss_pct):
    """
    Record limit order + send Telegram with TP/SL info
    """
    state = _load(STATE_FILE)
    
    # Determine if TP or SL
    is_tp = price >= entry_price * (1 + take_profit_pct / 100)
    order_type = "TAKE PROFIT" if is_tp else "STOP LOSS"
    emoji = "Target" if is_tp else "Stop"

    state[ca] = {
        "limit_price": price,
        "amount": amount,
        "entry_price": entry_price,
        "take_profit_pct": take_profit_pct,
        "stop_loss_pct": stop_loss_pct,
        "order_type": order_type
    }
    _save(STATE_FILE, state)

    # Send Telegram
    send_telegram_message(
        f"{emoji} **{order_type} ORDER SET**\n"
        f"Coin: {trades.get(ca, {}).get('buy', {}).get('name', 'Unknown')}\n"
        f"📃CA: `{ca}`\n"
        f"🔫Entry: ${entry_price:.8f}\n"
        f"📌Target: ${price:.8f} ({'+' if is_tp else ''}{take_profit_pct if is_tp else -stop_loss_pct}%)\n"
        f"💵Amount: ~${(amount / 1e9) * entry_price:.2f}"
    )

def record_sell(ca: str, signature: str, profit_usd: float, is_tp: bool, profit_pct: float):
    state = _load(STATE_FILE)
    trade = _load(TRADE_FILE).get(ca, {}).get("buy", {})
    name = trade.get("name", "Unknown")

    order_type = "TAKE PROFIT" if is_tp else "STOP LOSS"
    emoji = "Target" if is_tp else "Stop"

    # Update compounding
    update_compounding(profit_usd)

    # Send Telegram
    send_telegram_message(
        f"📑 {order_type} HIT\n"
        f"🪙Coin: {name}\n"
        f"📃CA: `{ca}`\n"
        f"💵Profit: ${profit_usd:+.2f} ({profit_pct:+.1f}%)\n"
        f"✏️TX: [{signature[:8]}...](https://solscan.io/tx/{signature})"
    )

def update_compounding(profit_usd: float):
    state = _load(STATE_FILE)
    state["balance"] = round(state.get("balance", 0) + profit_usd, 2)
    state["cycle"] = state.get("cycle", 0) + 1
    _save(STATE_FILE, state)