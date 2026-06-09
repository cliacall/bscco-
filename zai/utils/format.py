import re


def safe_float(value, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def fmt_money(value, prefix: str = "$") -> str:
    n = safe_float(value)
    if n >= 1_000_000_000:
        return f"{prefix}{n / 1_000_000_000:,.2f}B"
    if n >= 1_000_000:
        return f"{prefix}{n / 1_000_000:,.2f}M"
    if n >= 1_000:
        return f"{prefix}{n:,.2f}"
    if n >= 1:
        return f"{prefix}{n:,.4f}"
    return f"{prefix}{n:,.8f}"


def fmt_pct(value) -> str:
    n = safe_float(value)
    sign = "+" if n >= 0 else ""
    return f"{sign}{n:.2f}%"


def pct_emoji(value) -> str:
    return "🟢" if safe_float(value) >= 0 else "🔴"


def risk_emoji(level: str) -> str:
    mapping = {
        "low": "🟢",
        "medium": "🟡",
        "high": "🔴",
        "critical": "⛔",
        "unknown": "⚪",
    }
    return mapping.get(level, "⚪")


def is_bsc_address(text: str) -> bool:
    return bool(re.fullmatch(r"0x[a-fA-F0-9]{40}", text.strip()))


def truncate_address(addr: str, head: int = 6, tail: int = 4) -> str:
    if len(addr) <= head + tail + 2:
        return addr
    return f"{addr[:head]}...{addr[-tail:]}"
