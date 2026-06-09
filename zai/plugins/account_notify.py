"""账户开平仓通知 — 独立于 TG bot 运行状态。"""

import logging
from typing import Any

from config.settings import get_settings
from utils.format import safe_float

logger = logging.getLogger(__name__)


def short_addr(addr: str) -> str:
    if not addr:
        return ""
    return f"{addr[:8]}…{addr[-6:]}" if len(addr) > 16 else addr


async def send_notify(text: str) -> bool:
    settings = get_settings()
    if not text or not settings.notify_configured:
        return False
    try:
        import aiohttp

        url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
        payload = {
            "chat_id": settings.notify_chat_id,
            "text": text,
            "disable_web_page_preview": True,
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, timeout=8) as resp:
                if resp.status >= 400:
                    logger.warning("账户通知失败: %s %s", resp.status, await resp.text())
                    return False
                return True
    except Exception as e:
        logger.warning("账户通知失败: %s", e)
        return False


async def notify_open(symbol: str, address: str, amount_bnb: float, result: dict[str, Any]) -> None:
    await send_notify(
        "🟢 开单已提交\n"
        f"{symbol or '?'} · {safe_float(amount_bnb):.6f} BNB\n"
        f"{short_addr(address)}\n"
        f"路线: {result.get('route', '--')}\n"
        f"Tx: {short_addr(str(result.get('pact_id') or result.get('transaction_id') or ''))}\n"
        "状态: 等待/已由 Cobo App 确认"
    )


async def notify_close(address: str, sell_pct: float, result: dict[str, Any]) -> None:
    action = result.get("action")
    if action == "approve_sell":
        title = "🟡 平仓授权已提交"
        tail = "手机批准后，等链上生效再点一次清仓"
    else:
        title = "🔴 平仓已提交"
        tail = "等待/已由 Cobo App 确认"
    await send_notify(
        f"{title}\n"
        f"{safe_float(sell_pct):.1f}% · {short_addr(address)}\n"
        f"路线: {result.get('route', '--')}\n"
        f"Tx: {short_addr(str(result.get('pact_id') or result.get('transaction_id') or ''))}\n"
        f"状态: {tail}"
    )
