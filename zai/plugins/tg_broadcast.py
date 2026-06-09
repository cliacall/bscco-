"""TG 播报格式 — 极简紧凑，仅 DM，无废话"""

from datetime import datetime
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Shanghai")


def _now() -> datetime:
    return datetime.now(TZ)


def _t() -> str:
    return _now().strftime("%H:%M")


def _date() -> str:
    return f"{_now().month}/{_now().day}"


def score_heart(score: float) -> str:
    if score >= 8:
        return "💚"
    if score >= 6:
        return "💛"
    return "🤍"


# ① 开单通知
def fmt_open(symbol: str, leverage: int, amount_usd: float, sl: float, tps: list[float]) -> str:
    tp_str = " + ".join(f"${tp:.2f}" for tp in tps[:2])
    return f"🟢 {symbol} {leverage}x 开单\n${amount_usd:.0f} | SL ${sl:.2f} | TP {tp_str}"


# ② 平仓通知
def fmt_close_profit(symbol: str, leverage: int, pnl: float) -> str:
    return f"✅ {symbol} {leverage}x +{pnl:.2f}"


def fmt_close_loss(symbol: str, leverage: int, pnl: float) -> str:
    return f"❌ {symbol} {leverage}x {pnl:.2f}"


def fmt_close_timeout(symbol: str, leverage: int, pnl: float) -> str:
    return f"⏰ {symbol} {leverage}x +{pnl:.2f}"


def fmt_close_volatile(symbol: str, leverage: int, pnl: float) -> str:
    return f"🌊 {symbol} {leverage}x {pnl:.2f}"


# ③ 信号播报（每6h）
def fmt_signal_broadcast(signals: list[dict]) -> str | None:
    if not signals:
        return None
    lines = [f"📡 信号得分播报 ({_t()})"]
    for i, s in enumerate(signals[:5], 1):
        sym = s.get("symbol", "?")
        score = s.get("score", 0)
        heart = score_heart(score / 10 if score > 10 else score)
        lines.append(f"{i}. 🟢 {sym} {heart}{score:.0f}分")
    return "\n".join(lines)


# ④ 每日报告
def fmt_daily_report(
    equity: float,
    trades: list[dict],
    strategy_stats: dict[str, dict],
) -> str | None:
    if not trades:
        return None
    wins = [t for t in trades if t.get("pnl", 0) > 0]
    losses = [t for t in trades if t.get("pnl", 0) < 0]
    net = sum(t.get("pnl", 0) for t in trades)
    lines = [f"📊 日终报告 — {_date()}", f"💰 权益 ${equity:.2f}", f"📈 今日交易：{len(trades)}笔"]
    for t in wins:
        lines.append(f"✅ +{t['pnl']:.2f} ({t['symbol']})")
    for t in losses:
        lines.append(f"❌ {t['pnl']:.2f} ({t['symbol']})")
    lines.append(f"📊 净 {net:+.2f}")
    if strategy_stats:
        lines.append("🏆 策略胜率")
        for name, st in strategy_stats.items():
            w, l = st.get("wins", 0), st.get("losses", 0)
            total = w + l
            wr = (w / total * 100) if total else 0
            lines.append(f"{name} {w}/{total} {wr:.1f}%")
    return "\n".join(lines)


# ⑤ 自我升级报告
def fmt_upgrade_report(
    period_stats: dict,
    adjustments: list[str],
    pending: list[str],
    ai_insight: str = "",
) -> str | None:
    if not adjustments and not pending and not ai_insight:
        return None
    s = period_stats
    lines = [f"🔧 自我升级 — {_t()}", "📈 上周期成绩",
             f"{s.get('trades', 0)}笔 | 胜率 {s.get('win_rate', 0):.1f}% | EV {s.get('ev', 0):+.2f}",
             f"夏普 {s.get('sharpe', 0):+.2f} | 最大回撤 ${s.get('max_dd', 0):.0f}"]
    if ai_insight:
        lines.append(f"🧠 {ai_insight[:120]}")
    if adjustments:
        lines.append("🔄 已调整")
        for a in adjustments:
            lines.append(f"▸ {a}")
    if pending:
        lines.append("📋 下次规则")
        for p in pending:
            lines.append(f"▸ {p}")
    return "\n".join(lines)


# ⑥ 日报汇总行
def fmt_daily_summary(
    equity: float, open_count: int, trade_count: int, net_pnl: float,
    wallet_bnb: float | None = None,
) -> str | None:
    if trade_count == 0 and open_count == 0:
        return None
    sign = "+" if net_pnl >= 0 else ""
    w = f" | {wallet_bnb:.3f}BNB" if wallet_bnb is not None else ""
    return f"📊 bscco — {_date()} | ${equity:.2f}{w} | {open_count}仓 | {trade_count}单 {sign}${net_pnl:.2f}"


# ⑦ Daybook
def fmt_daybook(entries: list[dict]) -> str | None:
    if not entries:
        return None
    lines = [f"📖 {_date()} daybook"]
    for e in entries:
        sym = e.get("symbol", "?")
        pnl = e.get("pnl", 0)
        sign = "+" if pnl >= 0 else ""
        reason = e.get("reflection", e.get("reason", ""))[:50]
        rule = e.get("next_rule", "")
        line = f"{sym} {sign}{pnl:.2f}：{reason}"
        if rule:
            line += f" → {rule[:40]}"
        lines.append(line)
    if entries:
        lesson = entries[0].get("next_rule") or entries[0].get("lesson", "")
        if lesson:
            lines.append(f"下次：{lesson[:60]}")
    return "\n".join(lines)
