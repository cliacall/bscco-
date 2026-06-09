"""GMGN 聪明钱 — gmgn-cli 自动拉取 + Skill 快照"""

import asyncio
import json
import logging
import os
import shutil
import time
from typing import Any

logger = logging.getLogger(__name__)

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
SNAPSHOT_FILE = os.path.join(DATA_DIR, "gmgn_snapshot.json")
STALE_HOURS = 6
CLUSTER_WINDOW_SEC = 1800

_CACHE: dict[str, Any] = {"ts": 0, "data": None}
_CACHE_TTL = 25
_LAST_PULL = 0.0


def _ensure() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)


def _resolve_gmgn_api_key() -> str:
    from config.settings import get_settings

    key = (get_settings().gmgn_api_key or os.getenv("GMGN_API_KEY", "")).strip()
    if key:
        return key
    env_file = os.path.expanduser("~/.config/gmgn/.env")
    if os.path.exists(env_file):
        try:
            with open(env_file, encoding="utf-8") as f:
                for line in f:
                    if line.startswith("GMGN_API_KEY="):
                        val = line.split("=", 1)[1].strip().strip('"').strip("'")
                        if val:
                            return val
        except OSError:
            pass
    return ""


def cli_ready() -> bool:
    return bool(shutil.which("gmgn-cli") and _resolve_gmgn_api_key())


def gmgn_subprocess_env() -> dict[str, str]:
    env = os.environ.copy()
    key = _resolve_gmgn_api_key()
    if key:
        env["GMGN_API_KEY"] = key
    return env


def _normalize_trade(item: dict) -> dict[str, Any] | None:
    if item.get("address") and item.get("symbol"):
        return {
            "symbol": item["symbol"],
            "address": item["address"].lower(),
            "amount_usd": float(item.get("amount_usd") or item.get("cost_usd") or 0),
            "side": item.get("side", "buy"),
            "maker": item.get("maker") or item.get("maker_address") or "",
            "price_usd": float(item.get("price_usd") or 0),
            "is_open_or_close": item.get("is_open_or_close", 0),
            "tags": item.get("tags") or [],
            "timestamp": item.get("timestamp") or item.get("ts"),
            "source": item.get("source", "gmgn"),
        }

    base = (item.get("base_address") or item.get("token_address") or "").lower()
    if not base.startswith("0x"):
        return None
    sym = item.get("symbol") or item.get("base_symbol") or "?"
    side = item.get("side") or item.get("event") or "buy"
    if side not in ("buy", "sell"):
        side = "sell" if "sell" in str(side).lower() else "buy"
    maker = item.get("maker") or item.get("maker_address") or ""
    if not maker and isinstance(item.get("maker_info"), dict):
        maker = item["maker_info"].get("address", "")
    amount = float(item.get("cost_usd") or item.get("amount_usd") or item.get("usd_value") or 0)
    tags = []
    if isinstance(item.get("maker_info"), dict):
        tags = item["maker_info"].get("tags") or []
    return {
        "symbol": sym,
        "address": base,
        "amount_usd": amount,
        "side": side,
        "maker": maker,
        "price_usd": float(item.get("price_usd") or item.get("price") or 0),
        "is_open_or_close": item.get("is_open_or_close", 0),
        "tags": tags,
        "timestamp": item.get("timestamp") or item.get("block_time"),
        "source": "gmgn-cli",
    }


def _normalize_list(items: list) -> tuple[list[dict], list[dict]]:
    buys, sells = [], []
    for raw in items:
        t = _normalize_trade(raw if isinstance(raw, dict) else {})
        if not t:
            continue
        (buys if t["side"] == "buy" else sells).append(t)
    return buys, sells


def cluster_signals(buys: list[dict], window_sec: int = CLUSTER_WINDOW_SEC) -> dict[str, dict]:
    now = time.time()
    by_addr: dict[str, dict] = {}

    for t in buys:
        addr = t.get("address", "")
        if not addr:
            continue
        ts = t.get("timestamp")
        if ts and isinstance(ts, (int, float)) and ts > 1e12:
            ts = ts / 1000
        if ts and now - float(ts) > window_sec:
            continue

        bucket = by_addr.setdefault(addr, {
            "symbol": t.get("symbol", "?"),
            "address": addr,
            "wallets": set(),
            "total_usd": 0.0,
            "trades": 0,
        })
        bucket["wallets"].add(t.get("maker") or f"t{bucket['trades']}")
        bucket["total_usd"] += float(t.get("amount_usd") or 0)
        bucket["trades"] += 1

    clusters: dict[str, dict] = {}
    for addr, c in by_addr.items():
        wc = len(c["wallets"])
        if wc < 1:
            continue
        if wc >= 3:
            strength = "strong"
        elif wc >= 2:
            strength = "medium"
        else:
            strength = "weak"
        clusters[addr] = {
            "symbol": c["symbol"],
            "address": addr,
            "wallet_count": wc,
            "total_usd": round(c["total_usd"], 2),
            "strength": strength,
            "trade_count": c["trades"],
        }
    return clusters


def format_for_ai(
    trades: list[dict],
    clusters: dict[str, dict] | list,
    focus_address: str | None = None,
) -> str:
    if isinstance(clusters, list):
        clusters = {c.get("address", ""): c for c in clusters if c.get("address")}
    lines = ["GMGN 聪明钱:"]
    if focus_address:
        fa = focus_address.lower()
        hit = clusters.get(fa)
        if hit:
            lines.append(
                f"目标币 {hit.get('symbol')} 集群 {hit.get('wallet_count')}钱包 "
                f"${hit.get('total_usd', 0):.0f} 强度={hit.get('strength')}"
            )
        else:
            lines.append(f"目标 {fa[:10]}… 无集群信号")
    for addr, c in list(clusters.items())[:8]:
        lines.append(
            f"- {c.get('symbol')} {c.get('wallet_count')}钱包 "
            f"${c.get('total_usd', 0):.0f} [{c.get('strength')}]"
        )
    recent = [t for t in trades if t.get("side") == "buy"][:5]
    for t in recent:
        lines.append(f"  买 {t.get('symbol')} ${t.get('amount_usd', 0):.0f}")
    return "\n".join(lines) if len(lines) > 1 else "暂无聪明钱数据"


def _load_snapshot() -> dict:
    _ensure()
    if not os.path.exists(SNAPSHOT_FILE):
        return {}
    try:
        with open(SNAPSHOT_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _save_snapshot(data: dict) -> None:
    _ensure()
    with open(SNAPSHOT_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def ingest_skill_payload(payload: dict) -> dict:
    buys_raw = payload.get("buys") or []
    sells_raw = payload.get("sells") or []
    raw_list = payload.get("items") or payload.get("list")
    if not buys_raw and not sells_raw and raw_list:
        buys_raw, sells_raw = _normalize_list(raw_list)

    buys = [_normalize_trade(t) for t in buys_raw if isinstance(t, dict)]
    sells = [_normalize_trade(t) for t in sells_raw if isinstance(t, dict)]
    buys = [t for t in buys if t]
    sells = [t for t in sells if t]
    clusters = cluster_signals(buys)

    snap = {
        "source": payload.get("source", "cursor_skill"),
        "updated_at": time.time(),
        "buys": buys,
        "sells": sells,
        "ai_summary": payload.get("ai_summary", ""),
        "cluster_count": len(clusters),
    }
    _save_snapshot(snap)
    _CACHE["ts"] = 0
    return {"buys": len(buys), "sells": len(sells), "clusters": len(clusters)}


def snapshot_status() -> dict:
    snap = _load_snapshot()
    updated = snap.get("updated_at", 0)
    age_h = (time.time() - updated) / 3600 if updated else 999
    return {
        "cli_ready": cli_ready(),
        "has_snapshot": bool(snap.get("buys") or snap.get("sells")),
        "updated_at": updated,
        "age_hours": round(age_h, 1),
        "stale": age_h > STALE_HOURS,
        "buy_count": len(snap.get("buys") or []),
        "cluster_count": snap.get("cluster_count", 0),
    }


async def _run_gmgn(*args: str) -> Any:
    if not cli_ready():
        return None
    try:
        proc = await asyncio.create_subprocess_exec(
            "gmgn-cli", *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=gmgn_subprocess_env(),
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            err = stderr.decode(errors="replace").strip()
            if err and "401" not in err:
                logger.debug("gmgn-cli %s: %s", " ".join(args[:3]), err[:120])
            return None
        text = stdout.decode(errors="replace").strip()
        if not text:
            return None
        return json.loads(text)
    except (json.JSONDecodeError, OSError, asyncio.TimeoutError) as e:
        logger.debug("gmgn-cli error: %s", e)
        return None


async def pull_smartmoney(min_interval: int = 300) -> dict | None:
    global _LAST_PULL
    now = time.time()
    if min_interval > 0 and now - _LAST_PULL < min_interval:
        return None
    if not cli_ready():
        return None

    data = await _run_gmgn("track", "smartmoney", "--chain", "bsc", "--limit", "60")
    if not data:
        return None

    items = data if isinstance(data, list) else data.get("list") or data.get("data") or []
    if not isinstance(items, list):
        items = []

    buys, sells = _normalize_list(items)
    clusters = cluster_signals(buys)
    snap = {
        "source": "gmgn-cli",
        "updated_at": time.time(),
        "buys": buys,
        "sells": sells,
        "ai_summary": "",
        "cluster_count": len(clusters),
    }
    _save_snapshot(snap)
    _CACHE["ts"] = 0
    _LAST_PULL = now
    logger.info("📡 聪明钱快照 %d买 %d集群", len(buys), len(clusters))
    return snap


async def get_intel(force: bool = False) -> dict[str, Any]:
    now = time.time()
    if not force and _CACHE["data"] and now - _CACHE["ts"] < _CACHE_TTL:
        return _CACHE["data"]

    snap = _load_snapshot()
    buys = snap.get("buys") or []
    sells = snap.get("sells") or []
    clusters = cluster_signals(buys)
    updated = snap.get("updated_at", 0)
    available = bool(buys or sells) and (time.time() - updated) < STALE_HOURS * 3600

    intel = {
        "available": available,
        "source": snap.get("source", ""),
        "updated_at": updated,
        "buys": buys,
        "sells": sells,
        "smartmoney": buys + sells,
        "clusters": clusters,
        "cluster_count": len(clusters),
        "ai_summary": snap.get("ai_summary", ""),
    }
    _CACHE["data"] = intel
    _CACHE["ts"] = now
    return intel


async def get_radar(limit: int = 20) -> dict[str, Any]:
    """聪明钱 + GMGN 自主机会雷达"""
    from plugins.fourmeme_scan import get_tracked_addresses
    from plugins.gmgn_scout import get_opportunities

    intel = await get_intel()
    fourmeme_addrs = get_tracked_addresses()
    scout_addrs = {o["address"] for o in get_opportunities()}
    buys = [t for t in intel.get("buys", []) if t.get("side") == "buy"]
    clusters = intel.get("clusters") or {}
    cluster_addrs = set(clusters.keys())

    def _is_fourmeme(addr: str) -> bool:
        a = addr.lower()
        return a in fourmeme_addrs or a in scout_addrs

    seen: set[str] = set()
    items: list[dict] = []

    for c in sorted(clusters.values(), key=lambda x: x.get("wallet_count", 0), reverse=True):
        addr = c.get("address", "")
        if not addr or addr in seen or not _is_fourmeme(addr):
            continue
        seen.add(addr)
        strength = c.get("strength", "medium")
        items.append({
            "symbol": c.get("symbol", "?"),
            "address": addr,
            "amount_usd": c.get("total_usd", 0),
            "wallet_count": c.get("wallet_count", 0),
            "strength": strength,
            "emoji": {"strong": "🔥", "medium": "⚡", "weak": "💡"}.get(strength, "📡"),
            "is_cluster": True,
            "side": "buy",
        })

    for t in buys:
        addr = t.get("address", "")
        if not addr or addr in seen or not _is_fourmeme(addr):
            continue
        seen.add(addr)
        items.append({
            "symbol": t.get("symbol", "?"),
            "address": addr,
            "amount_usd": t.get("amount_usd", 0),
            "wallet_count": 1,
            "strength": "weak",
            "emoji": "🐋",
            "is_cluster": addr in cluster_addrs,
            "side": "buy",
            "maker": (t.get("maker") or "")[:10],
        })

    for o in get_opportunities():
        addr = o.get("address", "")
        if not addr or addr in seen:
            continue
        seen.add(addr)
        src = o.get("source", "scout")
        emoji = {"trending": "🔥", "graduated": "✅", "smartmoney": "🐋", "signal": "⚡", "kol": "👑"}.get(src, "🔭")
        items.append({
            "symbol": o.get("symbol", "?"),
            "address": addr,
            "amount_usd": 0,
            "wallet_count": o.get("wallet_count", o.get("smart_degen_count", 0)),
            "strength": "medium" if o.get("score", 0) >= 8 else "weak",
            "emoji": emoji,
            "is_cluster": src == "smartmoney",
            "side": "buy",
            "source": src,
            "reason": o.get("reason", ""),
        })

    return {
        "available": intel.get("available", False) or bool(get_opportunities()),
        "updated_at": intel.get("updated_at"),
        "items": items[:limit],
        "cluster_count": len(clusters),
        "buy_count": len(buys),
    }
