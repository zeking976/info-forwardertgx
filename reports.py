# /root/ux-solsniper/reports.py
import json
from datetime import datetime
import os
import asyncio
from utils import send_telegram_message, escape_md

# === CONFIG ===
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "").strip()
MAX_BUYS_PER_DAY = int(os.getenv("MAX_BUYS_PER_DAY", "10"))

STATE_FILE = "position_state.json"
TRADE_FILE = "trades_history.json"
STATS_FILE = "daily_stats.json"

def _load(file):
    try:
        with open(file, "r") as f:
            return json.loads(f.read().strip() or "{}")
    except Exception:
        return {}

def _save(file, data):
    with open(file, "w") as f:
        json.dump(data, f, indent=2)

# === DAILY STATS ===
def _load_stats():
    return _load(STATS_FILE)

def _save_stats(stats):
    _save(STATS_FILE, stats)

def _update_daily_stats(is_tp: bool, profit_usd: float):
    stats = _load_stats()
    today = datetime.utcnow().strftime("%Y-%m-%d")
    
    if stats.get("date") != today:
        stats = {
            "date": today,
            "buys": 0,
            "tp_count": 0,
            "sl_count": 0,
            "total_profit": 0.0,
            "wins": [],
            "losses": []
        }
    
    stats["buys"] += 1
    stats["total_profit"] = round(stats["total_profit"] + profit_usd, 2)
    
    if is_tp:
        stats["tp_count"] += 1
        stats["wins"].append(profit_usd)
    else:
        stats["sl_count"] += 1
        stats["losses"].append(profit_usd)
    
    _save_stats(stats)
    return stats

def _send_daily_report():
    stats = _load_stats()
    if not stats or stats.get("date") != datetime.utcnow().strftime("%Y-%m-%d"):
        return

    wins = len(stats["wins"])
    total = stats["buys"]
    win_rate = (wins / total * 100) if total > 0 else 0
    avg_win = sum(stats["wins"]) / wins if wins > 0 else 0
    avg_loss = sum(stats["losses"]) / len(stats["losses"]) if stats["losses"] else 0

    msg = (
        f"📊**DAILY REPORT** | {stats['date']}\n"
        f"✅Buys: `{stats['buys']}` / {MAX_BUYS_PER_DAY}\n"
        f"🎖️Win Rate: **{win_rate:.1f}%** ({wins}W/{stats['sl_count']}L)\n"
        f"💰Total P&L: **${stats['total_profit']:+.2f}**\n"
        f"🔥Avg Win: **${avg_win:+.2f}**\n"
        f"📉Avg Loss: **${avg_loss:+.2f}**\n"
        f"🎉Best Win: **${max(stats['wins']):+.2f}**\n"
        f"📛Worst Loss: **${min(stats['losses']):+.2f}**"
    )

    asyncio.create_task(
        send_telegram_message(escape_md(msg), BOT_TOKEN, CHAT_ID)
    )

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
        f"✅BUY {escape_md(name)}\n"
        f"📃CA: `{ca}`\n"
        f"📊MCAP: ${mcap:,.0f}\n"
        f"💵Net: ${net:.2f}"
    )
    if tx_sig:
        short = tx_sig[:8]
        msg += f"🖋️\nTX: [{short}...](https://solscan.io/tx/{tx_sig})"

    asyncio.create_task(
        send_telegram_message(escape_md(msg), BOT_TOKEN, CHAT_ID)
    )

# === RECORD SELL ===
def record_sell(ca: str, signature: str, profit_usd: float, is_tp: bool, profit_pct: float):
    state  = _load(STATE_FILE)
    trades = _load(TRADE_FILE)
    name   = trades.get(ca, {}).get("buy", {}).get("name", "Unknown")
    order  = "TAKE PROFIT" if is_tp else "STOP LOSS"

    # UPDATE BALANCE
    state["balance"] = round(state.get("balance", 0) + profit_usd, 2)
    state["cycle"]   = state.get("cycle", 0) + 1
    _save(STATE_FILE, state)
    state.pop(ca, None)
    _save(STATE_FILE, state)

    # UPDATE STATS
    stats = _update_daily_stats(is_tp, profit_usd)

    msg = (
        f"📑**{order} HIT**\n"
        f"🪙Coin: {escape_md(name)}\n"
        f"📃CA: `{ca}`\n"
        f"💸Profit: **${profit_usd:+.2f}** ({profit_pct:+.1f}%)\n"
        f"🖋️TX: [{signature[:8]}...](https://solscan.io/tx/{signature})"
    )

    asyncio.create_task(
        send_telegram_message(escape_md(msg), BOT_TOKEN, CHAT_ID)
    )

    # AUTO-REPORT AFTER MAX BUYS
    if stats["buys"] >= MAX_BUYS_PER_DAY:
        asyncio.create_task(asyncio.sleep(5))  # tiny delay
        _send_daily_report()

# === TRACKERS ===
def get_balance() -> float:
    return _load(STATE_FILE).get("balance", 0.0)

def get_cycle() -> int:
    return _load(STATE_FILE).get("cycle", 0)

def get_daily_stats():
    return _load_stats()