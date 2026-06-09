"""代币分析：DexScreener + GoPlus 安全检查"""

import asyncio
from typing import Any

from config.settings import BSC_CHAIN_ID, DEXSCREENER_CHAIN
from utils.format import fmt_money, fmt_pct, risk_emoji, safe_float
from utils.http import fetch_json

DEX_URL = "https://api.dexscreener.com/latest/dex/search"
GOPLUS_URL = f"https://api.gopluslabs.io/api/v1/token_security/{BSC_CHAIN_ID}"


def _assess_risk(security: dict) -> tuple[str, list[str]]:
    flags: list[str] = []
    level = "low"

    if security.get("is_honeypot") == "1":
        return "critical", ["⛔ 蜜罐代币，无法卖出"]

    buy_tax = safe_float(security.get("buy_tax"))
    sell_tax = safe_float(security.get("sell_tax"))
    if buy_tax > 10 or sell_tax > 10:
        level = "high"
        flags.append(f"⚠️ 高税率: 买 {buy_tax:.1f}% / 卖 {sell_tax:.1f}%")
    elif buy_tax > 5 or sell_tax > 5:
        level = "medium"
        flags.append(f"⚠️ 税率偏高: 买 {buy_tax:.1f}% / 卖 {sell_tax:.1f}%")

    if security.get("is_mintable") == "1":
        if level in ("low", "medium"):
            level = "high"
        flags.append("⚠️ 可增发")

    if security.get("hidden_owner") == "1":
        level = "high" if level != "critical" else level
        flags.append("⚠️ 隐藏所有者")

    if security.get("can_take_back_ownership") == "1":
        level = "medium" if level == "low" else level
        flags.append("⚠️ 可收回所有权")

    if security.get("is_open_source") == "0":
        level = "medium" if level == "low" else level
        flags.append("⚠️ 未开源")

    if security.get("is_proxy") == "1":
        flags.append("ℹ️ 代理合约")

    holder_count = security.get("holder_count", "N/A")
    flags.append(f"👥 持有者: {holder_count}")

    if not flags:
        flags.append("✅ 未发现明显风险")

    return level, flags


async def fetch_dex_data(address: str) -> dict[str, Any] | None:
    data = await fetch_json(DEX_URL, params={"q": address})
    pairs = data.get("pairs") or []
    bsc_pairs = [p for p in pairs if p.get("chainId") == DEXSCREENER_CHAIN]
    if not bsc_pairs:
        bsc_pairs = pairs
    if not bsc_pairs:
        return None
    return max(bsc_pairs, key=lambda p: safe_float((p.get("liquidity") or {}).get("usd")))


async def fetch_security(address: str) -> dict[str, Any]:
    data = await fetch_json(GOPLUS_URL, params={"contract_addresses": address.lower()})
    if data.get("code") != 1:
        return {}
    result = data.get("result") or {}
    return result.get(address.lower()) or result.get(address) or next(iter(result.values()), {})


async def analyze_token(address: str) -> dict[str, Any]:
    address = address.strip()
    dex_result, sec_result = await asyncio.gather(
        fetch_dex_data(address),
        fetch_security(address),
        return_exceptions=True,
    )
    dex = dex_result if isinstance(dex_result, dict) else None
    security = sec_result if isinstance(sec_result, dict) else {}
    risk_level, risk_flags = _assess_risk(security) if security else ("unknown", ["⚪ 无安全数据"])

    if dex:
        base = dex.get("baseToken") or {}
        quote = dex.get("quoteToken") or {}
        liq = dex.get("liquidity") or {}
        changes = dex.get("priceChange") or {}
        symbol = base.get("symbol", "???")
        name = base.get("name", "Unknown")
        price = safe_float(dex.get("priceUsd"))
        volume = safe_float(dex.get("volume", {}).get("h24") if isinstance(dex.get("volume"), dict) else dex.get("volume"))
        liquidity = safe_float(liq.get("usd"))
        mcap = safe_float(dex.get("marketCap") or dex.get("fdv"))
        change_24h = safe_float(changes.get("h24"))
        dex_name = dex.get("dexId", "unknown")
        pair_addr = dex.get("pairAddress", "")
    else:
        symbol = security.get("token_symbol", "???")
        name = security.get("token_name", "Unknown")
        price = volume = liquidity = mcap = change_24h = 0
        dex_name = pair_addr = ""
        base = {"address": address, "symbol": symbol, "name": name}
        quote = {}

    return {
        "address": address,
        "symbol": symbol,
        "name": name,
        "price": price,
        "price_fmt": fmt_money(price),
        "volume": volume,
        "volume_fmt": fmt_money(volume),
        "liquidity": liquidity,
        "liquidity_fmt": fmt_money(liquidity),
        "mcap": mcap,
        "mcap_fmt": fmt_money(mcap),
        "change_24h": change_24h,
        "change_fmt": fmt_pct(change_24h),
        "dex": dex_name,
        "pair_address": pair_addr,
        "base_token": base,
        "quote_token": quote,
        "risk_level": risk_level,
        "risk_emoji": risk_emoji(risk_level),
        "risk_flags": risk_flags,
        "security": security,
        "has_dex_data": dex is not None,
    }


def format_token_card(data: dict[str, Any]) -> str:
    lines = [
        f"🔍 {data['symbol']} ({data['name']})",
        f"💰 价格: {data['price_fmt']}",
        f"📊 24h: {data['change_fmt']}",
        f"📈 成交量: {data['volume_fmt']}",
        f"💧 流动性: {data['liquidity_fmt']}",
        f"🏦 市值: {data['mcap_fmt']}",
        f"{data['risk_emoji']} 风险: {data['risk_level'].upper()}",
    ]
    for flag in data["risk_flags"]:
        lines.append(flag)
    lines.append(f"📍 `{data['address']}`")
    if data.get("dex"):
        lines.append(f"🔗 DEX: {data['dex']}")
    return "\n".join(lines)
