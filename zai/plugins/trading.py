"""Cobo 交易 — contract_call 买入 / 卖出"""

import logging
import time
from decimal import Decimal
from typing import Any

from config.settings import get_settings
from plugins.cobo_local import resolve_cobo
from plugins.cobo_pact import _friendly_denial, ensure_trading_pact

logger = logging.getLogger(__name__)

PANCAKE_ROUTER = "0x10ED43C718714eb63d5aA57B78B54704E256024E"
TOKEN_MANAGER2 = "0x5c952063c7fc8610FFDB798152D69F0B9550762b"
WBNB = "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c"
TEST_AMOUNT_BNB = 0.0001
CHAIN_ID = "BSC_BNB"

ROUTER_ABI = [
    {
        "name": "swapExactETHForTokens",
        "type": "function",
        "inputs": [
            {"name": "amountOutMin", "type": "uint256"},
            {"name": "path", "type": "address[]"},
            {"name": "to", "type": "address"},
            {"name": "deadline", "type": "uint256"},
        ],
        "outputs": [{"name": "amounts", "type": "uint256[]"}],
    },
    {
        "name": "swapExactTokensForETH",
        "type": "function",
        "inputs": [
            {"name": "amountIn", "type": "uint256"},
            {"name": "amountOutMin", "type": "uint256"},
            {"name": "path", "type": "address[]"},
            {"name": "to", "type": "address"},
            {"name": "deadline", "type": "uint256"},
        ],
        "outputs": [{"name": "amounts", "type": "uint256[]"}],
    },
]

ERC20_ABI = [
    {"name": "balanceOf", "type": "function", "inputs": [{"name": "account", "type": "address"}], "outputs": [{"type": "uint256"}]},
    {"name": "approve", "type": "function", "inputs": [{"name": "spender", "type": "address"}, {"name": "amount", "type": "uint256"}], "outputs": [{"type": "bool"}]},
    {"name": "allowance", "type": "function", "inputs": [{"name": "owner", "type": "address"}, {"name": "spender", "type": "address"}], "outputs": [{"type": "uint256"}]},
]

TM2_ABI = [
    {
        "name": "buyTokenAMAP",
        "type": "function",
        "stateMutability": "payable",
        "inputs": [
            {"name": "token", "type": "address"},
            {"name": "funds", "type": "uint256"},
            {"name": "minAmount", "type": "uint256"},
        ],
        "outputs": [],
    },
    {
        "name": "sellToken",
        "type": "function",
        "inputs": [
            {"name": "token", "type": "address"},
            {"name": "amount", "type": "uint256"},
        ],
        "outputs": [],
    },
]


def _get_cobo() -> dict[str, str]:
    s = get_settings()
    return resolve_cobo(s.cobo_api_key, s.cobo_wallet_id, s.cobo_api_url)


def _valid_address(addr: str) -> bool:
    a = addr.strip().lower()
    return a.startswith("0x") and len(a) == 42


def _tx_id(result: Any) -> str:
    if isinstance(result, dict):
        return (
            result.get("transaction_uuid")
            or result.get("cobo_transaction_id")
            or result.get("pact_id")
            or result.get("id")
            or str(result)
        )
    return str(result)


def _amount_wei(amount_bnb: float) -> int:
    return int(Decimal(str(amount_bnb)) * Decimal("1000000000000000000"))


def _native_value(amount_bnb: float) -> str:
    return format(Decimal(str(amount_bnb)), "f")


def _sell_amount(balance: int, sell_pct: float) -> int:
    if sell_pct >= 99.999:
        return balance
    return int(Decimal(balance) * Decimal(str(sell_pct)) / Decimal("100"))


async def _get_wallet_address(client, wallet_id: str) -> str | None:
    cobo = _get_cobo()
    if cobo.get("bsc_address"):
        return cobo["bsc_address"]
    addresses = await client.list_wallet_addresses(wallet_id)
    for a in addresses:
        if a.get("chain_type", "").upper() in ("BSC", "BSC_BNB", "EVM"):
            return a.get("address")
        chains = [c.upper() for c in (a.get("compatible_chains") or [])]
        if "BSC_BNB" in chains and (a.get("address") or "").startswith("0x"):
            return a.get("address")
    return None


def _encode(w3, fn) -> str:
    data = fn._encode_transaction_data()
    return data if isinstance(data, str) else data.hex()


async def _token_stage(token_address: str) -> str:
    """new_creation | near_completion | graduated"""
    from plugins.fourmeme_scan import lookup_fourmeme_token
    pool = await lookup_fourmeme_token(token_address)
    if pool:
        return pool.get("stage") or "new_creation"
    return "graduated"


async def _contract_call(
    client,
    wallet_id: str,
    *,
    contract_addr: str,
    value: str,
    calldata: str,
    description: str,
    pact: dict[str, Any] | None = None,
) -> dict[str, Any]:
    src = await _get_wallet_address(client, wallet_id)
    target = client
    scoped_client = None
    pact_api_key = (pact or {}).get("api_key")
    if pact_api_key:
        from cobo_agentic_wallet.client import WalletAPIClient

        cobo = _get_cobo()
        scoped_client = WalletAPIClient(base_url=cobo["api_url"], api_key=pact_api_key)
        target = scoped_client
    try:
        result = await target.contract_call(
            wallet_id,
            chain_id=CHAIN_ID,
            contract_addr=contract_addr,
            value=value,
            calldata=calldata,
            src_addr=src,
            description=description,
        )
    finally:
        if scoped_client is not None:
            await scoped_client.close()
    tid = _tx_id(result)
    return {"success": True, "pact_id": tid, "transaction_id": tid, "raw": result}


async def buy_token(token_address: str, amount_bnb: float) -> dict[str, Any]:
    from plugins.fourmeme_scan import ensure_fourmeme

    settings = get_settings()
    cobo = _get_cobo()
    if not settings.cobo_configured or not cobo.get("api_key"):
        return {"success": False, "message": "❌ 未找到 caw 钱包 · 运行 caw wallet pair 配对"}
    if amount_bnb <= 0:
        return {"success": False, "message": "❌ 金额须 > 0"}
    if not _valid_address(token_address):
        return {"success": False, "message": "❌ 无效 CA"}

    ok, msg = await ensure_fourmeme(token_address)
    if not ok:
        return {"success": False, "message": msg}

    pact = await ensure_trading_pact()
    if not pact.get("ok"):
        return {"success": False, "message": pact.get("message", "❌ 缺少 Cobo 交易授权"), "pact_status": pact.get("status")}

    token_address = token_address.strip().lower()
    try:
        from cobo_agentic_wallet.client import WalletAPIClient
        from web3 import Web3

        amount_wei = _amount_wei(amount_bnb)
        value_bnb = _native_value(amount_bnb)
        deadline = int(time.time()) + 600
        w3 = Web3()
        router = w3.eth.contract(address=Web3.to_checksum_address(PANCAKE_ROUTER), abi=ROUTER_ABI)

        stage = await _token_stage(token_address)
        on_curve = stage in ("new_creation", "near_completion")

        async with WalletAPIClient(base_url=cobo["api_url"], api_key=cobo["api_key"]) as client:
            bsc_addr = await _get_wallet_address(client, cobo["wallet_id"])
            if not bsc_addr:
                return {"success": False, "message": "❌ 无 BSC 地址"}

            if on_curve:
                tm2 = w3.eth.contract(address=Web3.to_checksum_address(TOKEN_MANAGER2), abi=TM2_ABI)
                data = _encode(tm2, tm2.functions.buyTokenAMAP(
                    Web3.to_checksum_address(token_address), amount_wei, 0,
                ))
                r = await _contract_call(
                    client, cobo["wallet_id"],
                    contract_addr=TOKEN_MANAGER2,
                    value=value_bnb,
                    calldata=data,
                    description=f"four.meme buy {amount_bnb} BNB → {token_address[:10]}",
                    pact=pact,
                )
                route = "four.meme 内盘"
            else:
                path = [Web3.to_checksum_address(WBNB), Web3.to_checksum_address(token_address)]
                data = _encode(router, router.functions.swapExactETHForTokens(
                    0, path, Web3.to_checksum_address(bsc_addr), deadline,
                ))
                r = await _contract_call(
                    client, cobo["wallet_id"],
                    contract_addr=PANCAKE_ROUTER,
                    value=value_bnb,
                    calldata=data,
                    description=f"Pancake buy {amount_bnb} BNB → {token_address[:10]}",
                    pact=pact,
                )
                route = "Pancake（已射出）"

            return {
                **r,
                "message": (
                    f"🟢 买入已提交 ({route})\n💰 {amount_bnb} BNB\n🎯 `{token_address}`\n"
                    f"📋 Tx: {r['pact_id']}\n⏳ Cobo App 批准"
                ),
                "token_address": token_address,
                "amount_bnb": amount_bnb,
                "action": "buy",
                "route": route,
            }
    except ImportError:
        return {"success": False, "message": "❌ pip install cobo-agentic-wallet web3"}
    except Exception as e:
        return {"success": False, "message": _friendly_denial(e)}


async def sell_token(token_address: str, sell_pct: float = 100.0) -> dict[str, Any]:
    settings = get_settings()
    cobo = _get_cobo()
    if not settings.cobo_configured or not cobo.get("api_key"):
        return {"success": False, "message": "❌ 未找到 caw 钱包"}
    if not _valid_address(token_address):
        return {"success": False, "message": "❌ 无效 CA"}

    pact = await ensure_trading_pact()
    if not pact.get("ok"):
        return {"success": False, "message": pact.get("message", "❌ 缺少 Cobo 交易授权"), "pact_status": pact.get("status")}

    token_address = token_address.strip().lower()
    try:
        from cobo_agentic_wallet.client import WalletAPIClient
        from web3 import Web3

        w3 = Web3(Web3.HTTPProvider(settings.bsc_rpc_url))
        deadline = int(time.time()) + 600

        async with WalletAPIClient(base_url=cobo["api_url"], api_key=cobo["api_key"]) as client:
            bsc_addr = await _get_wallet_address(client, cobo["wallet_id"])
            if not bsc_addr:
                return {"success": False, "message": "❌ 无 BSC 地址"}

            token = w3.eth.contract(address=Web3.to_checksum_address(token_address), abi=ERC20_ABI)
            balance = token.functions.balanceOf(Web3.to_checksum_address(bsc_addr)).call()
            if balance <= 0:
                return {"success": False, "message": "❌ 代币余额为 0"}

            amount_in = _sell_amount(balance, sell_pct)
            stage = await _token_stage(token_address)
            on_curve = stage in ("new_creation", "near_completion")

            if on_curve:
                spender = TOKEN_MANAGER2
                tm2 = w3.eth.contract(address=Web3.to_checksum_address(TOKEN_MANAGER2), abi=TM2_ABI)
                sell_data = _encode(tm2, tm2.functions.sellToken(
                    Web3.to_checksum_address(token_address), amount_in,
                ))
                route = "four.meme 内盘"
            else:
                spender = PANCAKE_ROUTER
                router = w3.eth.contract(address=Web3.to_checksum_address(PANCAKE_ROUTER), abi=ROUTER_ABI)
                path = [Web3.to_checksum_address(token_address), Web3.to_checksum_address(WBNB)]
                sell_data = _encode(router, router.functions.swapExactTokensForETH(
                    amount_in, 0, path, Web3.to_checksum_address(bsc_addr), deadline,
                ))
                route = "Pancake（已射出）"

            approve_data = _encode(token, token.functions.approve(
                Web3.to_checksum_address(spender), amount_in,
            ))

            allowance = token.functions.allowance(
                Web3.to_checksum_address(bsc_addr),
                Web3.to_checksum_address(spender),
            ).call()
            if allowance < amount_in:
                ar = await _contract_call(
                    client, cobo["wallet_id"],
                    contract_addr=token_address,
                    value="0",
                    calldata=approve_data,
                    description=f"Approve sell {token_address[:10]}",
                    pact=pact,
                )
                return {
                    "success": True,
                    "pact_id": ar.get("pact_id"),
                    "message": (
                        f"🟡 已提交卖出授权 Approve ({route})\n🎯 `{token_address}`\n"
                        f"📋 Approve: {ar.get('pact_id')}\n"
                        "📱 请在 Cobo App 批准；链上生效后再点一次清仓"
                    ),
                    "action": "approve_sell",
                    "route": route,
                    "needs_retry": True,
                    "allowance": allowance,
                    "amount_in": amount_in,
                }

            sr = await _contract_call(
                client, cobo["wallet_id"],
                contract_addr=spender,
                value="0",
                calldata=sell_data,
                description=f"Sell {sell_pct}% {token_address[:10]}",
                pact=pact,
            )
            return {
                "success": True,
                "pact_id": sr.get("pact_id"),
                "message": (
                    f"🔴 卖出 {sell_pct}% 已提交 ({route})\n🎯 `{token_address}`\n"
                    f"📋 Sell: {sr.get('pact_id')}\n⏳ Cobo App 批准"
                ),
                "action": "sell",
                "route": route,
                "allowance": allowance,
                "amount_in": amount_in,
            }
    except Exception as e:
        return {"success": False, "message": _friendly_denial(e)}


async def test_roundtrip(token_address: str, amount_bnb: float = TEST_AMOUNT_BNB) -> dict[str, Any]:
    buy = await buy_token(token_address, amount_bnb)
    if not buy.get("success"):
        return buy
    sell = await sell_token(token_address)
    return {
        "success": sell.get("success", False),
        "message": (
            f"🧪 测试往返\n1️⃣ 买 {amount_bnb} BNB — {buy.get('pact_id')}\n"
            f"2️⃣ 卖 — {sell.get('pact_id', '失败')}\n⏳ Cobo App 依次批准"
        ),
        "steps": [
            {"step": "buy", "ok": True, "pact_id": buy.get("pact_id")},
            {"step": "sell", "ok": sell.get("success"), "pact_id": sell.get("pact_id")},
        ],
        "action": "test",
    }
