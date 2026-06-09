"""本机 caw CLI 钱包 — 自动读取已配对 Agent，无需手填 .env"""

import json
import logging
import os
import shutil
import subprocess
import time
from typing import Any

logger = logging.getLogger(__name__)

_CACHE: dict[str, Any] = {"ts": 0, "data": None}
_CACHE_TTL = 60
_PLACEHOLDERS = {"your_cobo_api_key", "your_wallet_uuid", ""}


def _caw_bin() -> str | None:
    found = shutil.which("caw")
    if found:
        return found
    local = os.path.expanduser("~/.cobo-agentic-wallet/bin/caw")
    return local if os.path.isfile(local) else None


def caw_available() -> bool:
    return _caw_bin() is not None


def _run_caw(*args: str) -> Any:
    caw = _caw_bin()
    if not caw:
        return None
    env = os.environ.copy()
    env["PATH"] = f"{os.path.dirname(caw)}:{env.get('PATH', '')}"
    try:
        proc = subprocess.run(
            [caw, *args],
            capture_output=True,
            text=True,
            timeout=20,
            env=env,
        )
        if proc.returncode != 0:
            logger.debug("caw %s: %s", " ".join(args), (proc.stderr or proc.stdout)[:120])
            return None
        text = (proc.stdout or "").strip()
        return json.loads(text) if text.startswith("{") or text.startswith("[") else text
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError) as e:
        logger.debug("caw 调用失败: %s", e)
        return None


def _pick_bsc_address(addresses: list[dict]) -> str | None:
    for a in addresses:
        if a.get("chain_type", "").upper() in ("BSC", "BSC_BNB"):
            return a.get("address")
    for a in addresses:
        chains = [c.upper() for c in (a.get("compatible_chains") or [])]
        if "BSC_BNB" in chains and (a.get("address") or "").startswith("0x"):
            return a["address"]
    for a in addresses:
        addr = a.get("address") or ""
        if addr.startswith("0x"):
            return addr
    return None


def load_from_caw(force: bool = False) -> dict[str, str] | None:
    """读取 caw wallet current + address list"""
    now = time.time()
    if not force and _CACHE["data"] and now - _CACHE["ts"] < _CACHE_TTL:
        return _CACHE["data"]

    current = _run_caw("wallet", "current", "--show-api-key")
    if not isinstance(current, dict):
        return None

    api_key = (current.get("api_key") or "").strip()
    wallet_id = (current.get("wallet_uuid") or "").strip()
    if not api_key or not wallet_id:
        return None

    addresses = _run_caw("address", "list")
    bsc_addr = ""
    if isinstance(addresses, list):
        picked = _pick_bsc_address(addresses)
        if picked:
            bsc_addr = picked.lower()

    data = {
        "api_key": api_key,
        "wallet_id": wallet_id,
        "api_url": current.get("api_url") or "https://api.agenticwallet.cobo.com",
        "bsc_address": bsc_addr,
        "agent_id": current.get("agent_id", ""),
        "wallet_name": current.get("wallet_name", "default"),
        "source": "caw_cli",
    }
    _CACHE["data"] = data
    _CACHE["ts"] = now
    logger.info("📱 已自动加载本机 caw 钱包 %s…", wallet_id[:8])
    return data


def resolve_cobo(env_key: str, env_wallet_id: str, env_api_url: str) -> dict[str, str]:
    """合并 .env 与本机 caw — .env 占位符时自动用 caw"""
    key = (env_key or "").strip()
    wid = (env_wallet_id or "").strip()
    if key not in _PLACEHOLDERS and wid not in _PLACEHOLDERS:
        return {
            "api_key": key,
            "wallet_id": wid,
            "api_url": env_api_url,
            "bsc_address": "",
            "source": "env",
        }
    caw = load_from_caw()
    if caw:
        return caw
    return {
        "api_key": key if key not in _PLACEHOLDERS else "",
        "wallet_id": wid if wid not in _PLACEHOLDERS else "",
        "api_url": env_api_url,
        "bsc_address": "",
        "source": "none",
    }


def is_cobo_ready(env_key: str, env_wallet_id: str) -> bool:
    creds = resolve_cobo(env_key, env_wallet_id, "")
    return bool(creds.get("api_key") and creds.get("wallet_id"))
