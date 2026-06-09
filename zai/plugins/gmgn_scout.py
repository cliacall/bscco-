"""GMGN 自主探机会 — trending / 已射出 / 聪明钱 / 信号（skill 同款）"""

import logging
import time
from typing import Any

from config.strategy import FOURMEME_PLATFORMS, STRONG_BUY_SCORE
from plugins import gmgn_intel, memecoin_score, trade_journal
from utils.format import safe_float

logger = logging.getLogger(__name__)

_SCOUT_CACHE: dict[str, Any] = {"ts": 0, "items": []}
_SCOUT_TTL = 45

SIGNAL_TYPE_LABEL = {
    6: "价格拉升",
    7: "ATH",
    12: "聪明钱买入",
    13: "平台喊单",
    11: "CTO",
}

SOURCE_SCORE = {
    "trending": 8.0,
    "graduated": 7.5,
    "smartmoney": 9.0,
    "kol": 7.0,
    "signal": 8.5,
    "trenches": 6.5,
}


def _is_fourmeme_item(item: dict) -> bool:
    lp = (item.get("launchpad_platform") or item.get("launchpad") or "").lower()
    return any(p in lp for p in FOURMEME_PLATFORMS) or "four" in lp


def _parse_trending(data: Any) -> list[dict]:
    if not data:
        return []
    root = data.get("data") if isinstance(data, dict) else data
    rank = root.get("rank") if isinstance(root, dict) else None
    if not isinstance(rank, list):
        rank = data if isinstance(data, list) else []
    out = []
    for item in rank:
        if not isinstance(item, dict):
            continue
        addr = (item.get("address") or "").lower()
        if not addr.startswith("0x"):
            continue
        if not _is_fourmeme_item(item):
            continue
        sm = int(item.get("smart_degen_count") or 0)
        vol = safe_float(item.get("volume") or item.get("volume_1h") or item.get("volume_24h"))
        score = SOURCE_SCORE["trending"] + min(3, sm * 0.5) + min(2, vol / 50000)
        out.append({
            "symbol": item.get("symbol", "?"),
            "address": addr,
            "source": "trending",
            "stage": "graduated" if item.get("is_out_market") or item.get("exchange") else "hot",
            "reason": f"热门 {item.get('interval', '1h')} vol={vol:.0f} SM={sm}",
            "score": round(score, 1),
            "smart_degen_count": sm,
            "volume": vol,
            "raw": item,
        })
    return out


def _parse_signals(data: Any) -> list[dict]:
    if not data:
        return []
    items = data if isinstance(data, list) else (data.get("data") or data.get("list") or [])
    if isinstance(items, dict):
        items = items.get("list") or []
    out = []
    for item in items:
        if not isinstance(item, dict):
            continue
        addr = (item.get("token_address") or item.get("address") or "").lower()
        if not addr.startswith("0x"):
            continue
        cur = item.get("cur_data") or {}
        snap = item.get("data") or {}
        merged = {**snap, **cur}
        if not _is_fourmeme_item(merged) and not _is_fourmeme_item(item):
            continue
        st = int(item.get("signal_type") or 0)
        label = SIGNAL_TYPE_LABEL.get(st, f"信号{st}")
        sm = int(merged.get("smart_degen_count") or 0)
        score = SOURCE_SCORE["signal"] + min(2, sm * 0.3)
        out.append({
            "symbol": merged.get("symbol") or item.get("symbol", "?"),
            "address": addr,
            "source": "signal",
            "stage": "signal",
            "reason": f"GMGN {label} MC=${safe_float(item.get('trigger_mc')):.0f}",
            "score": round(score, 1),
            "signal_type": st,
            "smart_degen_count": sm,
            "raw": item,
        })
    return out


def _parse_intel_buys(intel: dict) -> list[dict]:
    out = []
    clusters = intel.get("clusters") or {}
    strength_bonus = {"strong": 3, "medium": 2, "weak": 1}
    for addr, c in clusters.items():
        if not addr.startswith("0x"):
            continue
        boost = strength_bonus.get(c.get("strength", "weak"), 1)
        out.append({
            "symbol": c.get("symbol", "?"),
            "address": addr.lower(),
            "source": "smartmoney",
            "stage": "smartmoney",
            "reason": f"聪明钱集群 {c.get('wallet_count')}钱包 ${c.get('total_usd', 0):.0f}",
            "score": round(SOURCE_SCORE["smartmoney"] + boost, 1),
            "wallet_count": c.get("wallet_count", 0),
            "raw": c,
        })
    for t in intel.get("buys", [])[:15]:
        addr = (t.get("address") or "").lower()
        if not addr.startswith("0x"):
            continue
        if any(o["address"] == addr for o in out):
            continue
        out.append({
            "symbol": t.get("symbol", "?"),
            "address": addr,
            "source": "smartmoney",
            "stage": "smartmoney",
            "reason": f"聪明钱买入 ${t.get('amount_usd', 0):.0f}",
            "score": SOURCE_SCORE["smartmoney"],
            "raw": t,
        })
    return out


def _parse_pools(pools: list[dict], source: str) -> list[dict]:
    out = []
    for p in pools:
        addr = (p.get("address") or p.get("token_address") or "").lower()
        if not addr.startswith("0x"):
            continue
        stage = p.get("stage", "new_creation")
        base = SOURCE_SCORE.get(source, 6)
        sc = safe_float(p.get("score")) / 10 if p.get("score") else base
        out.append({
            "symbol": p.get("symbol", "?"),
            "address": addr,
            "source": source,
            "stage": stage,
            "reason": f"{source} {stage} 分{p.get('score', '?')}",
            "score": round(max(sc, base), 1),
            "pool_score": p.get("score"),
            "smart_degen_count": p.get("smart_degen_count", 0),
            "raw": p,
        })
    return out


async def _verify_and_register(opp: dict) -> bool:
    from plugins.fourmeme_scan import ensure_fourmeme, register_verified_pool, get_pool_by_address

    addr = opp["address"]
    raw = opp.get("raw") or {}
    if _is_fourmeme_item(raw) or opp.get("source") in ("trenches", "graduated", "trending"):
        pool = get_pool_by_address(addr)
        if not pool and isinstance(raw, dict) and raw.get("address"):
            register_verified_pool({
                "pool_id": f"fourmeme_{addr}",
                "address": addr,
                "token_address": addr,
                "symbol": opp.get("symbol", "?"),
                "name": raw.get("name", opp.get("symbol", "?")),
                "launchpad": raw.get("launchpad_platform", "fourmeme"),
                "stage": opp.get("stage", "new_creation"),
                "progress": safe_float(raw.get("progress")),
                "smart_degen_count": int(opp.get("smart_degen_count") or raw.get("smart_degen_count") or 0),
                "discovered_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
                "verified_via": f"gmgn_scout_{opp.get('source')}",
            })
        return True
    ok, _ = await ensure_fourmeme(addr)
    return ok


async def scout_opportunities(
    *,
    pools_live: list[dict] | None = None,
    force: bool = False,
) -> list[dict[str, Any]]:
    """自主扫描 GMGN 机会 → 信号队列 + 注册池子"""
    now = time.time()
    if not force and _SCOUT_CACHE["items"] and now - _SCOUT_CACHE["ts"] < _SCOUT_TTL:
        return _SCOUT_CACHE["items"]

    if not gmgn_intel.cli_ready():
        logger.debug("gmgn-cli 未配置，跳过自主探机会")
        return _SCOUT_CACHE.get("items", [])

    opps: list[dict] = []
    seen: set[str] = set()

    def merge(batch: list[dict]) -> None:
        for o in batch:
            a = o.get("address", "")
            if not a or a in seen:
                continue
            seen.add(a)
            opps.append(o)

    trending_1h = await gmgn_intel._run_gmgn(
        "market", "trending", "--chain", "bsc", "--interval", "1h",
        "--platform", "fourmeme", "--platform", "fourmeme_agent",
        "--platform", "bn_fourmeme", "--platform", "four_xmode_agent",
        "--order-by", "volume", "--limit", "30",
    )
    merge(_parse_trending(trending_1h))

    signals = await gmgn_intel._run_gmgn(
        "market", "signal", "--chain", "bsc",
        "--groups", '[{"signal_type":[12,13]},{"signal_type":[6,7],"mc_min":20000}]',
    )
    merge(_parse_signals(signals))

    intel = await gmgn_intel.get_intel()
    merge(_parse_intel_buys(intel))

    kol = await gmgn_intel._run_gmgn("track", "kol", "--chain", "bsc", "--limit", "30")
    if isinstance(kol, list):
        buys, _ = gmgn_intel._normalize_list(kol)
        for t in buys[:10]:
            addr = t.get("address", "")
            if addr and addr not in seen:
                seen.add(addr)
                opps.append({
                    "symbol": t.get("symbol", "?"),
                    "address": addr,
                    "source": "kol",
                    "stage": "kol",
                    "reason": f"KOL买入 ${t.get('amount_usd', 0):.0f}",
                    "score": SOURCE_SCORE["kol"],
                    "raw": t,
                })

    if pools_live:
        graduated = [p for p in pools_live if p.get("stage") == "graduated"]
        new_c = [p for p in pools_live if p.get("stage") == "new_creation"]
        near = [p for p in pools_live if p.get("stage") == "near_completion"]
        merge(_parse_pools(graduated[:20], "graduated"))
        merge(_parse_pools(new_c[:15], "trenches"))
        merge(_parse_pools(near[:10], "trenches"))

    verified: list[dict] = []
    for opp in opps:
        try:
            if await _verify_and_register(opp):
                opp["verified_fourmeme"] = True
                trade_journal.add_signal(
                    opp["symbol"], opp["address"],
                    score=opp.get("score", 6),
                    strategy=f"gmgn_{opp.get('source', 'scout')}",
                    source=opp.get("source", ""),
                    stage=opp.get("stage", ""),
                    reason=opp.get("reason", ""),
                )
                verified.append(opp)
        except Exception as e:
            logger.debug("scout verify %s: %s", opp.get("address", "")[:10], e)

    verified.sort(key=lambda x: x.get("score", 0), reverse=True)
    _SCOUT_CACHE["items"] = verified
    _SCOUT_CACHE["ts"] = now
    if verified:
        logger.info("🔭 GMGN 自主机会 %d (热门/已射出/聪明钱/信号)", len(verified))
    return verified


def get_opportunities() -> list[dict]:
    return list(_SCOUT_CACHE.get("items", []))


def opportunities_to_pools() -> list[dict]:
    """供候选池合并"""
    pools = []
    for o in get_opportunities():
        pools.append({
            "pool_id": f"scout_{o['address']}",
            "address": o["address"],
            "token_address": o["address"],
            "symbol": o.get("symbol", "?"),
            "stage": o.get("stage", "hot"),
            "score": safe_float(o.get("pool_score")) or min(100, o.get("score", 6) * 10),
            "signal": "WATCH",
            "displayable": True,
            "strategy": f"gmgn_{o.get('source')}",
            "source": o.get("source"),
            "scout_reason": o.get("reason"),
            "smart_degen_count": o.get("smart_degen_count", 0),
        })
    return pools
