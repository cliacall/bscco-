"""Cobo Pact — 配对后需授权才能 contract_call"""

import logging
from typing import Any

from plugins.cobo_local import resolve_cobo
from config.settings import get_settings

logger = logging.getLogger(__name__)

PACT_NAME = "bscco-trading"
TM2 = "0x5c952063c7fc8610FFDB798152D69F0B9550762b"
PANCAKE = "0x10ED43C718714eb63d5aA57B78B54704E256024E"

TRADING_SPEC = {
    "policies": [
        {
            "name": "bscco-fourmeme-bsc",
            "type": "contract_call",
            "rules": {
                "effect": "allow",
                "when": {
                    "chain_in": ["BSC_BNB"],
                    "target_in": [
                        {"chain_id": "BSC_BNB", "contract_addr": TM2},
                        {"chain_id": "BSC_BNB", "contract_addr": PANCAKE},
                    ],
                },
                "deny_if": {
                    "amount_usd_gt": "100",
                    "usage_limits": {"rolling_24h": {"tx_count_gt": 200}},
                },
                "always_review": True,
            },
        },
        {
            "name": "bscco-token-approve",
            "type": "contract_call",
            "rules": {
                "effect": "allow",
                "when": {"chain_in": ["BSC_BNB"]},
                "deny_if": {"usage_limits": {"rolling_24h": {"tx_count_gt": 200}}},
                "always_review": True,
            },
        },
    ],
    "completion_conditions": [
        {"type": "time_elapsed", "threshold": "604800"},
        {"type": "tx_count", "threshold": "200"},
    ],
}


def _friendly_denial(err: Exception) -> str:
    try:
        from cobo_agentic_wallet.errors import PolicyDeniedError
        if isinstance(err, PolicyDeniedError):
            d = err.denial
            if d.code == "INSUFFICIENT_PERMISSION":
                return (
                    "❌ 缺少交易授权（Pact）\n"
                    "📱 请打开 Cobo App → 批准「bscco-trading」授权\n"
                    "💡 配对钱包后还需单独批准 Agent 交易权限"
                )
            return f"❌ Cobo 策略拒绝: {d.code}\n{d.suggestion or d.reason}"
    except ImportError:
        pass
    text = str(err)
    if "INSUFFICIENT_PERMISSION" in text or "can_call_contract" in text:
        return (
            "❌ 缺少交易授权（Pact）\n"
            "📱 请打开 Cobo App → 批准待审批的 Pact\n"
            "💡 配对后还需授权 Agent 才能买入/卖出"
        )
    if "401" in text:
        return "❌ Cobo API Key 无效 · 运行 caw wallet current --show-api-key"
    if "403" in text or "Forbidden" in text:
        return "❌ Cobo 拒绝 (403) · 检查 App 内 Pact 是否已批准"
    return f"❌ 交易失败: {text[:180]}"


async def get_pact_status() -> dict[str, Any]:
    """active | pending_approval | none — 每次实时拉取"""
    cobo = resolve_cobo(
        get_settings().cobo_api_key,
        get_settings().cobo_wallet_id,
        get_settings().cobo_api_url,
    )
    if not cobo.get("api_key"):
        return {"status": "no_wallet", "message": "未配置 caw 钱包"}

    try:
        from cobo_agentic_wallet.client import WalletAPIClient

        async with WalletAPIClient(base_url=cobo["api_url"], api_key=cobo["api_key"]) as client:
            for st in ("active", "pending_approval"):
                result = await client.list_pacts(status=st, wallet_id=cobo["wallet_id"])
                pacts = result if isinstance(result, list) else (result or {}).get("pacts", [])
                ours = [p for p in pacts if (p.get("name") or "") == PACT_NAME or "bscco" in (p.get("intent") or "").lower()]
                if ours:
                    p = ours[0]
                    pid = p.get("id") or p.get("pact_id")
                    has_scoped_key = False
                    if pid:
                        try:
                            detail = await client.get_pact(pid)
                            has_scoped_key = bool(isinstance(detail, dict) and detail.get("api_key"))
                        except Exception:
                            pass
                    return {
                        "status": st,
                        "pact_id": pid,
                        "name": p.get("name"),
                        "intent": p.get("intent"),
                        "scoped_key": has_scoped_key,
                    }
            return {"status": "none"}
    except Exception as e:
        logger.warning("pact status: %s", e)
        return {"status": "error", "message": str(e)[:120]}


async def ensure_trading_pact() -> dict[str, Any]:
    """确保有交易 Pact；无则自动提交，待 App 批准"""
    status = await get_pact_status()
    if status.get("status") == "active":
        cobo = resolve_cobo(
            get_settings().cobo_api_key,
            get_settings().cobo_wallet_id,
            get_settings().cobo_api_url,
        )
        try:
            from cobo_agentic_wallet.client import WalletAPIClient

            async with WalletAPIClient(base_url=cobo["api_url"], api_key=cobo["api_key"]) as client:
                detail = await client.get_pact(status["pact_id"])
                api_key = detail.get("api_key") if isinstance(detail, dict) else ""
                if not api_key:
                    return {
                        "ok": False,
                        "status": "active_no_scoped_key",
                        "pact_id": status.get("pact_id"),
                        "message": (
                            "❌ Pact 已激活，但 Cobo 没返回交易 scoped key\n"
                            "📱 请在 Cobo App 确认「bscco-trading」仍有效，必要时撤销后重新授权"
                        ),
                    }
                return {
                    "ok": True,
                    "status": "active",
                    "pact_id": status.get("pact_id"),
                    "api_key": api_key,
                }
        except Exception as e:
            logger.warning("active pact detail failed: %s", e)
            return {
                "ok": False,
                "status": "active_detail_error",
                "pact_id": status.get("pact_id"),
                "message": f"❌ Pact 已激活，但读取交易 scoped key 失败: {str(e)[:100]}",
            }

    if status.get("status") == "pending_approval":
        return {
            "ok": False,
            "status": "pending_approval",
            "pact_id": status.get("pact_id"),
            "message": (
                "⏳ 交易授权待批准\n"
                f"📋 Pact: {status.get('pact_id', '')[:8]}…\n"
                "📱 请打开 Cobo App → 批准「bscco-trading」\n"
                "✅ 批准后再点买入"
            ),
        }

    cobo = resolve_cobo(
        get_settings().cobo_api_key,
        get_settings().cobo_wallet_id,
        get_settings().cobo_api_url,
    )
    try:
        from cobo_agentic_wallet.client import WalletAPIClient

        async with WalletAPIClient(base_url=cobo["api_url"], api_key=cobo["api_key"]) as client:
            r = await client.submit_pact(
                wallet_id=cobo["wallet_id"],
                intent="bscco four.meme 交易（内盘买入/卖出 + 已射出 Pancake）",
                original_intent="bscco机器人 four.meme meme 自动/手动交易",
                spec=TRADING_SPEC,
                name=PACT_NAME,
            )
            pid = r.get("pact_id") if isinstance(r, dict) else str(r)
            logger.info("已提交交易 Pact %s — 待 App 批准", pid)
            return {
                "ok": False,
                "status": "submitted",
                "pact_id": pid,
                "message": (
                    "📤 已提交交易授权请求\n"
                    f"📋 Pact: {str(pid)[:8]}…\n"
                    "📱 请打开 Cobo App → 批准「bscco-trading」\n"
                    "✅ 批准后即可买入/卖出"
                ),
            }
    except Exception as e:
        return {"ok": False, "status": "error", "message": _friendly_denial(e)}
