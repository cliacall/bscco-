"""钱包余额 — 自动读本机 caw 钱包 + BSC RPC"""

import logging
from typing import Any

from config.settings import get_settings
from plugins.cobo_local import caw_available, resolve_cobo

logger = logging.getLogger(__name__)

BNB_USD_ESTIMATE = 600.0


def _friendly_cobo_error(exc: Exception) -> str:
    msg = str(exc)
    if "401" in msg or "IDENTITY_INVALID_API_KEY" in msg or "Unauthorized" in msg:
        return "Cobo 凭证无效 · 请运行 caw wallet current --show-api-key 或检查配对"
    return "Cobo 地址获取失败"


def _get_cobo() -> dict[str, str]:
    s = get_settings()
    return resolve_cobo(s.cobo_api_key, s.cobo_wallet_id, s.cobo_api_url)


async def _resolve_address() -> tuple[str | None, str | None]:
    settings = get_settings()
    if settings.wallet_address:
        return settings.wallet_address, None

    cobo = _get_cobo()
    if cobo.get("bsc_address"):
        return cobo["bsc_address"], None

    if not cobo.get("api_key") or not cobo.get("wallet_id"):
        if caw_available():
            return None, "caw 已安装但未读到钱包 · 运行 caw wallet pair-status 确认已配对"
        return None, "运行 caw onboard + caw wallet pair 创建钱包，或填 WALLET_ADDRESS"

    try:
        from cobo_agentic_wallet.client import WalletAPIClient
        async with WalletAPIClient(
            base_url=cobo["api_url"], api_key=cobo["api_key"],
        ) as client:
            addrs = await client.list_wallet_addresses(cobo["wallet_id"])
            for a in addrs:
                if a.get("chain_type", "").upper() in ("BSC", "BSC_BNB", "EVM"):
                    return a.get("address"), None
                chains = [c.upper() for c in (a.get("compatible_chains") or [])]
                if "BSC_BNB" in chains and (a.get("address") or "").startswith("0x"):
                    return a.get("address"), None
    except Exception as e:
        logger.warning("Cobo 地址获取失败: %s", _friendly_cobo_error(e))
        return None, _friendly_cobo_error(e)
    return None, "Cobo 钱包无 BSC 地址 · 运行 caw address create --chain-id BSC_BNB"


def _fmt_address(addr: str) -> str:
    if len(addr) < 12:
        return addr
    return f"{addr[:6]}…{addr[-4:]}"


async def get_balance() -> dict[str, Any]:
    """BNB 余额 + 美元估算"""
    settings = get_settings()
    cobo = _get_cobo()
    address, resolve_err = await _resolve_address()
    if not address:
        return {
            "configured": False,
            "message": resolve_err or "未找到钱包地址",
            "bnb": 0.0,
            "usd": 0.0,
            "address": "",
            "address_fmt": "",
            "cobo_error": resolve_err or "",
            "cobo_source": cobo.get("source", "none"),
        }

    try:
        from web3 import Web3
        w3 = Web3(Web3.HTTPProvider(settings.bsc_rpc_url))
        wei = w3.eth.get_balance(Web3.to_checksum_address(address))
        bnb = wei / 10**18
        usd = bnb * BNB_USD_ESTIMATE
        return {
            "configured": True,
            "address": address,
            "address_fmt": _fmt_address(address),
            "bnb": round(bnb, 6),
            "usd": round(usd, 2),
            "bnb_fmt": f"{bnb:.6f}",
            "usd_fmt": f"${usd:.2f}",
            "source": "bsc_rpc_live",
            "cobo_source": cobo.get("source", "env"),
        }
    except Exception as e:
        logger.error("余额查询失败: %s", e)
        return {
            "configured": True,
            "address": address,
            "address_fmt": _fmt_address(address),
            "bnb": 0.0,
            "usd": 0.0,
            "bnb_fmt": "0.0000",
            "usd_fmt": "$0.00",
            "error": f"RPC 查余额失败: {e}",
            "cobo_source": cobo.get("source", "env"),
        }


def format_balance_text(bal: dict) -> str:
    if not bal.get("configured"):
        return f"💰 {bal.get('message', '未配置钱包')}"
    return f"💰 {bal.get('bnb_fmt', '0')} BNB ({bal.get('usd_fmt', '$0')})\n`{bal.get('address', '')[:10]}…`"
