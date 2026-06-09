"""定时播报调度 — 6h信号 / 23:00日报 / 2:00&14:00升级"""

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from plugins import tg_broadcast, trade_journal, wallet_balance
from plugins.strategy_evolver import evolve_cycle

logger = logging.getLogger(__name__)
TZ = ZoneInfo("Asia/Shanghai")

_last_signal_hour: int = -1
_last_daily_date: str = ""
_last_upgrade_slot: str = ""


async def tick(send_fn) -> None:
    """每分钟检查一次定时任务"""
    global _last_signal_hour, _last_daily_date, _last_upgrade_slot
    now = datetime.now(TZ)
    h, m = now.hour, now.minute
    if m > 2:
        return

    # ③ 每6h信号播报 (0,6,12,18)
    if h % 6 == 0 and _last_signal_hour != h:
        _last_signal_hour = h
        signals = trade_journal.get_signal_queue(min_score=5)
        msg = tg_broadcast.fmt_signal_broadcast(signals)
        if msg:
            await send_fn(msg)
            logger.info("已发送 6h 信号播报")

    # ④ 23:00 日终报告
    date_key = now.strftime("%Y-%m-%d")
    if h == 23 and _last_daily_date != date_key:
        _last_daily_date = date_key
        trades = trade_journal.get_today_trades()
        if not trades:
            return
        equity = trade_journal.get_equity()
        stats = trade_journal.strategy_stats(trades)
        bal = await wallet_balance.get_balance()
        summary = tg_broadcast.fmt_daily_summary(
            equity, len(trade_journal.get_open_positions()), len(trades),
            sum(t.get("pnl", 0) for t in trades),
            wallet_bnb=bal.get("bnb") if bal.get("configured") else None,
        )
        if summary:
            await send_fn(summary)
        report = tg_broadcast.fmt_daily_report(equity, trades, stats)
        if report:
            await send_fn(report)
        daybook = trade_journal.get_today_daybook()
        db_msg = tg_broadcast.fmt_daybook(daybook)
        if db_msg:
            await send_fn(db_msg)
        logger.info("已发送日终报告")

    # ⑤ 2:00 / 14:00 自我升级
    slot = f"{date_key}-{h}"
    if h in (2, 14) and _last_upgrade_slot != slot:
        _last_upgrade_slot = slot
        result = await evolve_cycle()
        if not result["adjustments"] and not result.get("ai_insight"):
            return
        msg = tg_broadcast.fmt_upgrade_report(
            result["period_stats"], result["adjustments"], result["pending"],
            ai_insight=result.get("ai_insight", ""),
        )
        if msg:
            await send_fn(msg)
            logger.info("已发送自我升级报告")
