import json
from datetime import datetime
from utils import send_telegram_message

STATE_FILE = "position_state.json"
TRADE_FILE = "trades_history.json"

def _load(file): return json.load(open(file, "r")) if open(file).read().strip() else {}
def _save(file, data): json.dump(data, open(file, "w"), indent=2)

def record_buy(ca, name, mcap, gross, net, fee):
    trades = _load(TRADE_FILE)
    trades[ca] = {"buy": {"name": name, "mcap": mcap, "gross": gross, "net": net, "fee": fee, "time": datetime.utcnow().isoformat()}}
    _save(TRADE_FILE, trades)
    send_telegram_message(f"BUY {name}\nCA: `{ca}`\nMCAP: ${mcap:,.0f}\nNet: ${net:.2f}")

def record_limit_order(ca, price, amount):
    state = _load(STATE_FILE)
    state[ca] = {"limit_price": price, "amount": amount}
    _save(STATE_FILE, state)

def update_compounding(profit_usd: float):
    state = _load(STATE_FILE)
    state["balance"] = state.get("balance", 0) + profit_usd
    state["cycle"] = state.get("cycle", 0) + 1
    _save(STATE_FILE, state)