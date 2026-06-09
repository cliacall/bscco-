"""策略自我进化 — GMGN聪明钱 + four.meme 交易复盘 + AI"""

import json
import logging
import os
import time
from typing import Any

from config.strategy import ARENA_AGENTS
from core.agent import TradingAgent
from plugins import gmgn_intel, trade_journal
from utils.format import safe_float

logger = logging.getLogger(__name__)

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
STATE_FILE = os.path.join(DATA_DIR, "strategy_state.json")
CURRENT_VERSION = 4
MIN_WEIGHT = 0.2
MAX_WEIGHT = 1.5
DISABLE_WEIGHT_BELOW = 0.35

LEGACY_STRATEGY_ALIASES = {
    "momentum_rider": "momentum_chase",
    "momentum_fade": "fade_spike",
    "oversold_bounce": "balanced",
    "sniper_entry": "early_entry",
    "overbought_reversal": "fade_spike",
}

DEFAULT_STRATEGIES = {
    **{a.strategy: {"weight": 1.0, "enabled": True, "wins": 0, "losses": 0, "gmgn_boost": 0.0} for a in ARENA_AGENTS},
    "gmgn_smartmoney": {"weight": 1.0, "enabled": True, "wins": 0, "losses": 0, "gmgn_boost": 0.0},
}


def _default_state() -> dict:
    return {
        "version": CURRENT_VERSION,
        "strategies": {k: dict(v) for k, v in DEFAULT_STRATEGIES.items()},
        "next_criteria": ["仅 four.meme 一级", "聪明钱集群≥2"],
        "avoid_patterns": ["已毕业二级", "rug>0.3"],
        "ai_insight": "",
        "last_upgrade": 0,
        "last_intel": {},
    }


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _clean_str_list(value: Any, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        text = str(item).strip()
        if text and text not in out:
            out.append(text)
    return out[:limit]


def _merge_strategy(base: dict, saved: dict) -> dict:
    merged = dict(base)
    merged["weight"] = round(_clamp(safe_float(saved.get("weight"), merged["weight"]), MIN_WEIGHT, MAX_WEIGHT), 3)
    merged["enabled"] = bool(saved.get("enabled", merged["enabled"]))
    merged["wins"] = max(0, int(safe_float(saved.get("wins"), merged["wins"])))
    merged["losses"] = max(0, int(safe_float(saved.get("losses"), merged["losses"])))
    merged["gmgn_boost"] = round(_clamp(safe_float(saved.get("gmgn_boost"), merged["gmgn_boost"]), 0.0, 1.0), 3)
    return merged


def _normalize_state(raw: dict | None) -> dict:
    default = _default_state()
    if not isinstance(raw, dict):
        return default

    normalized = dict(default)
    normalized["version"] = CURRENT_VERSION
    normalized["ai_insight"] = str(raw.get("ai_insight") or "")[:500]
    normalized["last_upgrade"] = safe_float(raw.get("last_upgrade"), 0)
    normalized["last_intel"] = raw.get("last_intel") if isinstance(raw.get("last_intel"), dict) else {}

    criteria = _clean_str_list(raw.get("next_criteria"), 8)
    avoid = _clean_str_list(raw.get("avoid_patterns"), 8)
    if criteria:
        normalized["next_criteria"] = criteria
    if avoid:
        normalized["avoid_patterns"] = avoid

    saved_strategies = raw.get("strategies") if isinstance(raw.get("strategies"), dict) else {}
    strategies = {k: dict(v) for k, v in DEFAULT_STRATEGIES.items()}
    for key, saved in saved_strategies.items():
        if not isinstance(saved, dict):
            continue
        target = key if key in strategies else LEGACY_STRATEGY_ALIASES.get(key)
        if target and target in strategies:
            strategies[target] = _merge_strategy(strategies[target], saved)
    normalized["strategies"] = strategies
    return normalized


def _load() -> dict:
    os.makedirs(DATA_DIR, exist_ok=True)
    if not os.path.exists(STATE_FILE):
        return _default_state()
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
            state = _normalize_state(raw)
            if state != raw:
                _save(state)
            return state
    except (json.JSONDecodeError, OSError):
        return _default_state()


def _save(state: dict) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


def get_state() -> dict:
    return _load()


def apply_gmgn_boost(intel: dict) -> list[dict]:
    boosted = []
    state = _load()
    clusters = intel.get("clusters") or {}
    strength_boost = {"strong": 3.0, "medium": 1.5, "weak": 0.5}

    from plugins.fourmeme_scan import get_tracked_addresses
    fourmeme_addrs = get_tracked_addresses()

    for addr, c in clusters.items():
        if addr.lower() not in fourmeme_addrs:
            continue
        sym = c.get("symbol", "?")
        strength = c.get("strength", "medium")
        boost = strength_boost.get(strength, 1.0) + min(1.0, c.get("wallet_count", 1) * 0.3)
        trade_journal.add_signal(sym, addr, score=5 + boost, strategy="gmgn_smartmoney")
        boosted.append({"symbol": sym, "address": addr, "boost": boost, "strength": strength})
        st = state["strategies"].setdefault("gmgn_smartmoney", dict(DEFAULT_STRATEGIES["gmgn_smartmoney"]))
        st["gmgn_boost"] = round(_clamp(safe_float(st.get("gmgn_boost")) + 0.05 * boost, 0.0, 1.0), 3)

    state["last_intel"] = {"clusters": len(clusters), "ts": time.time()}
    _save(state)
    return boosted


def _rule_adjustments(state: dict, stats: dict) -> list[str]:
    adjustments: list[str] = []
    for key, st in state["strategies"].items():
        s = stats.get(key, {})
        w, l = s.get("wins", 0), s.get("losses", 0)
        total = w + l
        st["wins"], st["losses"] = w, l
        if total < 3:
            continue
        wr = w / total
        old_w = safe_float(st.get("weight"), 1.0)
        if wr >= 0.6:
            st["enabled"] = True
            st["weight"] = round(min(MAX_WEIGHT, old_w + 0.1), 3)
            if abs(st["weight"] - old_w) > 0.01:
                adjustments.append(f"{key} {old_w:.1f}→{st['weight']:.1f}")
        elif wr < 0.35:
            st["weight"] = round(max(MIN_WEIGHT, old_w - 0.2), 3)
            if st["weight"] <= DISABLE_WEIGHT_BELOW:
                st["enabled"] = False
                adjustments.append(f"{key} 禁用")
            else:
                adjustments.append(f"{key} {old_w:.1f}→{st['weight']:.1f}")
    return adjustments


async def _ai_evolve(state: dict, intel: dict) -> list[str]:
    agent = TradingAgent()
    if not agent.configured:
        return []

    gmgn_summary = intel.get("ai_summary") or gmgn_intel.format_for_ai(
        intel.get("smartmoney", []), intel.get("clusters", {}),
    )
    trades_summary = trade_journal.trades_summary_for_ai()
    state_summary = "\n".join(
        f"{k} w={s.get('weight', 1):.1f}" for k, s in state["strategies"].items()
    )

    result = await agent.evolve(gmgn_summary, trades_summary, state_summary)
    adjustments: list[str] = []

    if result.get("insights"):
        state["ai_insight"] = result["insights"]
        trade_journal.update_learning(
            rules=result.get("next_criteria"),
            avoid=result.get("avoid"),
            insight=result["insights"],
        )

    if result.get("next_criteria"):
        state["next_criteria"] = result["next_criteria"][:8]
        adjustments.append(f"AI开单条件 +{len(result['next_criteria'])}条")
    if result.get("avoid"):
        state["avoid_patterns"] = result["avoid"][:6]

    for adj in result.get("adjustments") or []:
        key = adj.get("strategy", "")
        delta = safe_float(adj.get("weight_delta"))
        st = state["strategies"].get(key)
        if st and abs(delta) >= 0.05:
            old_w = safe_float(st.get("weight"), 1.0)
            st["weight"] = round(_clamp(old_w + delta, MIN_WEIGHT, MAX_WEIGHT), 3)
            if st["weight"] > DISABLE_WEIGHT_BELOW:
                st["enabled"] = True
            adjustments.append(f"AI {key} {old_w:.1f}→{st['weight']:.1f}")

    for key in result.get("disable") or []:
        st = state["strategies"].get(str(key))
        if st and st.get("enabled", True):
            st["enabled"] = False
            adjustments.append(f"AI {key} 禁用")

    return adjustments


async def evolve_cycle() -> dict[str, Any]:
    intel = await gmgn_intel.get_intel()
    boosted = apply_gmgn_boost(intel) if intel.get("available") else []

    state = _load()
    adjustments = _rule_adjustments(state, trade_journal.strategy_stats())

    if trade_journal.get_recent_closed(1) or intel.get("available"):
        adjustments.extend(await _ai_evolve(state, intel))

    state["last_upgrade"] = time.time()
    _save(state)

    pending = (state.get("next_criteria") or [])[:3]
    pending += [f"避免:{a}" for a in (state.get("avoid_patterns") or [])[:2]]

    return {
        "intel": intel,
        "boosted": boosted,
        "adjustments": adjustments,
        "ai_insight": state.get("ai_insight", ""),
        "period_stats": trade_journal.period_stats(12),
        "pending": pending,
    }


def effective_buy_threshold(agent_strategy: str, base: int) -> int:
    return _effective_buy_threshold(_load(), agent_strategy, base)


def _effective_buy_threshold(state: dict, agent_strategy: str, base: int) -> int:
    """策略权重 1.0 为基准；权重越高，买入门槛越低。"""
    s = state.get("strategies", {}).get(agent_strategy, {})
    if not s.get("enabled", True):
        return 999
    w = _clamp(safe_float(s.get("weight"), 1.0), MIN_WEIGHT, MAX_WEIGHT)
    boost = _clamp(safe_float(s.get("gmgn_boost"), 0.0), 0.0, 1.0)
    return max(50, min(999, int(round(base - (w - 1.0) * 10 - boost * 5))))


def get_strategy_runtime() -> dict[str, dict]:
    state = _load()
    runtime: dict[str, dict] = {}
    agent_by_strategy = {a.strategy: a for a in ARENA_AGENTS}
    for key, st in state.get("strategies", {}).items():
        agent = agent_by_strategy.get(key)
        base = agent.buy_threshold if agent else 70
        runtime[key] = {
            "weight": safe_float(st.get("weight"), 1.0),
            "enabled": bool(st.get("enabled", True)),
            "wins": int(safe_float(st.get("wins"), 0)),
            "losses": int(safe_float(st.get("losses"), 0)),
            "gmgn_boost": safe_float(st.get("gmgn_boost"), 0),
            "base_threshold": base,
            "effective_buy_threshold": _effective_buy_threshold(state, key, base),
        }
    return runtime


def validate_runtime_rules() -> dict[str, Any]:
    state = _load()
    expected = set(DEFAULT_STRATEGIES)
    strategies = state.get("strategies", {})
    errors: list[str] = []

    missing = sorted(expected - set(strategies))
    legacy = sorted(set(strategies) - expected)
    if missing:
        errors.append("missing strategies: " + ", ".join(missing))
    if legacy:
        errors.append("unexpected strategies: " + ", ".join(legacy))
    if state.get("version") != CURRENT_VERSION:
        errors.append(f"state version {state.get('version')} != {CURRENT_VERSION}")

    thresholds: dict[str, int] = {}
    for agent in ARENA_AGENTS:
        st = strategies.get(agent.strategy, {})
        weight = safe_float(st.get("weight"), 1.0)
        if not (MIN_WEIGHT <= weight <= MAX_WEIGHT):
            errors.append(f"{agent.strategy} weight out of range: {weight}")
        threshold = _effective_buy_threshold(state, agent.strategy, agent.buy_threshold)
        thresholds[agent.strategy] = threshold
        if st.get("enabled", True) and not (50 <= threshold <= 120):
            errors.append(f"{agent.strategy} threshold invalid: {threshold}")

    return {
        "ok": not errors,
        "errors": errors,
        "version": state.get("version"),
        "strategies": sorted(strategies),
        "enabled": sorted(k for k, v in strategies.items() if v.get("enabled", True)),
        "thresholds": thresholds,
        "next_criteria": state.get("next_criteria", []),
        "avoid_patterns": state.get("avoid_patterns", []),
    }
