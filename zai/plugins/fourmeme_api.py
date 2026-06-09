"""four.meme platform API — direct crawl for new / progress / graduated tokens.

Ref: https://four.meme/en/agentic · POST https://four.meme/meme-api/v1/public/token/ranking
"""

import logging
from datetime import datetime, timezone
from typing import Any

from utils.format import fmt_money, safe_float
from utils.http import fetch_json_post

logger = logging.getLogger(__name__)

BASE = "https://four.meme/meme-api/v1"


def _parse_item(item: dict, stage: str) -> dict | None:
    addr = (item.get("tokenAddress") or "").lower()
    if not addr.startswith("0x"):
        return None
    sym = item.get("shortName") or item.get("name") or "?"
    progress = safe_float(item.get("progress"))
    created_ms = safe_float(item.get("createDate"))
    created_ts = created_ms / 1000 if created_ms > 1e12 else created_ms
    vol = safe_float(item.get("day1Vol") or item.get("volume"))
    cap = safe_float(item.get("cap"))
    return {
        "pool_id": f"fourmeme_{addr}",
        "address": addr,
        "token_address": addr,
        "symbol": sym,
        "name": item.get("name", sym),
        "launchpad": "fourmeme",
        "stage": stage,
        "progress": progress,
        "smart_degen_count": 0,
        "holder_count": int(item.get("hold") or 0),
        "rug_ratio": 0.0,
        "volume_1h": vol,
        "volume_24h": vol,
        "liquidity": cap,
        "volume": vol,
        "volume_fmt": fmt_money(vol),
        "liquidity_fmt": fmt_money(cap),
        "created_timestamp": created_ts,
        "discovered_at": datetime.now(timezone.utc).isoformat(),
        "price": safe_float(item.get("price")),
        "tag": item.get("tag", ""),
        "source": "fourmeme_api",
        "img": item.get("img", ""),
    }


async def _ranking(rank_type: str, page_size: int = 40) -> list[dict]:
    try:
        resp = await fetch_json_post(
            f"{BASE}/public/token/ranking",
            {"type": rank_type, "pageSize": page_size, "pageIndex": 1},
            headers={"Accept": "application/json", "Content-Type": "application/json"},
        )
    except Exception as e:
        logger.warning("four.meme ranking %s failed: %s", rank_type, e)
        return []
    if not isinstance(resp, dict) or resp.get("code") != 0:
        return []
    items = resp.get("data")
    if not isinstance(items, list):
        return []
    return items


async def fetch_new_creations(limit: int = 50) -> list[dict[str, Any]]:
    """Latest tokens on four.meme bonding curve (status PUBLISH)."""
    pools: list[dict] = []
    for item in await _ranking("NEW", limit):
        p = _parse_item(item, "new_creation")
        if p and (item.get("status") == "PUBLISH" or safe_float(item.get("progress")) < 1.0):
            pools.append(p)
    pools.sort(key=lambda x: x.get("created_timestamp") or 0, reverse=True)
    logger.debug("four.meme API new_creation: %d", len(pools))
    return pools[:limit]


async def fetch_near_graduation(limit: int = 30) -> list[dict[str, Any]]:
    pools: list[dict] = []
    for item in await _ranking("PROGRESS", limit):
        p = _parse_item(item, "near_completion")
        if p and safe_float(item.get("progress")) >= 0.5:
            pools.append(p)
    return pools[:limit]


async def fetch_graduated(limit: int = 30) -> list[dict[str, Any]]:
    pools: list[dict] = []
    for item in await _ranking("DEX", limit):
        p = _parse_item(item, "graduated")
        if p:
            pools.append(p)
    return pools[:limit]


async def fetch_platform_pools() -> list[dict[str, Any]]:
    """Primary source: four.meme platform API (agentic launchpad)."""
    import asyncio

    new_t, near_t, grad_t = await asyncio.gather(
        fetch_new_creations(60),
        fetch_near_graduation(30),
        fetch_graduated(30),
    )
    merged: dict[str, dict] = {}
    for pool in new_t + near_t + grad_t:
        addr = pool.get("address", "").lower()
        if addr:
            merged[addr] = {**merged.get(addr, {}), **pool}
    return list(merged.values())
