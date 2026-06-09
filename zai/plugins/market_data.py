"""市场数据：CoinGecko 热榜 / 板块 / 大盘"""

from typing import Any

from utils.format import fmt_money, fmt_pct, pct_emoji, safe_float
from utils.http import fetch_json

COINGECKO_BASE = "https://api.coingecko.com/api/v3"

OVERVIEW_IDS = {
    "bitcoin": "BTC",
    "ethereum": "ETH",
    "binancecoin": "BNB",
}


async def get_overview() -> list[dict[str, Any]]:
    ids = ",".join(OVERVIEW_IDS.keys())
    data = await fetch_json(
        f"{COINGECKO_BASE}/simple/price",
        params={
            "ids": ids,
            "vs_currencies": "usd",
            "include_24hr_change": "true",
        },
    )
    result = []
    for coin_id, symbol in OVERVIEW_IDS.items():
        info = data.get(coin_id, {})
        price = safe_float(info.get("usd"))
        change = safe_float(info.get("usd_24h_change"))
        result.append({
            "id": coin_id,
            "symbol": symbol,
            "price": price,
            "price_fmt": fmt_money(price),
            "change_24h": change,
            "change_fmt": fmt_pct(change),
            "emoji": pct_emoji(change),
        })
    return result


async def get_trending() -> list[dict[str, Any]]:
    data = await fetch_json(f"{COINGECKO_BASE}/search/trending")
    coins = data.get("coins") or []
    result = []
    for i, item in enumerate(coins[:10], 1):
        coin = item.get("item") or {}
        result.append({
            "rank": i,
            "id": coin.get("id", ""),
            "symbol": (coin.get("symbol") or "").upper(),
            "name": coin.get("name", ""),
            "market_cap_rank": coin.get("market_cap_rank"),
            "thumb": coin.get("thumb", ""),
            "score": safe_float(coin.get("score")),
        })
    return result


async def get_categories() -> list[dict[str, Any]]:
    data = await fetch_json(f"{COINGECKO_BASE}/coins/categories")
    if not isinstance(data, list):
        return []
    sorted_cats = sorted(data, key=lambda c: abs(safe_float(c.get("market_cap_change_24h"))), reverse=True)
    result = []
    for cat in sorted_cats[:15]:
        change = safe_float(cat.get("market_cap_change_24h"))
        result.append({
            "id": cat.get("id", ""),
            "name": cat.get("name", ""),
            "market_cap": safe_float(cat.get("market_cap")),
            "market_cap_fmt": fmt_money(safe_float(cat.get("market_cap"))),
            "change_24h": change,
            "change_fmt": fmt_pct(change),
            "emoji": pct_emoji(change),
            "top_coins": (cat.get("top_3_coins") or [])[:3],
        })
    return result


def format_overview_text(items: list[dict[str, Any]]) -> str:
    lines = ["📊 大盘概览", ""]
    for item in items:
        lines.append(f"{item['emoji']} {item['symbol']} {item['price_fmt']} ({item['change_fmt']})")
    return "\n".join(lines)


def format_trending_text(items: list[dict[str, Any]]) -> str:
    lines = ["🔥 CoinGecko 热榜 TOP10", ""]
    for item in items:
        lines.append(f"{item['rank']}. {item['symbol']} — {item['name']}")
    return "\n".join(lines)


def format_categories_text(items: list[dict[str, Any]]) -> str:
    lines = ["📂 板块轮动 TOP15", ""]
    for item in items[:10]:
        lines.append(f"{item['emoji']} {item['name']}: {item['change_fmt']}")
    return "\n".join(lines)
