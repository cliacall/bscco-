#!/usr/bin/env python3
"""bscco机器人 — BSC four.meme 一级 meme"""

import asyncio
import logging
import signal
import sys

from config.settings import get_settings
from config.strategy import APP_NAME
from tg_bot.bot import TelegramBotService
from web.server import run_web_server

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("main")


async def gmgn_puller() -> None:
    from plugins import gmgn_intel
    settings = get_settings()
    interval = settings.gmgn_pull_interval
    if gmgn_intel.cli_ready():
        await gmgn_intel.pull_smartmoney(min_interval=0)
    while True:
        try:
            await gmgn_intel.pull_smartmoney(min_interval=interval)
        except Exception as e:
            logger.error("聪明钱拉取失败: %s", e)
        await asyncio.sleep(interval)


async def pool_scanner(tg: TelegramBotService) -> None:
    from plugins.fourmeme_scan import scan_fourmeme
    from plugins.position_manager import monitor_positions
    from plugins.strategy_evolver import evolve_cycle

    settings = get_settings()
    logger.info("four.meme 新币扫描启动 (GMGN 每 %ds)", settings.scan_interval)

    while True:
        try:
            found = await scan_fourmeme(on_buy_signal=tg.notify_buy_signal)
            if found:
                logger.info("four.meme 新币 %d", len(found))
            from plugins.ai_trader import auto_trade_cycle
            ai_result = await auto_trade_cycle()
            if ai_result.get("decisions"):
                logger.info("🤖 AI 决策 %d", len(ai_result["decisions"]))
            evo = await evolve_cycle()
            if evo.get("boosted"):
                logger.info("聪明钱集群 %d", len(evo["boosted"]))
            for alert in await monitor_positions():
                await tg.notify_position_close(alert)
        except Exception as e:
            logger.error("扫描失败: %s", e)
        await asyncio.sleep(settings.scan_interval)


async def main() -> None:
    from plugins import trade_journal

    settings = get_settings()
    tg = TelegramBotService()
    removed = trade_journal.purge_non_fourmeme_signals()
    if removed:
        logger.info("已清理 %d 条非 four.meme 信号", removed)

    logger.info("=" * 50)
    logger.info("🤖 %s 启动", APP_NAME)
    logger.info("🎯 four.meme 发射币 + GMGN 自主探机会")
    logger.info("🌐 前端展示台: http://localhost:3000 (API :%s)", settings.web_port)
    logger.info("📱 Telegram: %s", "已配置" if settings.telegram_configured else "未配置")
    logger.info("🧠 DeepSeek: %s", "已配置" if settings.deepseek_configured else "未配置")
    from plugins.cobo_local import caw_available, load_from_caw
    caw = load_from_caw() if caw_available() else None
    logger.info(
        "💰 Cobo: %s",
        f"本机 caw ({caw['wallet_id'][:8]}…)" if caw else ("已配置" if settings.cobo_configured else "未配置"),
    )
    from plugins import gmgn_intel
    logger.info("📡 聪明钱: %s", "就绪" if gmgn_intel.cli_ready() else "待配置 gmgn-cli")
    from plugins.cobo_pact import ensure_trading_pact, get_pact_status
    pact_st = await get_pact_status()
    if pact_st.get("status") == "active":
        logger.info("✅ Cobo Pact 已激活 %s…", str(pact_st.get("pact_id", ""))[:8])
    elif pact_st.get("status") == "pending_approval":
        logger.warning("⏳ Cobo Pact 待批准 — 请在 App 批准后才能交易")
    elif caw or settings.cobo_configured:
        submitted = await ensure_trading_pact()
        if submitted.get("status") == "submitted":
            logger.warning("📤 已提交交易 Pact — 请在 Cobo App 批准")
        elif submitted.get("status") == "pending_approval":
            logger.warning("⏳ Cobo Pact 待批准 — 请在 App 批准后才能交易")

    logger.info("=" * 50)

    tasks = [
        asyncio.create_task(tg.run(), name="telegram"),
        asyncio.create_task(tg.run_scheduler(), name="scheduler"),
        asyncio.create_task(run_web_server(), name="web"),
        asyncio.create_task(gmgn_puller(), name="gmgn"),
        asyncio.create_task(pool_scanner(tg), name="scanner"),
    ]

    def shutdown():
        logger.info("正在关闭...")
        for t in tasks:
            t.cancel()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, shutdown)
        except NotImplementedError:
            pass

    try:
        await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        logger.info("已退出")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
