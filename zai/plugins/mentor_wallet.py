"""模板钱包学习 — GMGN 深挖 + AI 画像。"""

import asyncio
import json
import os
import time
from typing import Any

from core.agent import TradingAgent, _parse_json
from plugins import gmgn_intel, trade_journal
from utils.format import safe_float

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
STATE_FILE = os.path.join(DATA_DIR, "mentor_wallet.json")
DEFAULT_MENTOR_WALLET = ""

MENTOR_PROMPT = """你是 BSC four.meme 一级市场机器人训练师。
基于给定钱包的 GMGN stats/activity/holdings，提炼可复制但保守的交易规则。
必须区分“可学习规则”和“不能复制的风险”。
返回 JSON：
{
  "wallet_grade":"A|B|C|D|unknown",
  "style":"一句话打法画像",
  "entry_patterns":["入场模式"],
  "exit_patterns":["平仓/止盈/止损模式"],
  "sizing_rules":["仓位规则"],
  "copy_rules":["机器人可执行规则"],
  "avoid_rules":["必须回避"],
  "token_filters":["筛选条件"],
  "red_flags":["风险"],
  "ai_instruction":"给 bscco AI 的一段交易指令",
  "confidence":"high|medium|low"
}"""


def _ensure() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)


def _load() -> dict[str, Any]:
    _ensure()
    if not os.path.exists(STATE_FILE):
        return {"wallet": DEFAULT_MENTOR_WALLET, "chain": "bsc", "snapshots": []}
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {"wallet": DEFAULT_MENTOR_WALLET, "chain": "bsc", "snapshots": []}


def _save(data: dict[str, Any]) -> None:
    _ensure()
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _items(data: Any, *keys: str) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if not isinstance(data, dict):
        return []
    for key in keys:
        val = data.get(key)
        if isinstance(val, list):
            return [x for x in val if isinstance(x, dict)]
        if isinstance(val, dict):
            nested = _items(val, *keys)
            if nested:
                return nested
    for key in ("data", "list", "result", "items"):
        val = data.get(key)
        if isinstance(val, list):
            return [x for x in val if isinstance(x, dict)]
        if isinstance(val, dict):
            nested = _items(val, *keys)
            if nested:
                return nested
    return []


def _compact(data: Any, max_chars: int = 15000) -> str:
    text = json.dumps(data, ensure_ascii=False, default=str)
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "...TRUNCATED"


def _activity_summary(activities: list[dict[str, Any]]) -> dict[str, Any]:
    buys, sells = [], []
    symbols: dict[str, dict[str, Any]] = {}
    for item in activities:
        typ = str(item.get("type") or item.get("side") or item.get("event_type") or item.get("event") or "").lower()
        token = item.get("token") if isinstance(item.get("token"), dict) else {}
        symbol = item.get("symbol") or token.get("symbol") or item.get("base_symbol") or "?"
        amount = safe_float(item.get("cost_usd") or item.get("amount_usd") or item.get("usd_value"))
        row = symbols.setdefault(symbol, {"symbol": symbol, "buy_usd": 0.0, "sell_usd": 0.0, "buys": 0, "sells": 0})
        if "sell" in typ:
            sells.append(item)
            row["sell_usd"] += amount
            row["sells"] += 1
        else:
            buys.append(item)
            row["buy_usd"] += amount
            row["buys"] += 1
    top = sorted(symbols.values(), key=lambda x: x["buy_usd"] + x["sell_usd"], reverse=True)[:10]
    return {
        "buy_count": len(buys),
        "sell_count": len(sells),
        "top_symbols": top,
    }


def _stats_summary(stats: Any) -> dict[str, Any]:
    if not isinstance(stats, dict):
        return {}
    keys = (
        "native_balance",
        "realized_profit",
        "realized_profit_pnl",
        "unrealized_profit",
        "unrealized_profit_pnl",
        "buy",
        "sell",
        "buy_7d",
        "sell_7d",
        "winrate",
        "pnl",
        "pnl_7d",
        "total_profit",
    )
    out = {key: stats.get(key) for key in keys if key in stats}
    pnl = stats.get("pnl_stat") if isinstance(stats.get("pnl_stat"), dict) else {}
    if pnl:
        out["pnl_stat"] = {
            "token_num": pnl.get("token_num"),
            "winrate": pnl.get("winrate"),
            "avg_holding_period": pnl.get("avg_holding_period"),
            "pnl_lt_nd5_num": pnl.get("pnl_lt_nd5_num"),
            "pnl_nd5_0x_num": pnl.get("pnl_nd5_0x_num"),
            "pnl_0x_2x_num": pnl.get("pnl_0x_2x_num"),
            "pnl_2x_5x_num": pnl.get("pnl_2x_5x_num"),
            "pnl_gt_5x_num": pnl.get("pnl_gt_5x_num"),
        }
    return out


def _public_snapshot(snap: Any) -> dict[str, Any]:
    if not isinstance(snap, dict):
        return {}
    raw = snap.get("raw") if isinstance(snap.get("raw"), dict) else {}
    analysis = _guard_analysis(dict(snap.get("analysis") or {}), raw) if snap.get("analysis") else {}
    return {
        "wallet": snap.get("wallet"),
        "chain": snap.get("chain"),
        "updated_at": snap.get("updated_at"),
        "ok": snap.get("ok", False),
        "error": snap.get("error", ""),
        "gmgn_ready": snap.get("gmgn_ready"),
        "deepseek_ready": snap.get("deepseek_ready"),
        "raw": {
            "activity_summary": raw.get("activity_summary", {}),
            "holdings_unavailable": raw.get("holdings_unavailable", False),
            "stats_7d": _stats_summary(raw.get("stats_7d")),
            "stats_30d": _stats_summary(raw.get("stats_30d")),
        },
        "analysis": analysis,
    }


def _fallback_analysis(wallet: str, data: dict[str, Any], reason: str = "") -> dict[str, Any]:
    summary = _activity_summary(data.get("activities") or [])
    return {
        "wallet_grade": "unknown",
        "style": "等待 AI 或更多 GMGN 数据生成钱包画像",
        "entry_patterns": [],
        "exit_patterns": [],
        "sizing_rules": [],
        "copy_rules": [],
        "avoid_rules": [],
        "token_filters": [],
        "red_flags": [reason] if reason else [],
        "ai_instruction": f"观察模板钱包 {wallet[:8] or '待填写'}... 的买卖节奏，暂不盲目复制。",
        "confidence": "low",
        "activity_summary": summary,
    }


def _guard_analysis(parsed: dict[str, Any], raw: dict[str, Any]) -> dict[str, Any]:
    text = json.dumps(parsed, ensure_ascii=False, default=str).lower()
    stats_7d = raw.get("stats_7d") if isinstance(raw.get("stats_7d"), dict) else {}
    buy_count = safe_float(stats_7d.get("buy"))
    sell_count = safe_float(stats_7d.get("sell"))
    high_freq_hits = (
        buy_count >= 500
        or sell_count >= 500
        or any(x in text for x in ("夹子", "sandwich", "低延迟", "高频", "秒", "固定0.23", "0.23 bnb"))
    )
    if not high_freq_hits:
        return parsed

    red_flags = [
        "疑似高频/秒级/夹子类钱包，普通 Agent 不应直接复制",
        "固定大额仓位和极短持仓会放大滑点、Gas 与授权延迟风险",
        "Cobo 手机确认流程不适合复制秒级策略",
    ]
    avoid_rules = [
        "禁止把该钱包的固定仓位、秒级买卖节奏写入自动开单",
        "只把该钱包作为反面样本：识别高频套利行为后降低跟随权重",
        "寻找持仓时间更长、胜率更稳定、入场逻辑更清晰的钱包再学习",
    ]
    parsed["wallet_grade"] = "C" if parsed.get("wallet_grade") in ("A", "B") else parsed.get("wallet_grade", "C")
    parsed["style"] = "高频/秒级 four.meme 钱包，适合作为风险识别样本，不适合直接 copy。"
    parsed["copy_rules"] = [
        "不直接复制此钱包的交易动作",
        "遇到固定金额、秒级进出、近似刷单模式时，AI 自动开单降权或跳过",
        "只学习其风险标签：高频、固定仓位、短持仓、滑点/Gas 依赖",
    ]
    parsed["avoid_rules"] = list(dict.fromkeys(avoid_rules + (parsed.get("avoid_rules") or [])))[:8]
    parsed["red_flags"] = list(dict.fromkeys(red_flags + (parsed.get("red_flags") or [])))[:8]
    parsed["sizing_rules"] = [
        "禁止按该钱包 0.2 BNB 级别固定仓位复制；仍按当前钱包余额和硬上限计算",
        "低余额账户保持 0.001-0.002 BNB 级测试仓，不因导师钱包放大单笔",
    ]
    parsed["ai_instruction"] = (
        "把该钱包作为反面模板学习：识别固定仓位、秒级买卖、高频套利特征后跳过自动开单；"
        "后续只跟随持仓更久、胜率更稳定、入场理由清晰的钱包。"
    )
    parsed["confidence"] = parsed.get("confidence") or "medium"
    parsed["guarded"] = True
    return parsed


async def _run_gmgn_safe(*args: str, timeout: int = 18) -> Any:
    try:
        return await asyncio.wait_for(gmgn_intel._run_gmgn(*args), timeout=timeout)
    except (asyncio.TimeoutError, OSError):
        return None


async def deep_dive(wallet: str = DEFAULT_MENTOR_WALLET, chain: str = "bsc") -> dict[str, Any]:
    wallet = wallet.lower().strip()
    chain = chain.lower().strip() or "bsc"
    state = _load()

    if not gmgn_intel.cli_ready():
        snap = {
            "wallet": wallet,
            "chain": chain,
            "updated_at": time.time(),
            "ok": False,
            "error": "gmgn-cli 或 GMGN_API_KEY 未配置",
            "analysis": _fallback_analysis(wallet, {}, "gmgn-cli 或 GMGN_API_KEY 未配置"),
        }
        state.update({"wallet": wallet, "chain": chain, "latest": snap})
        state.setdefault("snapshots", []).insert(0, snap)
        state["snapshots"] = state["snapshots"][:8]
        _save(state)
        return snap

    stats_7d, stats_30d, activity_raw, holdings_raw, created_raw = await asyncio.gather(
        _run_gmgn_safe("portfolio", "stats", "--chain", chain, "--wallet", wallet, "--period", "7d"),
        _run_gmgn_safe("portfolio", "stats", "--chain", chain, "--wallet", wallet, "--period", "30d"),
        _run_gmgn_safe(
            "portfolio", "activity", "--chain", chain, "--wallet", wallet,
            "--type", "buy", "--type", "sell", "--limit", "80",
        ),
        _run_gmgn_safe(
            "portfolio", "holdings", "--chain", chain, "--wallet", wallet,
            "--order-by", "usd_value", "--direction", "desc", "--limit", "20",
        ),
        _run_gmgn_safe(
            "portfolio", "created-tokens", "--chain", chain, "--wallet", wallet,
            "--order-by", "token_ath_mc", "--direction", "desc",
        ),
    )

    activities = _items(activity_raw, "activities")
    holdings = _items(holdings_raw, "holdings")
    created = _items(created_raw, "tokens", "created_tokens")
    raw = {
        "stats_7d": stats_7d,
        "stats_30d": stats_30d,
        "activities": activities[:80],
        "holdings": holdings[:20],
        "created_tokens": created[:20],
        "activity_summary": _activity_summary(activities),
        "holdings_unavailable": holdings_raw is None,
    }

    agent = TradingAgent()
    if agent.configured:
        context = (
            f"模板钱包: {wallet}\n链: {chain}\n"
            f"GMGN数据:\n{_compact(raw)}"
        )
        try:
            answer = await asyncio.wait_for(agent.analyze(MENTOR_PROMPT, context, max_tokens=1200), timeout=45)
        except asyncio.TimeoutError:
            answer = f"{agent.provider} 请求超时"
        parsed_json = _parse_json(answer)
        parsed = parsed_json or _fallback_analysis(wallet, raw, answer[:180])
        parsed["_raw"] = "" if parsed_json else answer[:500]
    else:
        parsed = _fallback_analysis(wallet, raw, f"{agent.provider} 未配置")
    parsed = _guard_analysis(parsed, raw)

    trade_journal.update_learning(
        rules=(parsed.get("copy_rules") or [])[:6] + (parsed.get("entry_patterns") or [])[:4],
        avoid=(parsed.get("avoid_rules") or [])[:6] + (parsed.get("red_flags") or [])[:4],
        insight=parsed.get("ai_instruction") or parsed.get("style", ""),
    )

    snap = {
        "wallet": wallet,
        "chain": chain,
        "updated_at": time.time(),
        "ok": True,
        "gmgn_ready": True,
        "deepseek_ready": agent.configured,
        "ai_provider": agent.provider,
        "raw": raw,
        "analysis": parsed,
    }
    state.update({"wallet": wallet, "chain": chain, "latest": snap})
    state.setdefault("snapshots", []).insert(0, snap)
    state["snapshots"] = state["snapshots"][:8]
    _save(state)
    return snap


def get_state() -> dict[str, Any]:
    state = _load()
    latest = state.get("latest") or {}
    agent = TradingAgent()
    return {
        "wallet": state.get("wallet") or DEFAULT_MENTOR_WALLET,
        "chain": state.get("chain") or "bsc",
        "snapshots": [_public_snapshot(s) for s in (state.get("snapshots") or [])[:8]],
        "gmgn_ready": gmgn_intel.cli_ready(),
        "deepseek_ready": agent.configured,
        "ai_provider": agent.provider,
        "latest": _public_snapshot(latest),
    }
