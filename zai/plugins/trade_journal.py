"""交易日志 + AI反思 + GMGN聪明钱自循环记忆"""

import json
import os
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from core.agent import TradingAgent
from plugins import gmgn_intel

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
JOURNAL_FILE = os.path.join(DATA_DIR, "trade_journal.json")
SIGNAL_QUEUE_FILE = os.path.join(DATA_DIR, "signal_queue.json")
TZ = ZoneInfo("Asia/Shanghai")


def _ensure() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)


def _load(path: str) -> list | dict:
    _ensure()
    if not os.path.exists(path):
        default = {"trades": [], "daybook": [], "equity_usd": 100.0, "learning": {"rules": [], "avoid": []}}
        return [] if "queue" in path else default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        default = {"trades": [], "daybook": [], "equity_usd": 100.0, "learning": {"rules": [], "avoid": []}}
        return [] if "queue" in path else default


def _save(path: str, data) -> None:
    _ensure()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _today() -> str:
    return datetime.now(TZ).strftime("%Y-%m-%d")


def get_equity() -> float:
    return float(_load(JOURNAL_FILE).get("equity_usd", 100.0))


def set_equity(v: float) -> None:
    j = _load(JOURNAL_FILE)
    j["equity_usd"] = v
    _save(JOURNAL_FILE, j)


def get_learning() -> dict:
    j = _load(JOURNAL_FILE)
    return j.get("learning", {"rules": [], "avoid": [], "last_insight": ""})


def update_learning(rules: list[str] | None = None, avoid: list[str] | None = None, insight: str = "") -> None:
    j = _load(JOURNAL_FILE)
    learning = j.setdefault("learning", {"rules": [], "avoid": [], "last_insight": ""})
    if rules:
        for r in rules:
            if r and r not in learning["rules"]:
                learning["rules"].insert(0, r)
        learning["rules"] = learning["rules"][:15]
    if avoid:
        for a in avoid:
            if a and a not in learning["avoid"]:
                learning["avoid"].insert(0, a)
        learning["avoid"] = learning["avoid"][:10]
    if insight:
        learning["last_insight"] = insight[:500]
    _save(JOURNAL_FILE, j)


def _history_for_ai(limit: int = 5) -> str:
    """最近复盘教训 → 供下次开单/平仓 AI 参考"""
    learning = get_learning()
    lines = []
    if learning.get("last_insight"):
        lines.append(f"上轮进化: {learning['last_insight']}")
    for r in learning.get("rules", [])[:5]:
        lines.append(f"规则: {r}")
    for a in learning.get("avoid", [])[:3]:
        lines.append(f"避免: {a}")
    closed = get_recent_closed(limit)
    for t in closed:
        ref = t.get("close_reflection") or {}
        lesson = ref.get("lesson") or ref.get("next_rule", "")
        if lesson:
            lines.append(f"{t.get('symbol')} {t.get('pnl', 0):+.2f}: {lesson}")
    return "\n".join(lines) if lines else "暂无历史教训"


async def _gmgn_ctx_for_token(address: str) -> str:
    intel = await gmgn_intel.get_intel()
    if not intel.get("available"):
        return "暂无聪明钱数据（Bot 自动拉取中）"
    return gmgn_intel.format_for_ai(
        intel.get("smartmoney", []),
        intel.get("clusters", {}),
        address,
    )


def _fourmeme_only(address: str) -> bool:
    from plugins.fourmeme_scan import get_tracked_addresses
    addr = address.lower().strip()
    if addr in get_tracked_addresses():
        return True
    queue = _load(SIGNAL_QUEUE_FILE)
    if isinstance(queue, list):
        for q in queue:
            if q.get("address") == addr and q.get("verified_fourmeme"):
                return True
    return False


def add_signal(
    symbol: str, address: str, score: float, strategy: str = "",
    source: str = "", stage: str = "", reason: str = "",
) -> None:
    addr = address.lower().strip()
    if not addr.startswith("0x"):
        return
    queue = _load(SIGNAL_QUEUE_FILE)
    if not isinstance(queue, list):
        queue = []
    queue = [q for q in queue if q.get("address") != addr]
    queue.insert(0, {
        "symbol": symbol, "address": addr,
        "score": score, "strategy": strategy,
        "source": source or strategy.replace("gmgn_", ""),
        "stage": stage,
        "reason": reason,
        "verified_fourmeme": True,
        "ts": time.time(),
    })
    _save(SIGNAL_QUEUE_FILE, queue[:80])


def get_signal_queue(min_score: float = 5) -> list[dict]:
    queue = _load(SIGNAL_QUEUE_FILE)
    if not isinstance(queue, list):
        return []
    out = [
        q for q in queue
        if q.get("score", 0) >= min_score
        and (q.get("verified_fourmeme") or _fourmeme_only(q.get("address", "")))
    ]
    out.sort(key=lambda x: x.get("score", 0), reverse=True)
    return out[:20]


def purge_non_fourmeme_signals() -> int:
    """清理非 four.meme 信号（Pancake 等）"""
    queue = _load(SIGNAL_QUEUE_FILE)
    if not isinstance(queue, list):
        _save(SIGNAL_QUEUE_FILE, [])
        return 0
    kept = [q for q in queue if _fourmeme_only(q.get("address", ""))]
    removed = len(queue) - len(kept)
    _save(SIGNAL_QUEUE_FILE, kept)
    return removed


def get_ai_thoughts(limit: int = 12) -> list[dict]:
    """AI 开/平仓思考 — 供前端展示"""
    thoughts: list[dict] = []
    for t in get_open_positions():
        ref = t.get("open_reflection") or {}
        if ref.get("why") or ref.get("risk"):
            thoughts.append({
                "type": "open",
                "symbol": t.get("symbol", "?"),
                "address": t.get("address", ""),
                "ts": t.get("open_at", 0),
                "why": ref.get("why", ""),
                "risk": ref.get("risk", ""),
                "confidence": ref.get("confidence", ""),
                "gmgn_align": ref.get("gmgn_align", ""),
            })
    for t in get_recent_closed(limit):
        ref = t.get("close_reflection") or {}
        if ref.get("lesson") or ref.get("root_cause"):
            thoughts.append({
                "type": "close",
                "symbol": t.get("symbol", "?"),
                "address": t.get("address", ""),
                "ts": t.get("close_at", 0),
                "outcome": ref.get("outcome", ""),
                "root_cause": ref.get("root_cause", ""),
                "lesson": ref.get("lesson", ""),
                "next_rule": ref.get("next_rule", ""),
                "pnl": t.get("pnl", 0),
            })
    thoughts.sort(key=lambda x: x.get("ts", 0), reverse=True)
    return thoughts[:limit]


def clear_old_signals(max_age_hours: float = 12) -> None:
    queue = _load(SIGNAL_QUEUE_FILE)
    if not isinstance(queue, list):
        return
    cutoff = time.time() - max_age_hours * 3600
    _save(SIGNAL_QUEUE_FILE, [q for q in queue if q.get("ts", 0) > cutoff])


async def record_open(
    symbol: str, address: str, amount_usd: float, entry_price: float,
    strategy: str, sl: float, tps: list[float], reason: str = "",
) -> dict:
    agent = TradingAgent()
    gmgn_ctx = await _gmgn_ctx_for_token(address)
    history = _history_for_ai()

    trade = {
        "id": f"t_{int(time.time())}",
        "symbol": symbol, "address": address.lower(),
        "amount_usd": amount_usd, "entry_price": entry_price,
        "strategy": strategy, "sl": sl, "tps": tps,
        "gmgn_at_open": gmgn_ctx,
        "open_at": time.time(), "date": _today(),
        "status": "open", "leverage": 5,
    }

    if agent.configured and not reason:
        ref = await agent.reflect_open(trade, gmgn_ctx, history)
        trade["open_reflection"] = ref
        trade["reason"] = ref.get("why", strategy)
    else:
        trade["reason"] = reason or strategy
        trade["open_reflection"] = {"why": trade["reason"]}

    j = _load(JOURNAL_FILE)
    j.setdefault("trades", []).insert(0, trade)
    _save(JOURNAL_FILE, j)
    return trade


async def record_close(
    address: str, exit_price: float, pnl: float,
    close_type: str = "profit", reason: str = "",
) -> dict | None:
    j = _load(JOURNAL_FILE)
    trade = None
    for t in j.get("trades", []):
        if t.get("address") == address.lower() and t.get("status") == "open":
            trade = t
            break
    if not trade:
        return None

    trade["status"] = "closed"
    trade["exit_price"] = exit_price
    trade["pnl"] = pnl
    trade["close_type"] = close_type
    trade["close_at"] = time.time()

    agent = TradingAgent()
    gmgn_ctx = await _gmgn_ctx_for_token(address)
    trade["gmgn_at_close"] = gmgn_ctx
    history = _history_for_ai()

    if agent.configured and not reason:
        ref = await agent.reflect_close(trade, gmgn_ctx, history)
        trade["close_reflection"] = ref
        if ref.get("next_rule"):
            update_learning(rules=[ref["next_rule"]])
    else:
        trade["close_reflection"] = {"lesson": reason or f"盈亏{pnl:+.2f}"}

    ref = trade.get("close_reflection", {})
    entry = {
        "symbol": trade["symbol"], "pnl": pnl,
        "reflection": ref.get("root_cause") or ref.get("lesson", ""),
        "lesson": ref.get("lesson") or ref.get("next_rule", ""),
        "next_rule": ref.get("next_rule", ""),
        "outcome": ref.get("outcome", "win" if pnl > 0 else "loss"),
        "date": _today(),
    }
    j.setdefault("daybook", []).insert(0, entry)
    j["equity_usd"] = j.get("equity_usd", 100) + pnl
    _save(JOURNAL_FILE, j)
    return trade


def get_open_positions() -> list[dict]:
    j = _load(JOURNAL_FILE)
    return [t for t in j.get("trades", []) if t.get("status") == "open"]


def get_recent_closed(limit: int = 10) -> list[dict]:
    j = _load(JOURNAL_FILE)
    closed = [t for t in j.get("trades", []) if t.get("status") == "closed"]
    closed.sort(key=lambda t: t.get("close_at", 0), reverse=True)
    return closed[:limit]


def get_today_trades() -> list[dict]:
    j = _load(JOURNAL_FILE)
    return [t for t in j.get("trades", []) if t.get("date") == _today() and t.get("status") == "closed"]


def get_today_daybook() -> list[dict]:
    j = _load(JOURNAL_FILE)
    return [d for d in j.get("daybook", []) if d.get("date") == _today()]


def trades_summary_for_ai(limit: int = 10) -> str:
    """交易复盘摘要 → AI 进化输入"""
    closed = get_recent_closed(limit)
    if not closed:
        return "暂无已平仓交易"
    lines = []
    for t in closed:
        ref = t.get("close_reflection") or {}
        open_ref = t.get("open_reflection") or {}
        lines.append(
            f"{t.get('symbol')} {t.get('strategy')} pnl={t.get('pnl', 0):+.2f} "
            f"开单:{open_ref.get('why', '?')[:40]} "
            f"根因:{ref.get('root_cause', '?')[:40]} "
            f"下条规则:{ref.get('next_rule', '-')[:40]}"
        )
    stats = strategy_stats(closed)
    lines.append("策略统计:" + ", ".join(
        f"{k} {v['wins']}/{v['wins']+v['losses']}" for k, v in stats.items()
    ))
    return "\n".join(lines)


def strategy_stats(trades: list[dict] | None = None) -> dict[str, dict]:
    j = _load(JOURNAL_FILE)
    closed = trades or [t for t in j.get("trades", []) if t.get("status") == "closed"]
    stats: dict[str, dict] = {}
    for t in closed:
        s = t.get("strategy", "unknown")
        if s not in stats:
            stats[s] = {"wins": 0, "losses": 0, "pnl": 0.0}
        if t.get("pnl", 0) > 0:
            stats[s]["wins"] += 1
        else:
            stats[s]["losses"] += 1
        stats[s]["pnl"] += t.get("pnl", 0)
    return stats


def period_stats(hours: int = 12) -> dict:
    j = _load(JOURNAL_FILE)
    cutoff = time.time() - hours * 3600
    closed = [t for t in j.get("trades", []) if t.get("status") == "closed" and t.get("close_at", 0) > cutoff]
    if not closed:
        return {"trades": 0, "win_rate": 0, "ev": 0, "sharpe": 0, "max_dd": 0}
    pnls = [t.get("pnl", 0) for t in closed]
    wins = sum(1 for p in pnls if p > 0)
    ev = sum(pnls) / len(pnls)
    import statistics
    sharpe = (statistics.mean(pnls) / statistics.stdev(pnls)) if len(pnls) > 1 else 0
    cum, peak, max_dd = 0, 0, 0
    for p in pnls:
        cum += p
        peak = max(peak, cum)
        max_dd = max(max_dd, peak - cum)
    return {
        "trades": len(closed), "win_rate": wins / len(closed) * 100,
        "ev": ev, "sharpe": sharpe, "max_dd": max_dd,
    }
