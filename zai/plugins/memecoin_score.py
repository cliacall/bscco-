"""bscco 信号评分 — 六维+八席引擎（对外称 Arena，不提第三方）"""

import time
from datetime import datetime, timezone
from typing import Any

from config.strategy import (
    ARENA_AGENTS,
    DEFAULT_BUY_AMOUNT_BNB,
    SCORE_DISPLAY_MIN,
    STRONG_BUY_SCORE,
    TIER_BUY,
    TIER_MONITOR,
    TIER_WATCH,
)
from plugins import token_scan
from plugins.strategy_evolver import effective_buy_threshold, get_strategy_runtime
from utils.format import fmt_money, fmt_pct, safe_float

TIER_EMOJI = {"buy": "🟢", "watch": "🟡", "monitor": "🔵", "skip": "⚪", "reject": "🔴"}
TIER_LABEL = {"buy": "买入", "watch": "观察", "monitor": "监控", "skip": "跳过", "reject": "拒绝"}


def _classify_tier(score: float) -> str:
    if score >= TIER_BUY:
        return "buy"
    if score >= TIER_WATCH:
        return "watch"
    if score >= TIER_MONITOR:
        return "monitor"
    return "reject"


def _factor_scores(data: dict) -> dict[str, float]:
    liq = safe_float(data.get("liquidity"))
    vol = safe_float(data.get("volume"))
    mcap = safe_float(data.get("market_cap") or data.get("fdv"))
    buys = safe_float(data.get("buys_24h") or data.get("buy_count"))
    sells = safe_float(data.get("sells_24h") or data.get("sell_count"))
    ratio = buys / max(sells, 1)
    age_h = safe_float(data.get("pool_age_hours"), 999)
    sec = data.get("security") or {}

    return {
        "liquidity": min(100, liq / 500) if liq else 0,
        "volume": min(100, vol / 300) if vol else 0,
        "buy_sell_ratio": min(100, ratio * 25),
        "market_cap": max(0, min(100, 100 - mcap / 50000)) if mcap else 50,
        "pool_age": max(0, 100 - age_h * 2) if age_h < 48 else 30,
        "holder_security": _security_score(sec, data),
    }


def _security_score(sec: dict, data: dict) -> float:
    s = 50.0
    if sec.get("is_honeypot"):
        return 0
    if sec.get("is_open_source") or sec.get("contract_verified"):
        s += 20
    if not sec.get("is_proxy"):
        s += 10
    sell_tax = safe_float(sec.get("sell_tax"))
    if sell_tax <= 5:
        s += 15
    sm = int(data.get("smart_degen_count") or 0)
    s += min(15, sm * 5)
    return min(100, s)


def _weighted_score(factors: dict[str, float], weights: dict[str, int]) -> float:
    total_w = sum(weights.values()) or 1
    return sum(factors.get(k, 0) * w for k, w in weights.items()) / total_w


def score_all_agents(data: dict) -> list[dict]:
    factors = _factor_scores(data)
    gmgn_boost = safe_float(data.get("gmgn_boost"))
    runtime = get_strategy_runtime()
    results = []
    for agent in ARENA_AGENTS:
        raw = _weighted_score(factors, agent.weights) + gmgn_boost
        profile = runtime.get(agent.strategy, {})
        threshold = int(profile.get("effective_buy_threshold") or effective_buy_threshold(agent.strategy, agent.buy_threshold))
        score = round(min(100, max(0, raw)), 1)
        tier = _classify_tier(score)
        signal = "BUY" if score >= threshold else "WATCH" if score >= TIER_WATCH else "SKIP"
        results.append({
            "agent_id": agent.id,
            "agent_ticker": agent.ticker,
            "agent_name": agent.name,
            "strategy": agent.strategy,
            "score": score,
            "tier": tier,
            "tier_emoji": TIER_EMOJI.get(tier, "⚪"),
            "signal": signal,
            "buy_threshold": threshold,
            "base_buy_threshold": agent.buy_threshold,
            "strategy_weight": profile.get("weight", 1.0),
            "strategy_enabled": profile.get("enabled", True),
            "gmgn_boost": profile.get("gmgn_boost", 0),
            "factors": {k: round(v, 1) for k, v in factors.items()},
        })
    return results


def pick_best_signal(scores: list[dict]) -> dict | None:
    buys = [s for s in scores if s["signal"] == "BUY"]
    return max(buys, key=lambda x: x["score"]) if buys else None


def _pool_to_data(pool: dict, token_data: dict) -> dict:
    return {
        **token_data,
        "liquidity": safe_float(pool.get("liquidity") or token_data.get("liquidity")),
        "volume": safe_float(pool.get("volume_1h") or pool.get("volume")),
        "smart_degen_count": pool.get("smart_degen_count", 0),
        "gmgn_boost": min(15, int(pool.get("smart_degen_count") or 0) * 3),
        "pool_age_hours": _pool_age_hours(pool),
        "market_cap": safe_float(pool.get("usd_market_cap")),
    }


def _pool_age_hours(pool: dict) -> float:
    ts = pool.get("created_timestamp") or pool.get("open_timestamp")
    if not ts:
        discovered = pool.get("discovered_at")
        if discovered:
            try:
                dt = datetime.fromisoformat(str(discovered).replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return max(0.1, (time.time() - dt.timestamp()) / 3600)
            except ValueError:
                pass
        return 0.1 if pool.get("stage") == "new_creation" else 1.0
    return max(0.1, (time.time() - safe_float(ts, time.time())) / 3600)


async def evaluate_token(address: str, pool: dict | None = None) -> dict[str, Any]:
    from plugins.fourmeme_scan import ensure_fourmeme, get_candidates, get_recent_pools

    addr = address.lower().strip()
    if not pool:
        pool = next(
            (p for p in get_candidates() + get_recent_pools()
             if (p.get("address") or p.get("token_address") or "").lower() == addr),
            None,
        )
    if not pool:
        from plugins.fourmeme_scan import get_pool_by_address, lookup_fourmeme_token
        pool = await lookup_fourmeme_token(addr)
        if not pool:
            ok, msg = await ensure_fourmeme(addr)
            if not ok:
                raise ValueError(msg)
            pool = get_pool_by_address(addr)

    token_data = await token_scan.analyze_token(addr)
    if pool:
        data = _pool_to_data(pool, token_data)
    else:
        data = {**token_data, "pool_age_hours": 24}

    agent_scores = score_all_agents(data)
    best = pick_best_signal(agent_scores)
    top = max(agent_scores, key=lambda s: s["score"])
    composite = top["score"]
    tier = _classify_tier(composite)

    return {
        "symbol": token_data.get("symbol", pool.get("symbol", "?") if pool else "?"),
        "name": token_data.get("name", ""),
        "address": address.lower(),
        "price": safe_float(token_data.get("price")),
        "price_fmt": fmt_money(safe_float(token_data.get("price"))),
        "volume_fmt": fmt_money(safe_float(token_data.get("volume"))),
        "liquidity_fmt": fmt_money(safe_float(token_data.get("liquidity"))),
        "change_fmt": fmt_pct(safe_float(token_data.get("change_24h"))),
        "risk_level": token_data.get("risk_level", "—"),
        "risk_flags": token_data.get("risk_flags", []),
        "score": composite,
        "tier": tier,
        "tier_emoji": TIER_EMOJI.get(tier, "⚪"),
        "tier_label": TIER_LABEL.get(tier, tier),
        "signal": best["signal"] if best else top["signal"],
        "displayable": composite >= SCORE_DISPLAY_MIN,
        "strategy": (best or top)["strategy"],
        "agent_scores": agent_scores,
        "top_agent": top,
        "best_signal": best,
        "launchpad": (pool or {}).get("launchpad", "fourmeme"),
        "stage": (pool or {}).get("stage", ""),
        "is_primary": True,
    }


def score_pool(pool: dict) -> dict[str, Any]:
    """同步快速评分（扫描循环用）"""
    data = {
        "liquidity": safe_float(pool.get("liquidity")),
        "volume": safe_float(pool.get("volume_1h") or pool.get("volume")),
        "smart_degen_count": pool.get("smart_degen_count", 0),
        "gmgn_boost": min(15, int(pool.get("smart_degen_count") or 0) * 3),
        "pool_age_hours": _pool_age_hours(pool),
        "market_cap": safe_float(pool.get("usd_market_cap")),
        "security": {"sell_tax": 0},
    }
    if safe_float(pool.get("rug_ratio"), 0) > 0.3:
        data["security"] = {"is_honeypot": True}
    agent_scores = score_all_agents(data)
    best = pick_best_signal(agent_scores)
    top = max(agent_scores, key=lambda s: s["score"])
    tier = _classify_tier(top["score"])
    return {
        "score": top["score"],
        "tier": tier,
        "tier_emoji": TIER_EMOJI.get(tier, "⚪"),
        "tier_label": TIER_LABEL.get(tier, tier),
        "signal": best["signal"] if best else top["signal"],
        "displayable": top["score"] >= SCORE_DISPLAY_MIN,
        "strategy": (best or top)["strategy"],
        "agent_scores": agent_scores,
        "top_agent": top,
        "best_signal": best,
    }


def format_signal_card(ev: dict, amount_bnb: float = DEFAULT_BUY_AMOUNT_BNB) -> str:
    top = ev.get("top_agent") or {}
    return (
        f"{ev['tier_emoji']} {ev['symbol']} {ev['score']}/100\n"
        f"{ev['price_fmt']} | {top.get('agent_ticker', '')} {top.get('strategy', '')}\n"
        f"信号 {ev['signal']} | {amount_bnb} BNB\n`{ev['address']}`"
    )
