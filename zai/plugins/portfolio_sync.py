"""持仓/余额实时同步 — 链上 balanceOf + Cobo 余额"""

import json
import logging
import os
import time
from typing import Any

from config.settings import get_settings
from plugins.cobo_local import resolve_cobo
from plugins import token_scan
from utils.format import safe_float

logger = logging.getLogger(__name__)

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
WATCHED_FILE = os.path.join(DATA_DIR, "watched_tokens.json")
PENDING_FILE = os.path.join(DATA_DIR, "pending_buys.json")

ERC20_ABI = [
    {"name": "balanceOf", "type": "function", "inputs": [{"name": "account", "type": "address"}], "outputs": [{"type": "uint256"}]},
    {"name": "decimals", "type": "function", "inputs": [], "outputs": [{"type": "uint8"}]},
    {"name": "symbol", "type": "function", "inputs": [], "outputs": [{"type": "string"}]},
]


def _ensure() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)


def _load_json(path: str, default: Any) -> Any:
    _ensure()
    if not os.path.exists(path):
        return default
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return default


def _save_json(path: str, data: Any) -> None:
    _ensure()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def watch_token(address: str, symbol: str = "", amount_bnb: float = 0) -> None:
    """买入/查币后加入监控列表"""
    addr = address.lower().strip()
    items = _load_json(WATCHED_FILE, [])
    items = [i for i in items if i.get("address") != addr]
    items.insert(0, {"address": addr, "symbol": symbol or "?", "amount_bnb": amount_bnb, "ts": time.time()})
    _save_json(WATCHED_FILE, items[:100])


def record_pending_buy(address: str, symbol: str, amount_bnb: float, pact_id: str = "") -> None:
    pending = _load_json(PENDING_FILE, [])
    addr = address.lower().strip()
    pending = [p for p in pending if p.get("address") != addr]
    pending.insert(0, {
        "address": addr,
        "symbol": symbol,
        "amount_bnb": amount_bnb,
        "pact_id": pact_id,
        "submitted_at": time.time(),
    })
    _save_json(PENDING_FILE, pending[:20])


def _collect_watch_addrs() -> list[dict]:
    from plugins.position_manager import _load as load_positions

    seen: set[str] = set()
    out: list[dict] = []

    def add(addr: str, symbol: str = "?", amount_bnb: float = 0) -> None:
        a = addr.lower().strip()
        if not a.startswith("0x") or a in seen:
            return
        seen.add(a)
        out.append({"address": a, "symbol": symbol, "amount_bnb": amount_bnb})

    for item in _load_json(WATCHED_FILE, []):
        add(item.get("address", ""), item.get("symbol", "?"), safe_float(item.get("amount_bnb")))
    for p in load_positions():
        add(p.get("address", ""), p.get("symbol", "?"), safe_float(p.get("amount_bnb")))
    for p in _load_json(PENDING_FILE, []):
        add(p.get("address", ""), p.get("symbol", "?"), safe_float(p.get("amount_bnb")))
    return out


async def _wallet_address() -> str | None:
    from plugins.wallet_balance import _resolve_address
    addr, _ = await _resolve_address()
    return addr


async def _token_balance(w3, wallet: str, token: str) -> tuple[int, int, str, bool]:
    from web3 import Web3
    contract = w3.eth.contract(address=Web3.to_checksum_address(token), abi=ERC20_ABI)
    try:
        raw = contract.functions.balanceOf(Web3.to_checksum_address(wallet)).call()
        dec = contract.functions.decimals().call()
        sym = contract.functions.symbol().call()
        return raw, dec, sym, True
    except Exception:
        return 0, 18, "?", False


async def sync_live_positions() -> list[dict[str, Any]]:
    """从链上读取真实持仓，合并 pending"""
    wallet = await _wallet_address()
    if not wallet:
        return []

    from web3 import Web3
    settings = get_settings()
    w3 = Web3(Web3.HTTPProvider(settings.bsc_rpc_url))
    watch = _collect_watch_addrs()
    positions: list[dict[str, Any]] = []
    confirmed_addrs: set[str] = set()
    failed_addrs: set[str] = set()
    zero_addrs: set[str] = set()

    for item in watch:
        addr = item["address"]
        raw, dec, onchain_sym, ok = await _token_balance(w3, wallet, addr)
        if not ok:
            failed_addrs.add(addr)
            continue
        if raw <= 0:
            zero_addrs.add(addr)
            continue
        confirmed_addrs.add(addr)
        amount = raw / (10 ** dec)
        try:
            td = await token_scan.analyze_token(addr)
            price = safe_float(td.get("price"))
            symbol = td.get("symbol") or onchain_sym or item.get("symbol", "?")
        except Exception:
            price = 0
            symbol = onchain_sym or item.get("symbol", "?")

        entry = safe_float(item.get("amount_bnb")) or 0
        gain = 0.0
        if price > 0 and entry > 0:
            est_value_bnb = amount * price / 600
            gain = ((est_value_bnb - entry) / entry) * 100 if entry else 0

        positions.append({
            "address": addr,
            "symbol": symbol,
            "entry_price": price,
            "amount_bnb": entry or safe_float(item.get("amount_bnb")),
            "token_amount": round(amount, 4),
            "current_price": price,
            "current_gain_pct": round(gain, 2),
            "status": "open",
            "strategy": "live_sync",
            "synced_at": time.time(),
        })

    pending = _load_json(PENDING_FILE, [])
    now = time.time()
    still_pending = []
    for p in pending:
        addr = p.get("address", "").lower()
        if addr in confirmed_addrs:
            continue
        if now - safe_float(p.get("submitted_at")) > 600:
            continue
        still_pending.append(p)
        positions.append({
            "address": addr,
            "symbol": p.get("symbol", "?"),
            "entry_price": 0,
            "amount_bnb": safe_float(p.get("amount_bnb")),
            "token_amount": 0,
            "current_gain_pct": 0,
            "status": "pending",
            "pact_id": p.get("pact_id", ""),
            "submitted_at": p.get("submitted_at"),
            "strategy": "pending_buy",
        })
    _save_json(PENDING_FILE, still_pending)
    active_addrs = confirmed_addrs | failed_addrs | {p.get("address", "").lower() for p in still_pending}
    watched = _load_json(WATCHED_FILE, [])
    watched = [
        item for item in watched
        if item.get("address", "").lower() not in zero_addrs or item.get("address", "").lower() in active_addrs
    ]
    _save_json(WATCHED_FILE, watched[:100])

    from plugins.position_manager import _save, _load
    saved = _load()
    saved_by_addr = {p["address"]: p for p in saved}
    merged: dict[str, dict[str, Any]] = {}
    for p in positions:
        if p.get("status") == "open":
            old = saved_by_addr.get(p["address"], {})
            p["amount_bnb"] = p.get("amount_bnb") or old.get("amount_bnb", 0)
            p["entry_price"] = old.get("entry_price") or p.get("entry_price", 0)
            if old.get("entry_price") and p.get("current_price"):
                ep = safe_float(old["entry_price"])
                cp = safe_float(p["current_price"])
                if ep > 0:
                    p["current_gain_pct"] = round(((cp - ep) / ep) * 100, 2)
            merged[p["address"]] = {**old, **p}
    for p in saved:
        addr = p.get("address", "")
        if p.get("status") == "open" and addr in failed_addrs:
            merged[addr] = p
    for p in positions:
        if p.get("status") == "pending":
            merged[p["address"]] = p
    final = [p for p in merged.values() if p.get("status") in ("open", "pending")]
    final.sort(key=lambda x: (0 if x.get("status") == "pending" else 1, -(x.get("synced_at") or x.get("submitted_at") or 0)))
    _save(final)
    return final


async def get_live_bnb_balance() -> dict[str, Any]:
    """Cobo force_refresh + RPC，带更新时间"""
    from plugins.wallet_balance import get_balance, BNB_USD_ESTIMATE

    cobo = resolve_cobo(
        get_settings().cobo_api_key,
        get_settings().cobo_wallet_id,
        get_settings().cobo_api_url,
    )
    base = await get_balance()
    rpc_ok = bool(base.get("configured") and base.get("address") and not base.get("error"))
    updated = time.time()

    if cobo.get("api_key") and cobo.get("wallet_id"):
        try:
            from cobo_agentic_wallet.client import WalletAPIClient
            async with WalletAPIClient(base_url=cobo["api_url"], api_key=cobo["api_key"]) as client:
                rows = await client.list_balances(
                    cobo["wallet_id"], chain_id="BSC_BNB", force_refresh=True,
                )
                if isinstance(rows, list):
                    for row in rows:
                        if row.get("token_id") == "BSC_BNB":
                            bnb = safe_float(row.get("amount"))
                            base["cobo_bnb"] = round(bnb, 6)
                            base["cobo_bnb_fmt"] = f"{bnb:.6f}"
                            base["cobo_source"] = "cobo_live"
                            ts = row.get("balance_updated_at") or row.get("updated_at")
                            if ts:
                                base["cobo_balance_updated_at"] = ts
                            if not rpc_ok:
                                base["bnb"] = round(bnb, 6)
                                base["usd"] = round(bnb * BNB_USD_ESTIMATE, 2)
                                base["bnb_fmt"] = f"{bnb:.6f}"
                                base["usd_fmt"] = f"${bnb * BNB_USD_ESTIMATE:.2f}"
                                base["source"] = "cobo_live"
                            break
        except Exception as e:
            logger.debug("Cobo live balance: %s", e)

    if rpc_ok:
        base["source"] = "bsc_rpc_live"
    base["updated_at"] = updated
    return base
