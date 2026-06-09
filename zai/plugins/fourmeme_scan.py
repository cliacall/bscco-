"""four.meme 扫描 — GMGN trenches 新币/即将毕业/已射出"""

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from config.settings import get_settings
from config.strategy import FOURMEME_PLATFORMS, STRONG_BUY_SCORE
from plugins import gmgn_intel, memecoin_score
from utils.format import fmt_money, safe_float

logger = logging.getLogger(__name__)

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
SEEN_FILE = os.path.join(DATA_DIR, "seen_fourmeme.json")
HISTORY_FILE = os.path.join(DATA_DIR, "fourmeme_history.json")

_recent: list[dict[str, Any]] = []
_candidates: list[dict[str, Any]] = []


def _ensure() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)


def _load_seen() -> set[str]:
    _ensure()
    if not os.path.exists(SEEN_FILE):
        return set()
    try:
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    except (json.JSONDecodeError, OSError):
        return set()


def _save_seen(seen: set[str]) -> None:
    _ensure()
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(seen), f, indent=2)


def _load_history() -> list[dict]:
    _ensure()
    if not os.path.exists(HISTORY_FILE):
        return []
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


def _save_history(history: list[dict]) -> None:
    _ensure()
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history[-200:], f, indent=2, ensure_ascii=False)


def _is_fourmeme(item: dict) -> bool:
    lp = (item.get("launchpad_platform") or item.get("launchpad") or "").lower()
    return any(p in lp for p in FOURMEME_PLATFORMS) or "four" in lp


def _parse_trench_item(item: dict, stage: str) -> dict | None:
    addr = (item.get("address") or "").lower()
    if not addr.startswith("0x"):
        return None
    if not _is_fourmeme(item):
        return None
    sym = item.get("symbol", "?")
    return {
        "pool_id": f"fourmeme_{addr}",
        "address": addr,
        "token_address": addr,
        "symbol": sym,
        "name": item.get("name", sym),
        "launchpad": item.get("launchpad_platform", "fourmeme"),
        "stage": stage,
        "progress": safe_float(item.get("progress")),
        "smart_degen_count": int(item.get("smart_degen_count") or 0),
        "holder_count": int(item.get("holder_count") or 0),
        "rug_ratio": safe_float(item.get("rug_ratio")),
        "volume_1h": safe_float(item.get("volume_1h")),
        "volume_24h": safe_float(item.get("volume_24h")),
        "liquidity": safe_float(item.get("liquidity") or item.get("usd_market_cap")),
        "volume": safe_float(item.get("volume_1h") or item.get("volume_24h")),
        "volume_fmt": fmt_money(safe_float(item.get("volume_1h"))),
        "liquidity_fmt": fmt_money(safe_float(item.get("liquidity"))),
        "created_timestamp": safe_float(item.get("created_timestamp") or item.get("open_timestamp")),
        "discovered_at": datetime.now(timezone.utc).isoformat(),
    }


def _trenches_chunk(data: dict | None, response_key: str) -> list:
    if not isinstance(data, dict):
        return []
    root = data.get("data") if isinstance(data.get("data"), dict) else data
    if not isinstance(root, dict):
        return []
    chunk = root.get(response_key)
    return chunk if isinstance(chunk, list) else []


async def _fetch_trenches_stage(
    cli_type: str,
    response_key: str,
    stage_name: str,
    platforms: tuple[str, ...],
    *,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """GMGN skill: gmgn-cli market trenches --chain bsc --type <stage> --launchpad-platform …"""
    args = [
        "market", "trenches", "--chain", "bsc",
        "--type", cli_type,
        "--sort-by", "created_timestamp",
        "--direction", "desc",
        "--limit", str(limit),
        "--max-rug-ratio", "0.35",
    ]
    for p in platforms:
        args.extend(["--launchpad-platform", p])

    data = await gmgn_intel._run_gmgn(*args)
    pools: list[dict] = []
    for item in _trenches_chunk(data, response_key):
        parsed = _parse_trench_item(item, stage_name)
        if parsed:
            pools.append(parsed)
    return pools


async def fetch_fourmeme_primary() -> list[dict[str, Any]]:
    """four.meme platform API primary; GMGN trenches supplement."""
    from plugins import fourmeme_api

    platform_task = fourmeme_api.fetch_platform_pools()
    gmgn_task = _fetch_gmgn_trenches() if gmgn_intel.cli_ready() else asyncio.sleep(0, result=[])
    platform_items, gmgn_items = await asyncio.gather(platform_task, gmgn_task)

    merged: dict[str, dict] = {}
    for pool in platform_items + (gmgn_items or []):
        addr = pool.get("address", "").lower()
        if addr:
            merged[addr] = {**merged.get(addr, {}), **pool}

    if not merged:
        logger.warning("four.meme API + GMGN empty — using history cache")
        return _load_history()[:30]

    pools = list(merged.values())
    pools.sort(key=lambda x: (x.get("created_timestamp") or 0, x.get("score") or 0), reverse=True)
    n_new = sum(1 for p in pools if p.get("stage") == "new_creation")
    logger.debug("scan new_creation %d (four.meme API) | total %d", n_new, len(pools))
    return pools


async def _fetch_gmgn_trenches() -> list[dict[str, Any]]:
    new_task = _fetch_trenches_stage("new_creation", "new_creation", "new_creation", ("fourmeme",), limit=40)
    near_task = _fetch_trenches_stage("near_completion", "pump", "near_completion", FOURMEME_PLATFORMS, limit=30)
    done_task = _fetch_trenches_stage("completed", "completed", "graduated", FOURMEME_PLATFORMS, limit=30)
    new_items, near_items, done_items = await asyncio.gather(new_task, near_task, done_task)
    return new_items + near_items + done_items


async def _enrich(pool: dict) -> dict:
    try:
        sc = memecoin_score.score_pool(pool)
        pool.update({
            "score": sc["score"],
            "tier": sc["tier"],
            "tier_emoji": sc["tier_emoji"],
            "tier_label": sc["tier_label"],
            "signal": sc["signal"],
            "displayable": sc["displayable"],
            "strategy": sc["strategy"],
            "top_agent": sc.get("top_agent"),
            "best_signal": sc.get("best_signal"),
        })
    except Exception:
        pass
    return pool


def get_recent_pools() -> list[dict]:
    return list(_recent)


def get_history_pools(limit: int = 50) -> list[dict]:
    return _load_history()[:limit]


def get_candidates() -> list[dict]:
    return list(_candidates)


def get_pool_by_address(address: str) -> dict | None:
    addr = address.lower().strip()
    for p in _candidates + _recent + _load_history():
        if (p.get("address") or p.get("token_address") or "").lower() == addr:
            return p
    return None


def get_tracked_addresses() -> set[str]:
    """当前追踪的 four.meme 一级地址"""
    addrs: set[str] = set()
    for p in _candidates + _recent + _load_history():
        a = (p.get("address") or p.get("token_address") or "").lower()
        if a.startswith("0x"):
            addrs.add(a)
    return addrs


def _is_fourmeme_token_info(info: dict) -> bool:
    """GMGN token info → 是否 four.meme 发射币（含已射出/毕业）"""
    d = info.get("data") if isinstance(info.get("data"), dict) else info
    lp = (d.get("launchpad_platform") or d.get("launchpad") or "").lower()
    return _is_fourmeme({"launchpad_platform": lp})


def _pool_from_gmgn_info(info: dict, addr: str) -> dict | None:
    d = info.get("data") if isinstance(info.get("data"), dict) else info
    if not _is_fourmeme_token_info(d):
        return None
    sym = d.get("symbol", "?")
    progress = safe_float(d.get("launchpad_progress"))
    status = d.get("launchpad_status")
    if status in (2, "2") or progress >= 1.0 or d.get("is_on_curve") is False:
        stage = "graduated"
    elif progress >= 0.85:
        stage = "near_completion"
    else:
        stage = "new_creation"
    price_obj = d.get("price") or {}
    price = safe_float(price_obj.get("price") if isinstance(price_obj, dict) else price_obj)
    return {
        "pool_id": f"fourmeme_{addr}",
        "address": addr,
        "token_address": addr,
        "symbol": sym,
        "name": d.get("name", sym),
        "launchpad": d.get("launchpad_platform", "fourmeme"),
        "stage": stage,
        "progress": progress,
        "smart_degen_count": int(d.get("smart_degen_count") or 0),
        "holder_count": int(d.get("holder_count") or 0),
        "liquidity": safe_float(d.get("liquidity")),
        "volume": safe_float(d.get("volume_24h") or d.get("volume_1h")),
        "volume_fmt": fmt_money(safe_float(d.get("volume_24h"))),
        "liquidity_fmt": fmt_money(safe_float(d.get("liquidity"))),
        "discovered_at": datetime.now(timezone.utc).isoformat(),
        "price": price,
        "verified_via": "gmgn_token_info",
    }


def register_verified_pool(pool: dict) -> None:
    """手动/直查验证的 four.meme 币写入历史，供后续买入/信号使用"""
    global _recent
    addr = pool.get("address", "").lower()
    if not addr:
        return
    history = _load_history()
    history = [h for h in history if h.get("address", "").lower() != addr]
    history.insert(0, pool)
    _save_history(history[:200])
    if not any(p.get("address", "").lower() == addr for p in _recent):
        _recent = [pool] + list(_recent)
        _recent = _recent[:50]


async def lookup_fourmeme_by_dexscreener(addr: str) -> dict | None:
    """DexScreener 直查 fourmeme 内盘 — 无需 GMGN API"""
    from config.settings import DEXSCREENER_CHAIN
    from utils.http import fetch_json

    data = await fetch_json("https://api.dexscreener.com/latest/dex/search", params={"q": addr})
    pairs = [p for p in (data.get("pairs") or []) if p.get("chainId") == DEXSCREENER_CHAIN]
    if not pairs:
        return None

    fourmeme_pairs = [
        p for p in pairs
        if (p.get("dexId") or "").lower() in ("fourmeme", "four.meme")
    ]
    dex = None
    stage = "new_creation"
    if fourmeme_pairs:
        dex = max(fourmeme_pairs, key=lambda p: safe_float((p.get("volume") or {}).get("h24")))
    else:
        if gmgn_intel.cli_ready():
            raw = await gmgn_intel._run_gmgn("token", "info", "--chain", "bsc", "--address", addr)
            if raw:
                pool = _pool_from_gmgn_info(raw, addr)
                if pool:
                    register_verified_pool(pool)
                    return pool
        return None
    base = dex.get("baseToken") or {}
    sym = base.get("symbol", "?")
    price = safe_float(dex.get("priceUsd"))
    vol = safe_float((dex.get("volume") or {}).get("h24"))
    mcap = safe_float(dex.get("marketCap") or dex.get("fdv"))
    pool = {
        "pool_id": f"fourmeme_{addr}",
        "address": addr,
        "token_address": addr,
        "symbol": sym,
        "name": base.get("name", sym),
        "launchpad": "fourmeme",
        "stage": stage,
        "progress": 1.0 if stage == "graduated" else 0.0,
        "smart_degen_count": 0,
        "holder_count": 0,
        "liquidity": mcap,
        "volume": vol,
        "volume_fmt": fmt_money(vol),
        "liquidity_fmt": fmt_money(mcap),
        "discovered_at": datetime.now(timezone.utc).isoformat(),
        "price": price,
        "verified_via": "dexscreener",
    }
    register_verified_pool(pool)
    return pool


async def lookup_fourmeme_token(address: str) -> dict | None:
    """直查 four.meme 内盘 — GMGN token info 或 DexScreener"""
    addr = address.strip().lower()
    cached = get_pool_by_address(addr)
    if cached:
        return cached

    if gmgn_intel.cli_ready():
        raw = await gmgn_intel._run_gmgn("token", "info", "--chain", "bsc", "--address", addr)
        if raw:
            pool = _pool_from_gmgn_info(raw, addr)
            if pool:
                register_verified_pool(pool)
                return pool

    return await lookup_fourmeme_by_dexscreener(addr)


async def _search_trenches_for_address(addr: str) -> dict | None:
    """宽搜 trenches — 兜底"""
    if not gmgn_intel.cli_ready():
        return None
    args = [
        "market", "trenches", "--chain", "bsc",
        "--type", "new_creation", "--type", "near_completion", "--type", "completed",
        "--limit", "200",
    ]
    for p in FOURMEME_PLATFORMS:
        args.extend(["--launchpad-platform", p])
    data = await gmgn_intel._run_gmgn(*args)
    if not isinstance(data, dict):
        return None
    for stage_key, stage_name in (
        ("new_creation", "new_creation"),
        ("pump", "near_completion"),
        ("near_completion", "near_completion"),
        ("completed", "graduated"),
    ):
        chunk = data.get(stage_key)
        if not isinstance(chunk, list):
            inner = (data.get("data") or {}).get(stage_key) if isinstance(data.get("data"), dict) else None
            chunk = inner if isinstance(inner, list) else []
        for item in chunk:
            if (item.get("address") or "").lower() == addr:
                parsed = _parse_trench_item(item, stage_name)
                if parsed:
                    register_verified_pool(parsed)
                    return parsed
    return None


async def ensure_fourmeme(address: str) -> tuple[bool, str]:
    """校验 CA 是否为 four.meme 发射币（含已射出）"""
    addr = address.strip().lower()
    if not addr.startswith("0x") or len(addr) != 42:
        return False, "❌ 无效 CA"

    if addr in get_tracked_addresses():
        return True, ""

    pool = await lookup_fourmeme_token(addr)
    if pool:
        return True, ""

    if gmgn_intel.cli_ready():
        pool = await _search_trenches_for_address(addr)
        if pool:
            return True, ""
        fresh = await fetch_fourmeme_primary()
        for p in fresh:
            if p.get("address", "").lower() == addr:
                register_verified_pool(p)
                return True, ""

    return False, "❌ 非 four.meme 发射币（纯 Pancake 土狗不碰）"


async def scan_fourmeme(
    on_buy_signal: Callable[[dict], Awaitable[None]] | None = None,
) -> list[dict[str, Any]]:
    global _recent, _candidates
    settings = get_settings()
    seen = _load_seen()
    history = _load_history()
    raw = await fetch_fourmeme_primary()

    new_items: list[dict] = []
    live: list[dict] = []
    for pool in raw:
        if safe_float(pool.get("volume")) < settings.volume_threshold and pool.get("stage") != "new_creation":
            continue
        pool = await _enrich(pool)
        live.append(pool)
        key = pool["pool_id"]
        if key not in seen:
            seen.add(key)
            new_items.append(pool)
            history.insert(0, pool)
            if on_buy_signal and pool.get("signal") == "BUY" and pool.get("score", 0) >= STRONG_BUY_SCORE:
                await on_buy_signal(pool)

    _save_seen(seen)
    # 每轮都从 GMGN 重拉，不只在「首次发现」时更新列表
    live.sort(
        key=lambda x: (x.get("created_timestamp") or 0, x.get("score") or 0),
        reverse=True,
    )
    _recent = live[:50]
    scout_pools: list[dict] = []
    try:
        from plugins.gmgn_scout import scout_opportunities, opportunities_to_pools
        await scout_opportunities(pools_live=live)
        scout_pools = opportunities_to_pools()
    except Exception as e:
        logger.debug("scout: %s", e)

    merged: dict[str, dict] = {}
    for p in _recent + scout_pools:
        a = (p.get("address") or "").lower()
        if a:
            merged[a] = {**merged.get(a, {}), **p}
    _candidates = sorted(
        merged.values(),
        key=lambda x: safe_float(x.get("score")),
        reverse=True,
    )[:25]
    if new_items:
        _save_history(history)
    logger.debug("four.meme 新币刷新 %d 条 | 本轮新增 %d | 候选 %d", len(_recent), len(new_items), len(_candidates))
    if new_items:
        logger.info("four.meme 新币 %d | 候选 %d", len(new_items), len(_candidates))
    return new_items
