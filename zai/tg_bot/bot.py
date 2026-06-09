"""bscco机器人 — Telegram 命令 + DM 播报"""

import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from config.strategy import APP_NAME, DEFAULT_BUY_AMOUNT_BNB, HARD_STOP_LOSS_PCT, TAKE_PROFIT_TIERS
from config.settings import get_settings
from core.agent import TradingAgent
from plugins import memecoin_score, trading, tg_broadcast, trade_journal, wallet_balance
from plugins.fourmeme_scan import get_candidates
from plugins.position_manager import get_positions
from plugins.scheduler import tick as scheduler_tick
from utils.format import is_bsc_address, safe_float

logger = logging.getLogger(__name__)


class TelegramBotService:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.agent = TradingAgent()
        self._app: Application | None = None

    async def send_notify(self, text: str) -> None:
        if not text or not self._app or not self.settings.notify_configured:
            return
        try:
            await self._app.bot.send_message(chat_id=self.settings.notify_chat_id, text=text)
        except Exception as e:
            logger.error("DM 推送失败: %s", e)

    async def notify_buy_signal(self, pool: dict) -> None:
        score = safe_float(pool.get("score"))
        if score < 70:
            return
        sym = pool.get("symbol", "?")
        addr = pool.get("token_address") or pool.get("address", "")
        trade_journal.add_signal(sym, addr, score / 10, pool.get("strategy", "fourmeme_primary"))

    async def notify_position_close(self, alert: dict) -> None:
        sym = alert.get("symbol", "?")
        gain = safe_float(alert.get("gain_pct"))
        pnl = gain * 0.1
        close_type = "profit" if gain > 0 else "loss"
        if "移动止损" in str(alert.get("alerts")):
            close_type = "volatile"
        if "硬止损" in str(alert.get("alerts")):
            close_type = "loss"
        trade = await trade_journal.record_close(
            alert["address"], alert.get("current_price", 0), pnl, close_type,
        )
        if not trade:
            return
        lev = trade.get("leverage", 5)
        fmt = {
            "profit": tg_broadcast.fmt_close_profit,
            "loss": tg_broadcast.fmt_close_loss,
            "timeout": tg_broadcast.fmt_close_timeout,
            "volatile": tg_broadcast.fmt_close_volatile,
        }
        await self.send_notify(fmt.get(close_type, tg_broadcast.fmt_close_loss)(sym, lev, pnl))

    async def run_scheduler(self) -> None:
        import asyncio
        while True:
            try:
                await scheduler_tick(self.send_notify)
            except Exception as e:
                logger.error("调度器错误: %s", e)
            await asyncio.sleep(60)

    async def _cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.reply_text(
            f"🤖 {APP_NAME}\n"
            "four.meme 一级 | /balance /buy /candidates /positions"
        )

    async def _cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._cmd_start(update, context)

    async def _cmd_balance(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        bal = await wallet_balance.get_balance()
        await update.message.reply_text(wallet_balance.format_balance_text(bal), parse_mode="Markdown")

    async def _cmd_candidates(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        items = get_candidates()
        if not items:
            await update.message.reply_text("暂无 four.meme 一级候选")
            return
        signals = [{"symbol": p["symbol"], "score": p.get("score", 0) / 10} for p in items[:5]]
        msg = tg_broadcast.fmt_signal_broadcast(signals)
        await update.message.reply_text(msg or "暂无信号")

    async def _cmd_positions(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        positions = get_positions()
        opens = trade_journal.get_open_positions()
        bal = await wallet_balance.get_balance()
        if not positions and not opens:
            lines = [wallet_balance.format_balance_text(bal), "0仓"]
            await update.message.reply_text("\n".join(lines))
            return
        lines = [
            wallet_balance.format_balance_text(bal),
            f"📊 {len(opens)}仓",
        ]
        for p in (opens or positions)[:5]:
            lines.append(f"{p.get('symbol','?')} {p.get('current_gain_pct', 0):+.1f}%")
        await update.message.reply_text("\n".join(lines))

    async def _cmd_buy(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        args = context.args or []
        if len(args) < 2:
            await update.message.reply_text("/buy <CA> <BNB量>")
            return
        address, amount_str = args[0], args[1]
        try:
            amount_bnb = float(amount_str)
        except ValueError:
            await update.message.reply_text("金额无效")
            return
        if not is_bsc_address(address):
            await update.message.reply_text("地址无效")
            return

        await update.message.reply_text("⏳ 提交 Cobo...")
        ev = await memecoin_score.evaluate_token(address)
        result = await trading.buy_token(address, amount_bnb)
        await update.message.reply_text(result["message"])
        if not result.get("success"):
            return

        price = safe_float(ev.get("price"))
        amount_usd = amount_bnb * 600
        sl = price * (1 + HARD_STOP_LOSS_PCT / 100) if price else 0
        tps = [price * (1 + t["gain_pct"] / 100) for t in TAKE_PROFIT_TIERS[:2]] if price else []
        top = ev.get("best_signal") or ev.get("top_agent") or {}
        strategy = ev.get("strategy") or top.get("strategy", "fourmeme_primary")

        from plugins.position_manager import add_position
        add_position(address, ev["symbol"], price, amount_bnb, strategy)
        await trade_journal.record_open(ev["symbol"], address, amount_usd, price, strategy, sl, tps)
        await self.send_notify(tg_broadcast.fmt_open(ev["symbol"], 5, amount_usd, sl, tps))

    async def _handle_address(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        text = (update.message.text or "").strip()
        if not is_bsc_address(text):
            return
        try:
            from plugins.fourmeme_scan import ensure_fourmeme
            ok, msg = await ensure_fourmeme(text)
            if not ok:
                await update.message.reply_text(msg)
                return
            ev = await memecoin_score.evaluate_token(text)
            score = ev.get("score", 0)
            if score >= 70:
                trade_journal.add_signal(ev["symbol"], text, score / 10, ev.get("strategy", "fourmeme_primary"))
            card = (
                f"{ev['tier_emoji']} {ev['symbol']} {score}/100 · four.meme一级\n"
                f"{ev['price_fmt']}\n`{text}`"
            )
            await update.message.reply_text(card, parse_mode="Markdown")
            if self.agent.configured and score >= 50:
                ai = await self.agent.analyze_token(ev)
                await update.message.reply_text(ai[:300])
        except Exception as e:
            await update.message.reply_text(f"❌ {e}")

    async def run(self) -> None:
        if not self.settings.telegram_configured:
            logger.warning("Telegram 未配置")
            import asyncio
            while True:
                await asyncio.sleep(3600)
            return

        self._app = Application.builder().token(self.settings.telegram_bot_token).build()
        for cmd, fn in [
            ("start", self._cmd_start), ("help", self._cmd_help),
            ("buy", self._cmd_buy), ("balance", self._cmd_balance),
            ("candidates", self._cmd_candidates), ("positions", self._cmd_positions),
        ]:
            self._app.add_handler(CommandHandler(cmd, fn))
        self._app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_address))

        await self._app.initialize()
        await self._app.start()
        await self._app.updater.start_polling(drop_pending_updates=True)
        logger.info("%s TG就绪 | DM→%s", APP_NAME, self.settings.notify_chat_id)
        await self.send_notify(f"✅ {APP_NAME}启动成功")

        import asyncio
        try:
            while True:
                await asyncio.sleep(1)
        finally:
            await self._app.updater.stop()
            await self._app.stop()
            await self._app.shutdown()
