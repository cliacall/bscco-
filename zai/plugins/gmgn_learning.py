"""GMGN auto mentor learning.

Pulls BSC launchpad rankings and GMGN smart-money/KOL trades, selects wallets,
then deep-dives those wallets through the mentor-wallet trainer. This module
never submits trades.
"""

import asyncio
import json
import math
import os
import time
from typing import Any

from core.agent import TradingAgent
from plugins import gmgn_intel, mentor_wallet, trade_journal
from utils.format import safe_float

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
STATE_FILE = os.path.join(DATA_DIR, "gmgn_learning.json")
CYCLE_INTERVAL = 1800
DEFAULT_DEEP_LIMIT = 2
MAX_DEEP_LIMIT = 30
MAX_STORED_CANDIDATES = 80
MAX_STORED_LEARNED = 120
FOURMEME_PLATFORMS = (
    "fourmeme",
    "fourmeme_agent",
    "bn_fourmeme",
    "four_xmode_agent",
    "flap",
    "clanker",
    "lunafun",
)


def _ensure() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)


def _default_state() -> dict[str, Any]:
    return {
        "enabled": False,
        "running": False,
        "chain": "bsc",
        "cycle_interval": CYCLE_INTERVAL,
        "deep_limit": DEFAULT_DEEP_LIMIT,
        "last_run": 0,
        "last_message": "等待启动 GMGN 自动学习",
        "detected_total": 0,
        "learned_total": 0,
        "last_learned_count": 0,
        "requested_learn_count": DEFAULT_DEEP_LIMIT,
        "source_summary": {},
        "market_tokens": [],
        "candidates": [],
        "learned": [],
        "errors": [],
    }


def _load() -> dict[str, Any]:
    _ensure()
    if not os.path.exists(STATE_FILE):
        return _default_state()
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        data = {}
    state = _default_state()
    if isinstance(data, dict):
        state.update(data)
    return state


def _save(state: dict[str, Any]) -> None:
    _ensure()
    state["updated_at"] = time.time()
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


def _learn_limit(value: Any) -> int:
    try:
        raw = int(value or DEFAULT_DEEP_LIMIT)
    except (TypeError, ValueError):
        raw = DEFAULT_DEEP_LIMIT
    return max(1, min(MAX_DEEP_LIMIT, raw))


def _is_evm_address(value: Any) -> bool:
    text = str(value or "").strip().lower()
    if len(text) != 42 or not text.startswith("0x"):
        return False
    try:
        int(text[2:], 16)
    except ValueError:
        return False
    return True


def _learned_wallets(rows: Any) -> set[str]:
    if not isinstance(rows, list):
        return set()
    return {
        str(row.get("wallet") or "").lower()
        for row in rows
        if isinstance(row, dict) and row.get("wallet")
    }


def _dedupe_learned(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for row in rows:
        wallet = str(row.get("wallet") or "").lower()
        if not wallet or wallet in seen:
            continue
        seen.add(wallet)
        out.append(row)
    return out


def _candidate_for_wallet(state: dict[str, Any], wallet: str) -> dict[str, Any]:
    w = wallet.lower().strip()
    for cand in state.get("candidates") or []:
        if isinstance(cand, dict) and str(cand.get("wallet") or "").lower() == w:
            return cand
    return {
        "wallet": w,
        "score": 0,
        "reason": "GMGN 候选钱包直接深挖",
        "sources": ["manual_candidate"],
    }


def _learned_row_from_snapshot(cand: dict[str, Any], snap: dict[str, Any]) -> dict[str, Any]:
    analysis = snap.get("analysis") or {}
    return {
        "wallet": cand.get("wallet"),
        "ok": bool(snap.get("ok")),
        "updated_at": snap.get("updated_at") or time.time(),
        "grade": analysis.get("wallet_grade") or "unknown",
        "style": analysis.get("style") or "",
        "confidence": analysis.get("confidence") or "low",
        "guarded": bool(analysis.get("guarded")),
        "candidate_score": cand.get("score"),
        "reason": cand.get("reason", ""),
        "sources": cand.get("sources") or [cand.get("source", "")],
    }


def _items(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if not isinstance(data, dict):
        return []

    found: list[dict[str, Any]] = []
    for key in ("list", "rank", "items"):
        val = data.get(key)
        if isinstance(val, list):
            found.extend(x for x in val if isinstance(x, dict))

    for key in ("data", "result"):
        val = data.get(key)
        if isinstance(val, list):
            found.extend(x for x in val if isinstance(x, dict))
        elif isinstance(val, dict):
            for sub_key in ("rank", "list", "new_creation", "pump", "near_completion", "completed"):
                sub = val.get(sub_key)
                if isinstance(sub, list):
                    for item in sub:
                        if isinstance(item, dict):
                            item = dict(item)
                            item.setdefault("_stage", "near_completion" if sub_key == "pump" else sub_key)
                            found.append(item)

    for key in ("new_creation", "pump", "near_completion", "completed"):
        val = data.get(key)
        if isinstance(val, list):
            for item in val:
                if isinstance(item, dict):
                    item = dict(item)
                    item.setdefault("_stage", "near_completion" if key == "pump" else key)
                    found.append(item)
    return found


async def _run_gmgn_safe(*args: str, timeout: int = 24) -> Any:
    try:
        return await asyncio.wait_for(gmgn_intel._run_gmgn(*args), timeout=timeout)
    except (asyncio.TimeoutError, OSError):
        return None


def _token_address(item: dict[str, Any]) -> str:
    for key in ("address", "base_address", "token_address", "contract_address"):
        val = str(item.get(key) or "").lower().strip()
        if _is_evm_address(val):
            return val
    token = item.get("base_token") if isinstance(item.get("base_token"), dict) else {}
    for key in ("address", "base_address", "token_address"):
        val = str(token.get(key) or "").lower().strip()
        if _is_evm_address(val):
            return val
    return ""


def _wallet_address(item: dict[str, Any]) -> str:
    for key in ("maker", "maker_address", "wallet", "address"):
        val = str(item.get(key) or "").lower().strip()
        if _is_evm_address(val):
            return val
    maker_info = item.get("maker_info") if isinstance(item.get("maker_info"), dict) else {}
    val = str(maker_info.get("address") or "").lower().strip()
    return val if _is_evm_address(val) else ""


def _symbol(item: dict[str, Any]) -> str:
    token = item.get("base_token") if isinstance(item.get("base_token"), dict) else {}
    return str(item.get("symbol") or item.get("base_symbol") or token.get("symbol") or "?")[:24]


def _tags(item: dict[str, Any]) -> list[str]:
    vals: list[Any] = []
    maker_info = item.get("maker_info") if isinstance(item.get("maker_info"), dict) else {}
    for source in (item.get("tags"), maker_info.get("tags")):
        if isinstance(source, list):
            vals.extend(source)
    return sorted({str(x) for x in vals if x})


def _launchpad(item: dict[str, Any]) -> str:
    token = item.get("base_token") if isinstance(item.get("base_token"), dict) else {}
    return str(
        item.get("launchpad_platform")
        or item.get("launchpad")
        or token.get("launchpad")
        or token.get("launchpad_platform")
        or ""
    ).lower()


def _rank_score(item: dict[str, Any]) -> float:
    smart = safe_float(item.get("smart_degen_count"))
    renowned = safe_float(item.get("renowned_count"))
    hot = safe_float(item.get("hot_level"))
    vol = safe_float(
        item.get("volume_1h")
        or item.get("volume_24h")
        or item.get("volume")
        or item.get("trade_volume")
        or item.get("amount_usd")
    )
    swaps = safe_float(item.get("swaps_1h") or item.get("swaps_24h") or item.get("swaps"))
    rug = safe_float(item.get("rug_ratio"))
    score = 30 + smart * 14 + renowned * 7 + hot * 3 + min(20, math.log10(max(vol, 1)) * 4)
    score += min(12, swaps / 20)
    if _launchpad(item):
        score += 8
    if rug and rug <= 0.3:
        score += 8
    elif rug > 0.3:
        score -= 12
    return round(max(0, min(100, score)), 1)


def _extract_market_tokens(source_map: dict[str, Any]) -> list[dict[str, Any]]:
    by_addr: dict[str, dict[str, Any]] = {}
    for source, raw in source_map.items():
        for item in _items(raw):
            addr = _token_address(item)
            if not addr:
                continue
            token = by_addr.setdefault(addr, {
                "address": addr,
                "symbol": _symbol(item),
                "score": 0.0,
                "sources": [],
                "stage": item.get("_stage") or "",
                "smart_degen_count": 0,
                "renowned_count": 0,
                "launchpad": _launchpad(item),
            })
            token["score"] = max(safe_float(token.get("score")), _rank_score(item))
            token["symbol"] = token.get("symbol") or _symbol(item)
            token["stage"] = token.get("stage") or item.get("_stage") or ""
            token["smart_degen_count"] = max(safe_float(token.get("smart_degen_count")), safe_float(item.get("smart_degen_count")))
            token["renowned_count"] = max(safe_float(token.get("renowned_count")), safe_float(item.get("renowned_count")))
            token["launchpad"] = token.get("launchpad") or _launchpad(item)
            if source not in token["sources"]:
                token["sources"].append(source)
    return sorted(by_addr.values(), key=lambda x: safe_float(x.get("score")), reverse=True)[:40]


def _candidate_from_trade(
    item: dict[str, Any],
    source: str,
    market_index: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    normalized = gmgn_intel._normalize_trade(item)
    row = normalized or {}
    wallet = _wallet_address(item) or row.get("maker", "").lower().strip()
    if not _is_evm_address(wallet):
        return None
    token_addr = str(row.get("address") or _token_address(item)).lower()
    symbol = str(row.get("symbol") or _symbol(item))[:24]
    tags = sorted(set(_tags(item) + [str(x) for x in row.get("tags", []) if x]))
    side = str(row.get("side") or item.get("side") or "").lower()
    amount = safe_float(row.get("amount_usd") or item.get("amount_usd") or item.get("cost_usd"))
    is_open = item.get("is_open_or_close", row.get("is_open_or_close"))
    launchpad = _launchpad(item)
    score = 18.0
    reasons = []

    if source.startswith("smartmoney"):
        score += 34
        reasons.append("GMGN 聪明钱")
    if source.startswith("kol"):
        score += 16
        reasons.append("KOL/renowned")
    if side == "buy":
        score += 10
        reasons.append("买入动作")
    if is_open in (0, "0"):
        score += 12
        reasons.append("开仓/加仓")
    if "smart_degen" in tags:
        score += 18
    if "gmgn" in tags:
        score += 7
    if "kol" in tags or "renowned" in tags:
        score += 8
    score += min(18, amount / 300) if amount else 0
    if token_addr and token_addr in market_index:
        score += 28
        reasons.append("命中 GMGN 内盘/排行币")
    if launchpad and any(x in launchpad for x in FOURMEME_PLATFORMS):
        score += 16
        reasons.append("BSC 内盘发射币")

    token_meta = market_index.get(token_addr, {})
    if token_meta.get("smart_degen_count"):
        score += min(12, safe_float(token_meta.get("smart_degen_count")) * 4)

    return {
        "wallet": wallet,
        "score": round(max(0, min(100, score)), 1),
        "source": source,
        "tags": tags[:8],
        "trades": 1,
        "buy_usd": amount if side == "buy" else 0,
        "sell_usd": amount if side == "sell" else 0,
        "symbols": [symbol] if symbol else [],
        "tokens": [token_addr] if token_addr else [],
        "last_seen": row.get("timestamp") or item.get("timestamp") or item.get("block_time") or 0,
        "reason": " · ".join(reasons) if reasons else source,
    }


def _merge_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_wallet: dict[str, dict[str, Any]] = {}
    for cand in candidates:
        wallet = cand.get("wallet")
        if not wallet:
            continue
        current = by_wallet.setdefault(wallet, {
            "wallet": wallet,
            "score": 0.0,
            "sources": [],
            "tags": [],
            "trades": 0,
            "buy_usd": 0.0,
            "sell_usd": 0.0,
            "symbols": [],
            "tokens": [],
            "last_seen": 0,
            "reason": "",
        })
        current["score"] = max(safe_float(current.get("score")), safe_float(cand.get("score")))
        source = cand.get("source")
        if source and source not in current["sources"]:
            current["sources"].append(source)
        for key in ("tags", "symbols", "tokens"):
            for val in cand.get(key) or []:
                if val and val not in current[key]:
                    current[key].append(val)
        current["trades"] += int(cand.get("trades") or 0)
        current["buy_usd"] += safe_float(cand.get("buy_usd"))
        current["sell_usd"] += safe_float(cand.get("sell_usd"))
        current["last_seen"] = max(safe_float(current.get("last_seen")), safe_float(cand.get("last_seen")))
        if cand.get("reason") and cand["reason"] not in current["reason"]:
            current["reason"] = (current["reason"] + " · " + cand["reason"]).strip(" ·")

    rows = []
    for row in by_wallet.values():
        row["score"] = round(min(100, safe_float(row.get("score")) + min(10, row["trades"] * 1.5)), 1)
        row["buy_usd"] = round(row["buy_usd"], 2)
        row["sell_usd"] = round(row["sell_usd"], 2)
        row["tags"] = row["tags"][:8]
        row["symbols"] = row["symbols"][:8]
        row["tokens"] = row["tokens"][:8]
        rows.append(row)
    return sorted(rows, key=lambda x: (safe_float(x.get("score")), safe_float(x.get("buy_usd"))), reverse=True)


async def discover_candidates(chain: str = "bsc") -> dict[str, Any]:
    chain = chain.lower().strip() or "bsc"
    platform_args: list[str] = []
    for platform in FOURMEME_PLATFORMS:
        platform_args.extend(["--platform", platform])
    launchpad_args: list[str] = []
    for platform in FOURMEME_PLATFORMS:
        launchpad_args.extend(["--launchpad-platform", platform])

    pulls = {
        "smartmoney_buy": _run_gmgn_safe("track", "smartmoney", "--chain", chain, "--side", "buy", "--limit", "120", "--raw"),
        "kol_buy": _run_gmgn_safe("track", "kol", "--chain", chain, "--side", "buy", "--limit", "80", "--raw"),
        "trending_5m": _run_gmgn_safe(
            "market", "trending", "--chain", chain, "--interval", "5m",
            *platform_args, "--order-by", "volume", "--limit", "60", "--raw",
        ),
        "trenches_inner": _run_gmgn_safe(
            "market", "trenches", "--chain", chain, "--raw",
            "--type", "new_creation", "--type", "near_completion",
            *launchpad_args, "--filter-preset", "smart-money",
            "--sort-by", "smart_degen_count", "--limit", "80",
        ),
    }
    raw_values = await asyncio.gather(*pulls.values())
    raw = dict(zip(pulls.keys(), raw_values, strict=False))

    market_tokens = _extract_market_tokens({
        "trending_5m": raw.get("trending_5m"),
        "trenches_inner": raw.get("trenches_inner"),
    })
    market_index = {x["address"]: x for x in market_tokens}

    candidates: list[dict[str, Any]] = []
    for source in ("smartmoney_buy", "kol_buy"):
        for item in _items(raw.get(source)):
            cand = _candidate_from_trade(item, source, market_index)
            if cand:
                candidates.append(cand)

    merged = _merge_candidates(candidates)
    return {
        "market_tokens": market_tokens,
        "candidates": merged[:MAX_STORED_CANDIDATES],
        "candidate_total": len(merged),
        "market_token_total": len(market_tokens),
        "source_summary": {
            key: len(_items(value)) if value is not None else 0
            for key, value in raw.items()
        },
    }


def set_enabled(enabled: bool, deep_limit: int | None = None, cycle_interval: int | None = None) -> dict[str, Any]:
    state = _load()
    state["enabled"] = bool(enabled)
    if deep_limit is not None:
        state["deep_limit"] = _learn_limit(deep_limit)
        state["requested_learn_count"] = state["deep_limit"]
    if cycle_interval is not None:
        state["cycle_interval"] = max(300, min(7200, int(cycle_interval)))
    state["last_message"] = "GMGN 自动学习已启动" if enabled else "GMGN 自动学习已停止"
    _save(state)
    return get_state()


async def detect_only(chain: str = "bsc") -> dict[str, Any]:
    state = _load()
    if state.get("running"):
        state["last_message"] = "GMGN 自动学习正在运行中，检测稍后再试"
        return get_state()
    if not gmgn_intel.cli_ready():
        state["last_message"] = "gmgn-cli 或 GMGN_API_KEY 未配置"
        state["errors"] = [{"ts": time.time(), "message": state["last_message"]}] + (state.get("errors") or [])[:8]
        _save(state)
        return get_state()

    state.update({"running": True, "chain": chain, "last_message": "正在检测 GMGN 排行/内盘/聪明钱候选钱包..."})
    _save(state)
    try:
        discovery = await discover_candidates(chain)
        candidates = discovery.get("candidates") or []
        market_tokens = discovery.get("market_tokens") or []
        detected_total = int(discovery.get("candidate_total") or len(candidates))
        market_token_total = int(discovery.get("market_token_total") or len(market_tokens))
        state.update({
            "running": False,
            "last_message": f"检测完成：候选 {detected_total} 个，内盘/排行币 {market_token_total} 个",
            "detected_total": detected_total,
            "market_token_total": market_token_total,
            "last_detected_at": time.time(),
            "source_summary": discovery.get("source_summary") or {},
            "market_tokens": market_tokens[:20],
            "candidates": candidates[:MAX_STORED_CANDIDATES],
        })
        _save(state)
        return get_state()
    except Exception as e:  # noqa: BLE001
        state["running"] = False
        state["last_message"] = f"GMGN 检测失败：{str(e)[:160]}"
        state["errors"] = [{"ts": time.time(), "message": state["last_message"]}] + (state.get("errors") or [])[:8]
        _save(state)
        return get_state()


async def run_cycle(limit: int | None = None, chain: str = "bsc", force: bool = False) -> dict[str, Any]:
    state = _load()
    now = time.time()
    if state.get("running"):
        state["last_message"] = "GMGN 自动学习正在运行中"
        return get_state()
    if not force and not state.get("enabled"):
        state["last_message"] = "GMGN 自动学习未启动"
        _save(state)
        return get_state()
    interval = int(state.get("cycle_interval") or CYCLE_INTERVAL)
    if not force and state.get("last_run") and now - safe_float(state.get("last_run")) < interval:
        state["last_message"] = "距离下一轮学习时间还没到"
        _save(state)
        return get_state()
    if not gmgn_intel.cli_ready():
        state["last_message"] = "gmgn-cli 或 GMGN_API_KEY 未配置"
        state["errors"] = [{"ts": now, "message": state["last_message"]}] + (state.get("errors") or [])[:8]
        _save(state)
        return get_state()

    deep_limit = _learn_limit(limit or state.get("deep_limit") or DEFAULT_DEEP_LIMIT)
    state.update({
        "running": True,
        "chain": chain,
        "deep_limit": deep_limit,
        "requested_learn_count": deep_limit,
        "last_message": f"正在检测 GMGN 排行/内盘/聪明钱，计划学习 {deep_limit} 个钱包...",
    })
    _save(state)

    try:
        discovery = await discover_candidates(chain)
        candidates = discovery.get("candidates") or []
        market_tokens = discovery.get("market_tokens") or []
        detected_total = int(discovery.get("candidate_total") or len(candidates))
        market_token_total = int(discovery.get("market_token_total") or len(market_tokens))
        existing_wallets = _learned_wallets(state.get("learned"))
        learn_queue = [c for c in candidates if str(c.get("wallet") or "").lower() not in existing_wallets]
        if len(learn_queue) < deep_limit:
            extra_seen = _learned_wallets(learn_queue)
            learn_queue.extend(
                c for c in candidates
                if str(c.get("wallet") or "").lower() not in extra_seen
            )
        learned: list[dict[str, Any]] = []

        for cand in learn_queue[:deep_limit]:
            wallet = cand.get("wallet")
            if not wallet:
                continue
            try:
                snap = await asyncio.wait_for(mentor_wallet.deep_dive(wallet, chain), timeout=95)
                learned.append(_learned_row_from_snapshot(cand, snap))
            except Exception as e:  # noqa: BLE001 - surface external-data failures in state
                learned.append({
                    "wallet": wallet,
                    "ok": False,
                    "updated_at": time.time(),
                    "grade": "error",
                    "style": str(e)[:180],
                    "confidence": "low",
                    "candidate_score": cand.get("score"),
                    "reason": cand.get("reason", ""),
                    "sources": cand.get("sources") or [cand.get("source", "")],
                })
            await asyncio.sleep(0.2)

        if learned:
            rules = [
                f"GMGN 自动学习优先观察 {row['wallet'][:8]}...：{row.get('style') or row.get('reason')}"
                for row in learned
                if row.get("ok")
            ][:4]
            trade_journal.update_learning(
                rules=rules,
                avoid=["GMGN 自动学习只产规则和风险画像，不直接触发开单；自动开单仍走余额、评分、Pact 与手机确认。"],
                insight=f"已从 GMGN 排行/内盘/聪明钱检测 {detected_total} 个候选，按设置学习 {len(learned)} 个钱包。",
            )

        previous_learned = state.get("learned") if isinstance(state.get("learned"), list) else []
        merged_learned = _dedupe_learned(learned + previous_learned)
        unique_learned = _learned_wallets(merged_learned)
        state.update({
            "running": False,
            "last_run": time.time(),
            "last_message": f"完成：检测 {detected_total} 个候选，本轮批量学习 {len(learned)} 个钱包",
            "detected_total": detected_total,
            "market_token_total": market_token_total,
            "last_learned_count": len(learned),
            "learned_total": len(unique_learned),
            "source_summary": discovery.get("source_summary") or {},
            "market_tokens": market_tokens[:20],
            "candidates": candidates[:MAX_STORED_CANDIDATES],
            "learned": merged_learned[:MAX_STORED_LEARNED],
        })
        _save(state)
        return get_state()
    except Exception as e:  # noqa: BLE001 - keep dashboard usable
        state["running"] = False
        state["last_message"] = f"GMGN 自动学习失败：{str(e)[:160]}"
        state["errors"] = [{"ts": time.time(), "message": state["last_message"]}] + (state.get("errors") or [])[:8]
        _save(state)
        return get_state()


async def deep_dive_candidate(wallet: str, chain: str = "bsc") -> dict[str, Any]:
    state = _load()
    wallet = wallet.lower().strip()
    chain = chain.lower().strip() or "bsc"
    if not _is_evm_address(wallet):
        state["last_message"] = "候选钱包地址无效"
        _save(state)
        return get_state()
    if state.get("running"):
        state["last_message"] = "GMGN 自动学习正在运行中，单个深挖稍后再试"
        return get_state()

    cand = _candidate_for_wallet(state, wallet)
    state.update({
        "running": True,
        "chain": chain,
        "last_message": f"正在直接深挖 GMGN 候选钱包 {wallet[:10]}...",
    })
    _save(state)

    try:
        snap = await asyncio.wait_for(mentor_wallet.deep_dive(wallet, chain), timeout=95)
        learned_row = _learned_row_from_snapshot(cand, snap)
        previous_learned = state.get("learned") if isinstance(state.get("learned"), list) else []
        merged_learned = _dedupe_learned([learned_row] + previous_learned)
        trade_journal.update_learning(
            rules=[f"GMGN 候选钱包 {wallet[:8]}... 深挖画像：{learned_row.get('style') or learned_row.get('reason')}"],
            avoid=["候选钱包画像用于辅助交易判断，不直接触发自动开单。"],
            insight=f"已直接深挖 GMGN 候选钱包 {wallet[:8]}... 并写入本地画像记忆。",
        )
        state.update({
            "running": False,
            "last_run": time.time(),
            "last_message": f"已直接深挖候选钱包 {wallet[:10]}...，写入本地画像记忆",
            "last_learned_count": 1,
            "learned_total": len(_learned_wallets(merged_learned)),
            "learned": merged_learned[:MAX_STORED_LEARNED],
        })
        _save(state)
        return get_state()
    except Exception as e:  # noqa: BLE001
        state["running"] = False
        state["last_message"] = f"候选钱包深挖失败：{str(e)[:160]}"
        state["errors"] = [{"ts": time.time(), "message": state["last_message"]}] + (state.get("errors") or [])[:8]
        _save(state)
        return get_state()


def get_state() -> dict[str, Any]:
    state = _load()
    now = time.time()
    interval = int(state.get("cycle_interval") or CYCLE_INTERVAL)
    last_run = safe_float(state.get("last_run"))
    next_run_in = max(0, int(interval - (now - last_run))) if state.get("enabled") and last_run else 0
    agent = TradingAgent()
    return {
        **state,
        "gmgn_ready": gmgn_intel.cli_ready(),
        "deepseek_ready": agent.configured,
        "ai_provider": agent.provider,
        "next_run_in": next_run_in,
        "detected_total": int(state.get("detected_total") or len(state.get("candidates") or [])),
        "market_token_total": int(state.get("market_token_total") or len(state.get("market_tokens") or [])),
        "last_learned_count": int(state.get("last_learned_count") or 0),
        "learned_total": int(state.get("learned_total") or len({
            str(row.get("wallet") or "").lower()
            for row in (state.get("learned") or [])
            if isinstance(row, dict) and row.get("wallet")
        })),
        "requested_learn_count": int(state.get("requested_learn_count") or state.get("deep_limit") or DEFAULT_DEEP_LIMIT),
        "market_tokens": (state.get("market_tokens") or [])[:20],
        "candidates": (state.get("candidates") or [])[:MAX_STORED_CANDIDATES],
        "learned": (state.get("learned") or [])[:MAX_STORED_LEARNED],
        "errors": (state.get("errors") or [])[:8],
    }
