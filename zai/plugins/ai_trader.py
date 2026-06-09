"""AI 自主交易 — 扫描高分 four.meme → ZAI/DeepSeek 决策 → Cobo 下单"""

import json
import logging
import os
import time
from typing import Any

from config.strategy import DEFAULT_BUY_AMOUNT_BNB, STRONG_BUY_SCORE
from core.agent import TradingAgent
from plugins import gmgn_intel, trade_journal, trading
from plugins.fourmeme_scan import get_candidates
from plugins.gmgn_scout import get_opportunities, opportunities_to_pools
from plugins.position_manager import add_position, get_positions
from utils.format import safe_float

logger = logging.getLogger(__name__)

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
STATE_FILE = os.path.join(DATA_DIR, "ai_trader_state.json")

TRADE_DECISION = """你是 bscco 自主交易 AI。只交易 four.meme 发射币（含已射出）。
必须根据钱包余额、预留 gas、剩余仓位和系统建议金额决定本笔金额。
返回 JSON：{"action":"buy|sell|hold","confidence":"high|medium|low","reason":"一句话","amount_bnb":0.002}"""


def _env_float(name: str, default: float) -> float:
    return safe_float(os.getenv(name), default)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _ai_max_positions() -> int:
    return max(1, _env_int("AI_MAX_POSITIONS", 3))


def _ai_cycle_interval() -> int:
    return max(10, _env_int("AI_CYCLE_INTERVAL", 60))


def _default_state() -> dict[str, Any]:
    return {
        "enabled": False,
        "auto_trade": True,
        "last_run": 0,
        "last_learning_run": 0,
        "decisions": [],
        "trades": [],
    }


async def _wallet_sizing(open_count: int) -> dict[str, Any]:
    """Balance-aware AI sizing. Env knobs keep the hard risk limits explicit."""
    reserve_bnb = max(0.0, _env_float("AI_RESERVE_BNB", 0.002))
    min_bnb = max(0.0, _env_float("AI_MIN_BUY_BNB", 0.001))
    max_bnb = max(min_bnb, _env_float("AI_MAX_BUY_BNB", DEFAULT_BUY_AMOUNT_BNB))
    pct = max(0.01, min(1.0, _env_float("AI_TRADE_BALANCE_PCT", 0.25)))
    max_positions = _ai_max_positions()
    remaining_slots = max(0, max_positions - open_count)

    bal: dict[str, Any] = {"configured": False, "bnb": 0.0}
    try:
        from plugins.portfolio_sync import get_live_bnb_balance
        bal = await get_live_bnb_balance()
    except Exception as e:
        logger.warning("AI sizing balance failed: %s", e)

    balance_bnb = safe_float(bal.get("bnb"))
    spendable_bnb = max(0.0, balance_bnb - reserve_bnb)
    slot_cap = spendable_bnb / remaining_slots if remaining_slots else 0.0
    pct_cap = spendable_bnb * pct
    hard_cap = max(0.0, min(max_bnb, slot_cap, pct_cap))
    suggested = round(hard_cap, 6) if hard_cap >= min_bnb else 0.0

    return {
        "wallet_configured": bool(bal.get("configured")),
        "balance_bnb": round(balance_bnb, 6),
        "reserve_bnb": reserve_bnb,
        "spendable_bnb": round(spendable_bnb, 6),
        "open_positions": open_count,
        "max_positions": max_positions,
        "remaining_slots": remaining_slots,
        "min_bnb": min_bnb,
        "max_bnb": max_bnb,
        "balance_pct": pct,
        "suggested_bnb": suggested,
        "hard_cap_bnb": round(hard_cap, 6),
        "source": bal.get("source") or bal.get("cobo_source") or "unknown",
        "reason": (
            f"余额{balance_bnb:.6f} BNB，预留{reserve_bnb:.6f}，"
            f"剩余仓位{remaining_slots}/{max_positions}，建议本笔{suggested:.6f} BNB"
        ),
    }


def _clamp_ai_amount(decision: dict, sizing: dict[str, Any]) -> float:
    requested = safe_float(decision.get("amount_bnb"), sizing.get("suggested_bnb", 0.0))
    suggested = safe_float(sizing.get("suggested_bnb"))
    hard_cap = safe_float(sizing.get("hard_cap_bnb"))
    min_bnb = safe_float(sizing.get("min_bnb"))
    if hard_cap < min_bnb or suggested <= 0:
        return 0.0
    if requested <= 0:
        requested = suggested
    return round(max(min_bnb, min(requested, hard_cap)), 6)


def _load_state() -> dict:
    os.makedirs(DATA_DIR, exist_ok=True)
    if not os.path.exists(STATE_FILE):
        return _default_state()
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            state = json.load(f)
            return {**_default_state(), **state}
    except (json.JSONDecodeError, OSError):
        return _default_state()


def _save_state(state: dict) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


def get_state() -> dict:
    s = _load_state()
    open_count = len(get_positions())
    s["open_positions"] = open_count
    s["cycle_interval"] = _ai_cycle_interval()
    s["next_run_in"] = max(0, int(_ai_cycle_interval() - (time.time() - safe_float(s.get("last_run")))))
    return s


def set_enabled(enabled: bool, auto_trade: bool | None = None) -> dict:
    state = _load_state()
    state["enabled"] = bool(enabled)
    if auto_trade is not None:
        state["auto_trade"] = bool(auto_trade)
    state["updated_at"] = time.time()
    if enabled and not state.get("started_at"):
        state["started_at"] = time.time()
    if not enabled:
        state["stopped_at"] = time.time()
    _save_state(state)
    return get_state()


async def _ai_decide(pool: dict, gmgn_ctx: str, sizing: dict[str, Any]) -> dict:
    agent = TradingAgent()
    if not agent.configured:
        return {"action": "hold", "reason": "AI 未配置", "confidence": "low"}
    prompt = (
        f"币 {pool.get('symbol')} 分{pool.get('score')} 阶段{pool.get('stage')}\n"
        f"CA `{pool.get('address')}`\n聪明钱:\n{gmgn_ctx}\n"
        f"钱包余额: {sizing.get('balance_bnb')} BNB，可用: {sizing.get('spendable_bnb')} BNB，"
        f"预留: {sizing.get('reserve_bnb')} BNB，仓位: {sizing.get('open_positions')}/{sizing.get('max_positions')}。\n"
        f"本笔系统建议 {sizing.get('suggested_bnb')} BNB，硬上限 {sizing.get('hard_cap_bnb')} BNB，"
        f"最低 {sizing.get('min_bnb')} BNB。是否买入？amount_bnb 不得超过硬上限。"
    )
    raw = await agent.analyze(TRADE_DECISION, prompt, max_tokens=400)
    from core.agent import _parse_json
    parsed = _parse_json(raw) or {}
    if not parsed.get("action"):
        parsed = {"action": "hold", "reason": raw[:120], "confidence": "low"}
    return parsed


async def auto_trade_cycle(force: bool = False) -> dict[str, Any]:
    """每轮扫描后：AI 评估最高分候选，可选自动买入"""
    state = _load_state()
    if not state.get("enabled"):
        _save_state(state)
        return {"skipped": True, "reason": "AI 机器人未启动"}
    if os.getenv("AI_AUTO_TRADE", "true").lower() in ("0", "false", "no"):
        state["enabled"] = False
        _save_state(state)
        return {"skipped": True, "reason": "AI_AUTO_TRADE=off"}

    interval = _ai_cycle_interval()
    now = time.time()
    if not force and now - safe_float(state.get("last_run")) < interval:
        return {"skipped": True, "reason": "等待下一轮", "next_run_in": max(0, int(interval - (now - safe_float(state.get("last_run")))))}

    state["enabled"] = True
    state["last_run"] = now
    results: list[dict] = []

    scout_map = {p["address"]: p for p in opportunities_to_pools() if p.get("address")}
    merged_pools = {
        p.get("address"): p
        for p in get_candidates() + opportunities_to_pools()
        if p.get("address")
    }
    all_pools = list(merged_pools.values())
    candidates = [c for c in all_pools if (c.get("score") or 0) >= STRONG_BUY_SCORE]
    if not candidates:
        candidates = sorted(all_pools, key=lambda x: x.get("score", 0), reverse=True)[:5]

    intel = await gmgn_intel.get_intel()
    gmgn_ctx = intel.get("ai_summary") or gmgn_intel.format_for_ai(
        intel.get("buys", []), intel.get("clusters", {}),
    )

    positions = get_positions()
    open_addrs = {p["address"] for p in positions}
    sizing = await _wallet_sizing(len(positions))
    state["last_sizing"] = sizing

    for pool in candidates[:3]:
        addr = pool.get("address", "")
        if addr in open_addrs:
            continue
        scout = scout_map.get(addr, {})
        pool_ctx = pool
        if scout.get("scout_reason"):
            gmgn_ctx = f"{gmgn_ctx}\n机会: {scout.get('scout_reason')} [{scout.get('source')}]"
        decision = await _ai_decide(pool_ctx, gmgn_ctx, sizing)
        entry = {
            "ts": time.time(),
            "symbol": pool.get("symbol"),
            "address": addr,
            "score": pool.get("score"),
            "stage": pool.get("stage"),
            "sizing": sizing,
            **decision,
        }
        results.append(entry)
        state["decisions"] = [entry] + (state.get("decisions") or [])[:19]

        if decision.get("action") != "buy" or decision.get("confidence") == "low":
            entry["skipped"] = "AI 未选择买入"
            continue
        if not state.get("auto_trade", True):
            entry["skipped"] = "只学习模式，未提交交易"
            continue
        from plugins.cobo_pact import get_pact_status
        if (await get_pact_status()).get("status") != "active":
            entry["skipped"] = "Cobo Pact 未批准"
            continue
        if len(get_positions()) >= _ai_max_positions():
            entry["skipped"] = "仓位已满"
            continue

        amount = _clamp_ai_amount(decision, sizing)
        entry["amount_bnb"] = amount
        if amount <= 0:
            entry["skipped"] = "余额不足或低于最小开单金额"
            continue
        buy = await trading.buy_token(addr, amount)
        entry["trade"] = buy
        if buy.get("success"):
            from plugins import account_notify, memecoin_score
            ev = await memecoin_score.evaluate_token(addr, pool)
            strategy = pool.get("strategy") or ev.get("strategy") or "ai_auto"
            add_position(addr, ev["symbol"], ev.get("price", 0), amount, strategy)
            await account_notify.notify_open(ev["symbol"], addr, amount, buy)
            await trade_journal.record_open(
                ev["symbol"], addr, amount * 600, ev.get("price", 0),
                strategy, 0, [], reason=decision.get("reason", ""),
            )
            state["trades"] = [entry] + (state.get("trades") or [])[:19]
            logger.info("🤖 AI 买入 %s %.4f BNB", ev["symbol"], amount)

    _save_state(state)
    return {"decisions": results, "count": len(results)}


async def run_ai_test_once() -> dict[str, Any]:
    """Force one AI analysis on the latest four.meme new_creation token (no auto-buy)."""
    from plugins import fourmeme_api
    from plugins.fourmeme_scan import scan_fourmeme

    await scan_fourmeme()
    raw = await fourmeme_api.fetch_new_creations(20)
    candidates = get_candidates()
    pool = None
    for src in raw + candidates:
        if src.get("stage") == "new_creation":
            pool = src
            break
    if not pool and candidates:
        pool = candidates[0]
    if not pool and raw:
        pool = raw[0]
    if not pool:
        return {"ok": False, "message": "four.meme 平台暂无新创建代币"}

    from plugins import memecoin_score

    try:
        sc = memecoin_score.score_pool(pool)
        pool.update(sc)
    except Exception:
        pass

    intel = await gmgn_intel.get_intel()
    gmgn_ctx = intel.get("ai_summary") or gmgn_intel.format_for_ai(
        intel.get("buys", []), intel.get("clusters", {}),
    )
    gmgn_ctx = f"Source: four.meme platform API (agentic launchpad)\n{gmgn_ctx}"

    sizing = await _wallet_sizing(len(get_positions()))
    decision = await _ai_decide(pool, gmgn_ctx, sizing)
    decision["amount_bnb"] = _clamp_ai_amount(decision, sizing)
    entry = {
        "ts": time.time(),
        "symbol": pool.get("symbol"),
        "address": pool.get("address"),
        "score": pool.get("score"),
        "stage": pool.get("stage"),
        "source": pool.get("source", "fourmeme_api"),
        "test_run": True,
        "sizing": sizing,
        **decision,
    }

    state = _load_state()
    state["decisions"] = [entry] + (state.get("decisions") or [])[:19]
    state["last_test_run"] = time.time()
    _save_state(state)

    agent = TradingAgent()
    return {
        "ok": True,
        "message": f"已分析 {pool.get('symbol')} · 决策：{decision.get('action', 'hold')}",
        "pool": {
            "symbol": pool.get("symbol"),
            "address": pool.get("address"),
            "stage": pool.get("stage"),
            "score": pool.get("score"),
            "progress": pool.get("progress"),
            "source": pool.get("source"),
        },
        "decision": entry,
        "sizing": sizing,
        "ai_configured": agent.configured,
    }
