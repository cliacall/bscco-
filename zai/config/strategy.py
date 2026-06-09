"""bscco机器人 — 策略参数（内部引擎，对外不暴露来源）"""

from dataclasses import dataclass, field

APP_NAME = "bscco机器人"
PRIMARY_ONLY = True

FOURMEME_PLATFORMS = (
    "fourmeme", "fourmeme_agent", "bn_fourmeme", "four_xmode_agent",
)

SCAN_INTERVAL_SEC = 30
STRONG_BUY_SCORE = 70
SCORE_DISPLAY_MIN = 50
DEFAULT_BUY_AMOUNT_BNB = 0.05

TIER_BUY = 70
TIER_WATCH = 50
TIER_MONITOR = 35

DEFAULT_WEIGHTS = {
    "liquidity": 25,
    "volume": 20,
    "buy_sell_ratio": 15,
    "market_cap": 10,
    "pool_age": 10,
    "holder_security": 20,
}


@dataclass(frozen=True)
class ArenaAgent:
    id: str
    name: str
    ticker: str
    strategy: str
    buy_threshold: int
    description: str
    weights: dict[str, int] = field(default_factory=dict)


ARENA_AGENTS: list[ArenaAgent] = [
    ArenaAgent("a1", "稳核", "CORE", "steady_flow", 72, "保守流动性+安全",
               {"liquidity": 30, "volume": 15, "buy_sell_ratio": 10, "market_cap": 10, "pool_age": 15, "holder_security": 20}),
    ArenaAgent("a2", "猛追", "RUSH", "momentum_chase", 68, "激进量能+买卖比",
               {"liquidity": 15, "volume": 30, "buy_sell_ratio": 25, "market_cap": 10, "pool_age": 10, "holder_security": 10}),
    ArenaAgent("a3", "跟单", "KOL", "social_tail", 70, "KOL/热度跟随",
               {"liquidity": 20, "volume": 25, "buy_sell_ratio": 20, "market_cap": 15, "pool_age": 5, "holder_security": 15}),
    ArenaAgent("a4", "筹码", "HOLD", "holder_watch", 75, "持有者结构监控",
               {"liquidity": 20, "volume": 10, "buy_sell_ratio": 10, "market_cap": 10, "pool_age": 10, "holder_security": 40}),
    ArenaAgent("a5", "狙击", "SNIP", "early_entry", 65, "新池早期狙击",
               {"liquidity": 10, "volume": 20, "buy_sell_ratio": 15, "market_cap": 5, "pool_age": 35, "holder_security": 15}),
    ArenaAgent("a6", "逆势", "REV", "fade_spike", 78, "超买回落逆向",
               {"liquidity": 25, "volume": 15, "buy_sell_ratio": 30, "market_cap": 15, "pool_age": 5, "holder_security": 10}),
    ArenaAgent("a7", "量能", "VOL", "volume_break", 69, "放量突破",
               {"liquidity": 15, "volume": 35, "buy_sell_ratio": 20, "market_cap": 10, "pool_age": 10, "holder_security": 10}),
    ArenaAgent("a8", "均衡", "BAL", "balanced", 71, "六维均衡",
               DEFAULT_WEIGHTS),
]

TAKE_PROFIT_TIERS = [
    {"gain_pct": 20, "sell_pct": 30},
    {"gain_pct": 25, "sell_pct": 30},
    {"gain_pct": 30, "sell_pct": 20},
    {"gain_pct": 35, "sell_pct": 10},
    {"gain_pct": 40, "sell_pct": 10},
]

HARD_STOP_LOSS_PCT = -18
TRAILING_STOP_ACTIVATE_PCT = 25
TRAILING_STOP_PULLBACK_PCT = 15

SMART_EXIT = {
    "holder_drop_pct": 10,
    "score_decay_below": 35,
    "sell_tax_max": 10,
}
