#!/usr/bin/env python3
"""Offline self-check for strategy rules and scoring flow."""

import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from config.strategy import ARENA_AGENTS
from plugins.ai_trader import _clamp_ai_amount
from plugins import memecoin_score
from plugins.strategy_evolver import (
    CURRENT_VERSION,
    DEFAULT_STRATEGIES,
    _normalize_state,
    get_strategy_runtime,
    validate_runtime_rules,
)


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def check_legacy_migration() -> None:
    legacy = {
        "version": 2,
        "strategies": {
            "momentum_rider": {"weight": 0.75, "enabled": True, "wins": 2, "losses": 1, "gmgn_boost": 1.0},
            "sniper_entry": {"weight": 0.85, "enabled": True, "wins": 1, "losses": 0, "gmgn_boost": 0.4},
            "holder_watch": {"weight": 0.9, "enabled": True, "wins": 0, "losses": 2, "gmgn_boost": 0.2},
        },
        "next_criteria": ["legacy criterion"],
        "avoid_patterns": ["legacy avoid"],
    }
    normalized = _normalize_state(legacy)
    keys = set(normalized["strategies"])
    _assert(normalized["version"] == CURRENT_VERSION, "migration did not bump state version")
    _assert(keys == set(DEFAULT_STRATEGIES), "migration did not produce the current strategy set")
    _assert("momentum_rider" not in keys and "sniper_entry" not in keys, "legacy strategy keys leaked")
    _assert(normalized["strategies"]["momentum_chase"]["weight"] == 0.75, "momentum alias not migrated")
    _assert(normalized["strategies"]["early_entry"]["weight"] == 0.85, "sniper alias not migrated")
    _assert(normalized["next_criteria"] == ["legacy criterion"], "criteria were not preserved")


def check_runtime_rules() -> dict:
    validation = validate_runtime_rules()
    _assert(validation["ok"], "; ".join(validation["errors"]))

    runtime = get_strategy_runtime()
    for agent in ARENA_AGENTS:
        profile = runtime.get(agent.strategy)
        _assert(profile is not None, f"missing runtime profile for {agent.strategy}")
        if profile["enabled"]:
            _assert(50 <= profile["effective_buy_threshold"] <= 120, f"bad threshold for {agent.strategy}")
        else:
            _assert(profile["effective_buy_threshold"] == 999, f"disabled threshold not locked for {agent.strategy}")
    return validation


def check_scoring_flow() -> dict:
    pool = {
        "pool_id": "fourmeme_0x1111111111111111111111111111111111111111",
        "address": "0x1111111111111111111111111111111111111111",
        "symbol": "RULE",
        "stage": "new_creation",
        "created_timestamp": time.time() - 60,
        "volume_1h": 120000,
        "volume": 120000,
        "liquidity": 50000,
        "usd_market_cap": 20000,
        "smart_degen_count": 2,
        "rug_ratio": 0.0,
    }
    result = memecoin_score.score_pool(pool)
    _assert(result["strategy"], "score_pool did not return selected strategy")
    _assert(len(result["agent_scores"]) == len(ARENA_AGENTS), "agent score count mismatch")
    for score in result["agent_scores"]:
        _assert("buy_threshold" in score, "score missing effective threshold")
        _assert("strategy_weight" in score, "score missing runtime weight")
        _assert("strategy_enabled" in score, "score missing runtime enabled flag")
    _assert(result["top_agent"]["strategy"] == result["strategy"] or result.get("best_signal"), "strategy selection broke")
    return {
        "symbol": pool["symbol"],
        "score": result["score"],
        "signal": result["signal"],
        "strategy": result["strategy"],
        "top_threshold": result["top_agent"]["buy_threshold"],
    }


def check_ai_sizing_clamp() -> dict:
    sizing = {
        "suggested_bnb": 0.002,
        "hard_cap_bnb": 0.002,
        "min_bnb": 0.001,
    }
    capped = _clamp_ai_amount({"amount_bnb": 0.05}, sizing)
    fallback = _clamp_ai_amount({}, sizing)
    blocked = _clamp_ai_amount({"amount_bnb": 0.05}, {**sizing, "suggested_bnb": 0.0, "hard_cap_bnb": 0.0})
    _assert(capped == 0.002, "AI amount was not capped by balance hard limit")
    _assert(fallback == 0.002, "AI amount did not fall back to suggested balance sizing")
    _assert(blocked == 0.0, "AI amount should be blocked when balance is insufficient")
    return {"capped": capped, "fallback": fallback, "blocked": blocked}


def main() -> None:
    check_legacy_migration()
    validation = check_runtime_rules()
    scoring = check_scoring_flow()
    ai_sizing = check_ai_sizing_clamp()
    print(json.dumps({
        "ok": True,
        "version": validation["version"],
        "strategy_count": len(validation["strategies"]),
        "enabled_count": len(validation["enabled"]),
        "scoring": scoring,
        "ai_sizing": ai_sizing,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
