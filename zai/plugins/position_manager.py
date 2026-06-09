"""持仓管理 — four.meme 一级止盈止损"""

import json
import os
import time
from typing import Any

from config.strategy import (
    HARD_STOP_LOSS_PCT,
    SMART_EXIT,
    TAKE_PROFIT_TIERS,
    TRAILING_STOP_ACTIVATE_PCT,
    TRAILING_STOP_PULLBACK_PCT,
)
from plugins import memecoin_score, token_scan
from utils.format import fmt_money, fmt_pct, safe_float

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
POSITIONS_FILE = os.path.join(DATA_DIR, "positions.json")

_positions: list[dict[str, Any]] = []


def _ensure() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)


def _load() -> list[dict]:
    _ensure()
    if not os.path.exists(POSITIONS_FILE):
        return []
    try:
        with open(POSITIONS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


def _save(positions: list[dict]) -> None:
    _ensure()
    with open(POSITIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(positions, f, indent=2, ensure_ascii=False)


def get_positions() -> list[dict[str, Any]]:
    global _positions
    if not _positions:
        _positions = _load()
    return list(_positions)


def remove_position(address: str) -> None:
    global _positions
    addr = address.lower().strip()
    positions = [p for p in _load() if p.get("address") != addr]
    _positions = positions
    _save(positions)


def reduce_position(address: str, sell_pct: float) -> None:
    global _positions
    addr = address.lower().strip()
    positions = _load()
    for p in positions:
        if p.get("address") == addr:
            if sell_pct >= 100:
                p["status"] = "closed"
            else:
                p["amount_bnb"] = round(safe_float(p.get("amount_bnb")) * (1 - sell_pct / 100), 6)
            break
    positions = [p for p in positions if p.get("status") != "closed"]
    _positions = positions
    _save(positions)


def add_position(address: str, symbol: str, entry_price: float, amount_bnb: float, strategy: str = "") -> dict:
    global _positions
    positions = _load()
    pos = {
        "address": address.lower(),
        "symbol": symbol,
        "entry_price": entry_price,
        "amount_bnb": amount_bnb,
        "strategy": strategy,
        "entry_at": time.time(),
        "peak_gain_pct": 0.0,
        "tp_levels_hit": [],
        "status": "open",
    }
    positions = [p for p in positions if p["address"] != address.lower()]
    positions.insert(0, pos)
    _positions = positions
    _save(positions)
    return pos


def _check_take_profit(gain_pct: float, hit: list) -> tuple[str | None, list]:
    alerts = []
    new_hit = list(hit)
    cumulative = sum(TAKE_PROFIT_TIERS[i]["sell_pct"] for i in range(len(new_hit)))
    for i, tier in enumerate(TAKE_PROFIT_TIERS):
        if i in new_hit:
            continue
        if gain_pct >= tier["gain_pct"]:
            new_hit.append(i)
            cumulative += tier["sell_pct"]
            alerts.append(
                f"📈 止盈 L{i+1}: +{tier['gain_pct']}% → 卖{tier['sell_pct']}% (累计{min(cumulative, 100)}%)"
            )
    return ("\n".join(alerts) if alerts else None), new_hit


def _check_trailing_stop(gain_pct: float, peak: float) -> str | None:
    if peak < TRAILING_STOP_ACTIVATE_PCT:
        return None
    pullback = peak - gain_pct
    if pullback >= TRAILING_STOP_PULLBACK_PCT:
        return f"🔻 移动止损: 峰值+{peak:.1f}% 回撤{pullback:.1f}%"
    return None


def evaluate_position(pos: dict, current_price: float, token_data: dict, score: float) -> list[str]:
    entry = safe_float(pos.get("entry_price"))
    if entry <= 0:
        return []
    gain_pct = ((current_price - entry) / entry) * 100
    peak = max(safe_float(pos.get("peak_gain_pct")), gain_pct)
    alerts: list[str] = []

    if gain_pct <= HARD_STOP_LOSS_PCT:
        alerts.append(f"🛑 硬止损 {HARD_STOP_LOSS_PCT}%! 当前{gain_pct:.1f}%")

    tp_alert, new_hit = _check_take_profit(gain_pct, pos.get("tp_levels_hit", []))
    if tp_alert:
        alerts.append(tp_alert)
        pos["tp_levels_hit"] = new_hit

    trail = _check_trailing_stop(gain_pct, peak)
    if trail:
        alerts.append(trail)

    security = token_data.get("security") or {}
    sell_tax = safe_float(security.get("sell_tax"))
    if sell_tax > SMART_EXIT["sell_tax_max"]:
        alerts.append(f"⚠️ 卖税{sell_tax:.1f}%过高")

    if score < SMART_EXIT["score_decay_below"]:
        alerts.append(f"⚠️ 评分降至{score:.0f}")

    pos["peak_gain_pct"] = peak
    pos["current_gain_pct"] = round(gain_pct, 2)
    pos["current_price"] = current_price
    return alerts


async def monitor_positions() -> list[dict[str, Any]]:
    global _positions
    positions = _load()
    notifications = []

    for pos in positions:
        if pos.get("status") != "open":
            continue
        addr = pos["address"]
        try:
            token_data = await token_scan.analyze_token(addr)
            price = safe_float(token_data.get("price"))
            if price <= 0:
                continue
            ev = await memecoin_score.evaluate_token(addr)
            alerts = evaluate_position(pos, price, token_data, ev["score"])
            if alerts:
                notifications.append({
                    "address": addr,
                    "symbol": pos.get("symbol", token_data.get("symbol")),
                    "gain_pct": pos.get("current_gain_pct", 0),
                    "current_price": pos.get("current_price", price),
                    "alerts": alerts,
                    "message": (
                        f"📊 {pos.get('symbol', '?')} {fmt_pct(pos.get('current_gain_pct', 0))}\n"
                        + "\n".join(alerts)
                    ),
                })
        except Exception:
            continue

    _positions = positions
    _save(positions)
    return notifications
