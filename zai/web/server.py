"""bscco机器人 — FastAPI"""

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from config.settings import get_settings
from config.strategy import APP_NAME, ARENA_AGENTS, HARD_STOP_LOSS_PCT, TAKE_PROFIT_TIERS
from core.agent import TradingAgent
from plugins import gmgn_intel, memecoin_score, trade_journal, wallet_balance
from plugins.fourmeme_scan import get_candidates, get_history_pools, get_pool_by_address, get_recent_pools
from plugins.position_manager import add_position, get_positions, reduce_position
from utils.format import safe_float
from plugins.strategy_evolver import get_state, get_strategy_runtime, validate_runtime_rules
from plugins import trading

logger = logging.getLogger(__name__)
agent = TradingAgent()
templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "templates"))
_AI_RUNNER_TASK: asyncio.Task | None = None
_GMGN_LEARNING_TASK: asyncio.Task | None = None


class BuyRequest(BaseModel):
    address: str
    amount: float


class TestTradeRequest(BaseModel):
    address: str
    amount: float = 0.0001


class SellRequest(BaseModel):
    address: str
    sell_pct: float = 100.0


class AiControlRequest(BaseModel):
    enabled: bool
    auto_trade: bool = True


class MentorRequest(BaseModel):
    wallet: str = ""
    chain: str = "bsc"


class GmgnLearningControlRequest(BaseModel):
    enabled: bool
    limit: int = 2
    cycle_interval: int = 1800


class GmgnLearningRunRequest(BaseModel):
    limit: int = 2
    chain: str = "bsc"
    force: bool = True


class GmgnSnapshotRequest(BaseModel):
    buys: list[dict] = []
    sells: list[dict] = []
    items: list[dict] | None = None
    ai_summary: str = ""
    source: str = "cursor_skill"


async def _with_timeout(coro, seconds: float, fallback):
    try:
        return await asyncio.wait_for(coro, timeout=seconds)
    except Exception as e:
        logger.warning("接口超时/失败: %s", e)
        if callable(fallback):
            value = fallback()
            if asyncio.iscoroutine(value):
                return await value
            return value
        return fallback


def _local_pool(address: str) -> dict:
    addr = address.lower().strip()
    pool = next(
        (
            p for p in get_candidates() + get_recent_pools() + get_history_pools(50)
            if (p.get("address") or p.get("token_address") or "").lower() == addr
        ),
        None,
    )
    return pool or get_pool_by_address(addr) or {}


def _portfolio_fallback_positions() -> list[dict]:
    now = time.time()
    fresh: list[dict] = []
    for p in get_positions():
        status = p.get("status")
        if status == "pending":
            fresh.append(p)
            continue
        synced_at = safe_float(p.get("synced_at"))
        if status == "open" and synced_at and now - synced_at < 45:
            fresh.append(p)
    return fresh


async def _record_submitted_buy(address: str, amount: float, result: dict, strategy_default: str) -> dict | None:
    from plugins import account_notify
    from plugins.portfolio_sync import record_pending_buy, watch_token

    addr = address.lower().strip()
    pool = _local_pool(addr)
    symbol = pool.get("symbol") or addr[:10]
    price = safe_float(pool.get("price"))
    strategy = pool.get("strategy") or strategy_default
    amount_usd = amount * 600
    sl = price * (1 + HARD_STOP_LOSS_PCT / 100) if price else 0
    tps = [price * (1 + t["gain_pct"] / 100) for t in TAKE_PROFIT_TIERS[:2]] if price else []

    watch_token(addr, symbol, amount)
    record_pending_buy(addr, symbol, amount, result.get("pact_id", ""))
    add_position(addr, symbol, price, amount, strategy)
    await account_notify.notify_open(symbol, addr, amount, result)
    return await trade_journal.record_open(
        symbol, addr, amount_usd, price, strategy, sl, tps,
        reason="手动提交 Cobo 买入，等待手机确认",
    )


async def _ai_runner_loop() -> None:
    from plugins.ai_trader import auto_trade_cycle, get_state
    from plugins.fourmeme_scan import scan_fourmeme
    from plugins.strategy_evolver import evolve_cycle

    while True:
        state = get_state()
        if not state.get("enabled"):
            return
        try:
            await scan_fourmeme()
            await auto_trade_cycle(force=True)
            await evolve_cycle()
        except Exception as e:
            logger.error("Web AI runner failed: %s", e)
        await asyncio.sleep(max(10, int(state.get("cycle_interval") or 60)))


def _ensure_ai_runner() -> None:
    global _AI_RUNNER_TASK
    if _AI_RUNNER_TASK and not _AI_RUNNER_TASK.done():
        return
    try:
        _AI_RUNNER_TASK = asyncio.create_task(_ai_runner_loop(), name="web-ai-runner")
    except RuntimeError:
        _AI_RUNNER_TASK = None


async def _gmgn_learning_loop() -> None:
    from plugins.gmgn_learning import get_state as get_gmgn_learning_state, run_cycle

    while True:
        state = get_gmgn_learning_state()
        if not state.get("enabled"):
            return
        try:
            await run_cycle(limit=int(state.get("deep_limit") or 2), chain=state.get("chain") or "bsc")
        except Exception as e:
            logger.error("GMGN learning runner failed: %s", e)
        state = get_gmgn_learning_state()
        await asyncio.sleep(max(300, int(state.get("cycle_interval") or 1800)))


def _ensure_gmgn_learning_runner() -> None:
    global _GMGN_LEARNING_TASK
    if _GMGN_LEARNING_TASK and not _GMGN_LEARNING_TASK.done():
        return
    try:
        _GMGN_LEARNING_TASK = asyncio.create_task(_gmgn_learning_loop(), name="web-gmgn-learning-runner")
    except RuntimeError:
        _GMGN_LEARNING_TASK = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("%s → http://localhost:%s", APP_NAME, get_settings().web_port)
    try:
        from plugins.gmgn_learning import get_state as get_gmgn_learning_state
        if get_gmgn_learning_state().get("enabled"):
            _ensure_gmgn_learning_runner()
    except Exception as e:
        logger.warning("GMGN learning resume skipped: %s", e)
    yield


def create_app() -> FastAPI:
    app = FastAPI(title=APP_NAME, lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/")
    async def root(request: Request):
        return templates.TemplateResponse(request, "index.html", {"app_name": APP_NAME})

    @app.get("/api/wallet")
    async def api_wallet():
        from plugins.portfolio_sync import get_live_bnb_balance
        async def fallback():
            bal = await _with_timeout(
                wallet_balance.get_balance(),
                4,
                {
                    "configured": False,
                    "message": "钱包余额查询超时，请稍后刷新",
                    "bnb": 0.0,
                    "usd": 0.0,
                    "bnb_fmt": "0.0000",
                    "usd_fmt": "$0.00",
                },
            )
            bal["source"] = bal.get("source", "quick_fallback")
            return bal

        data = await _with_timeout(get_live_bnb_balance(), 6, fallback)
        return {"ok": True, "data": data}

    @app.get("/api/token/{address}")
    async def api_token(address: str, ai: bool = Query(False)):
        try:
            ev = await memecoin_score.evaluate_token(address)
            result = {"ok": True, "data": ev, "card": memecoin_score.format_signal_card(ev)}
            if ai and agent.configured:
                result["ai_analysis"] = await agent.analyze_token(ev)
            return result
        except ValueError as e:
            raise HTTPException(400, str(e))
        except Exception as e:
            raise HTTPException(502, str(e))

    @app.get("/api/candidates")
    async def api_candidates():
        return {"ok": True, "data": get_candidates()}

    @app.get("/api/agents")
    async def api_agents():
        runtime = get_strategy_runtime()
        return {
            "ok": True,
            "data": [
                {
                    "id": a.id, "name": a.name, "ticker": a.ticker,
                    "strategy": a.strategy, "buy_threshold": a.buy_threshold,
                    "description": a.description, "weights": a.weights,
                    "effective_buy_threshold": runtime.get(a.strategy, {}).get("effective_buy_threshold", a.buy_threshold),
                    "strategy_weight": runtime.get(a.strategy, {}).get("weight", 1.0),
                    "strategy_enabled": runtime.get(a.strategy, {}).get("enabled", True),
                    "gmgn_boost": runtime.get(a.strategy, {}).get("gmgn_boost", 0),
                }
                for a in ARENA_AGENTS
            ],
        }

    @app.get("/api/strategy")
    async def api_strategy():
        state = get_state()
        validation = validate_runtime_rules()
        return {
            "ok": True,
            "data": {
                "name": APP_NAME,
                "focus": "four.meme 发射币（含已射出）",
                "take_profit": TAKE_PROFIT_TIERS,
                "stop_loss_pct": HARD_STOP_LOSS_PCT,
                "rules": state.get("next_criteria", []),
                "avoid": state.get("avoid_patterns", []),
                "runtime_ok": validation.get("ok", False),
                "runtime_errors": validation.get("errors", []),
            },
        }

    @app.get("/api/positions")
    async def api_positions():
        from plugins.portfolio_sync import sync_live_positions
        return {"ok": True, "data": await sync_live_positions()}

    @app.get("/api/portfolio")
    async def api_portfolio():
        """钱包 + 持仓 + Pact 一次拉取（前端实时轮询）"""
        from plugins.cobo_pact import get_pact_status
        from plugins.portfolio_sync import get_live_bnb_balance, sync_live_positions
        wallet, positions, pact = await asyncio.gather(
            _with_timeout(
                get_live_bnb_balance(),
                6,
                {
                    "configured": False,
                    "message": "钱包余额查询超时，请点刷新钱包余额",
                    "bnb": 0.0,
                    "bnb_fmt": "0.0000",
                },
            ),
            _with_timeout(sync_live_positions(), 10, _portfolio_fallback_positions),
            _with_timeout(get_pact_status(), 6, {"status": "unknown", "message": "Pact 查询超时"}),
        )
        return {
            "ok": True,
            "data": {
                "wallet": wallet,
                "positions": positions,
                "pact": pact,
                "ts": __import__("time").time(),
            },
        }

    @app.get("/api/evolution")
    async def api_evolution():
        return {"ok": True, "data": get_state()}

    @app.get("/api/journal")
    async def api_journal():
        return {
            "ok": True,
            "data": {
                "equity": trade_journal.get_equity(),
                "open": trade_journal.get_open_positions(),
                "today": trade_journal.get_today_trades(),
                "daybook": trade_journal.get_today_daybook(),
                "stats": trade_journal.strategy_stats(),
                "learning": trade_journal.get_learning(),
                "recent": trade_journal.get_recent_closed(10),
            },
        }

    @app.get("/api/signals")
    async def api_signals():
        from plugins.gmgn_scout import get_opportunities
        return {
            "ok": True,
            "data": trade_journal.get_signal_queue(),
            "opportunities": get_opportunities()[:20],
        }

    @app.get("/api/opportunities")
    async def api_opportunities():
        from plugins.gmgn_scout import get_opportunities
        return {"ok": True, "data": get_opportunities()}

    @app.get("/api/pact")
    async def api_pact():
        from plugins.cobo_pact import get_pact_status
        return {"ok": True, "data": await get_pact_status()}

    @app.get("/api/ai-trader")
    async def api_ai_trader():
        from plugins.ai_trader import get_state
        return {"ok": True, "data": get_state()}

    @app.post("/api/ai-control")
    async def api_ai_control(req: AiControlRequest):
        from plugins.ai_trader import set_enabled
        state = set_enabled(req.enabled, req.auto_trade)
        if req.enabled:
            _ensure_ai_runner()
        return {"ok": True, "data": state}

    @app.post("/api/ai-cycle")
    async def api_ai_cycle():
        from plugins.ai_trader import auto_trade_cycle, get_state
        from plugins.fourmeme_scan import scan_fourmeme
        from plugins.strategy_evolver import evolve_cycle
        if not get_state().get("enabled"):
            return {"ok": False, "message": "AI 机器人未启动", "data": get_state()}
        try:
            await scan_fourmeme()
            decision = await auto_trade_cycle(force=True)
            learning = await evolve_cycle()
            return {"ok": True, "decision": decision, "learning": learning, "data": get_state()}
        except Exception as e:
            raise HTTPException(502, str(e))

    @app.post("/api/ai-test-once")
    async def api_ai_test_once():
        from plugins.ai_trader import run_ai_test_once
        try:
            result = await run_ai_test_once()
            return {"ok": result.get("ok", False), **result}
        except Exception as e:
            raise HTTPException(502, str(e))

    @app.post("/api/scan-now")
    async def api_scan_now():
        from plugins.fourmeme_scan import scan_fourmeme
        found = await scan_fourmeme()
        return {"ok": True, "count": len(found), "data": get_recent_pools()}

    @app.get("/api/ai-feed")
    async def api_ai_feed(limit: int = Query(12, ge=1, le=30)):
        learning = trade_journal.get_learning()
        return {
            "ok": True,
            "data": {
                "thoughts": trade_journal.get_ai_thoughts(limit),
                "learning": learning,
                "insight": learning.get("last_insight", ""),
                "rules": learning.get("rules", [])[:5],
            },
        }

    @app.get("/api/learning")
    async def api_learning():
        from plugins.ai_trader import get_state as get_ai_state
        from plugins.gmgn_learning import get_state as get_gmgn_learning_state
        from plugins.mentor_wallet import get_state as get_mentor_state
        from plugins.strategy_evolver import get_state as get_strategy_state, validate_runtime_rules
        learning = trade_journal.get_learning()
        return {
            "ok": True,
            "data": {
                "ai": get_ai_state(),
                "learning": learning,
                "strategy": get_strategy_state(),
                "runtime": validate_runtime_rules(),
                "open": trade_journal.get_open_positions(),
                "recent_closed": trade_journal.get_recent_closed(8),
                "daybook": trade_journal.get_today_daybook(),
                "mentor": get_mentor_state(),
                "gmgn_learning": get_gmgn_learning_state(),
            },
        }

    @app.get("/api/mentor")
    async def api_mentor():
        from plugins.mentor_wallet import get_state as get_mentor_state
        return {"ok": True, "data": get_mentor_state()}

    @app.post("/api/mentor/deep-dive")
    async def api_mentor_deep_dive(req: MentorRequest):
        from plugins.mentor_wallet import deep_dive, get_state as get_mentor_state
        try:
            data = await deep_dive(req.wallet, req.chain)
            return {"ok": data.get("ok", False), "data": get_mentor_state()}
        except Exception as e:
            raise HTTPException(502, str(e))

    @app.get("/api/gmgn-learning")
    async def api_gmgn_learning():
        from plugins.gmgn_learning import get_state as get_gmgn_learning_state
        return {"ok": True, "data": get_gmgn_learning_state()}

    @app.post("/api/gmgn-learning/control")
    async def api_gmgn_learning_control(req: GmgnLearningControlRequest):
        from plugins.gmgn_learning import set_enabled
        state = set_enabled(req.enabled, req.limit, req.cycle_interval)
        if req.enabled:
            _ensure_gmgn_learning_runner()
        return {"ok": True, "data": state}

    @app.post("/api/gmgn-learning/run")
    async def api_gmgn_learning_run(req: GmgnLearningRunRequest):
        from plugins.gmgn_learning import run_cycle
        data = await run_cycle(limit=req.limit, chain=req.chain, force=req.force)
        return {"ok": True, "data": data}

    @app.post("/api/gmgn-learning/detect")
    async def api_gmgn_learning_detect(req: GmgnLearningRunRequest):
        from plugins.gmgn_learning import detect_only
        data = await detect_only(chain=req.chain)
        return {"ok": True, "data": data}

    @app.post("/api/gmgn-learning/deep-dive")
    async def api_gmgn_learning_deep_dive(req: MentorRequest):
        from plugins.gmgn_learning import deep_dive_candidate
        data = await deep_dive_candidate(req.wallet, req.chain)
        return {"ok": True, "data": data}

    @app.get("/api/gmgn")
    async def api_gmgn_status():
        intel = await gmgn_intel.get_intel()
        return {"ok": True, "data": {**gmgn_intel.snapshot_status(), "intel": intel}}

    @app.post("/api/gmgn/snapshot")
    async def api_gmgn_snapshot(req: GmgnSnapshotRequest):
        snap = gmgn_intel.ingest_skill_payload(req.model_dump(exclude_none=True))
        intel = await gmgn_intel.get_intel(force=True)
        return {"ok": True, "data": snap, "intel": intel}

    @app.get("/api/pools")
    async def api_pools():
        pools = get_recent_pools()
        if not pools:
            pools = get_history_pools(50)
        return {"ok": True, "data": pools}

    @app.post("/api/buy")
    async def api_buy(req: BuyRequest):
        try:
            result = await trading.buy_token(req.address, req.amount)
            ai_thought = None
            if result.get("success"):
                trade = await _record_submitted_buy(req.address, req.amount, result, "manual_buy")
                ai_thought = trade.get("open_reflection")
            return {"ok": result["success"], **result, "ai_thought": ai_thought}
        except ValueError as e:
            raise HTTPException(400, str(e))
        except Exception as e:
            raise HTTPException(502, str(e))

    @app.post("/api/force-buy")
    async def api_force_buy(req: BuyRequest):
        """Demo 强制测试买入：不先跑评分；仍由 trading.buy_token 校验 four.meme 并等待 Cobo App 确认。"""
        try:
            result = await trading.buy_token(req.address, req.amount)
            ai_thought = None
            if result.get("success"):
                trade = await _record_submitted_buy(req.address, req.amount, result, "force_test_buy")
                ai_thought = trade.get("open_reflection")
            return {"ok": result["success"], "force": True, **result, "ai_thought": ai_thought}
        except Exception as e:
            raise HTTPException(502, str(e))

    @app.post("/api/sell")
    async def api_sell(req: SellRequest):
        try:
            from plugins import account_notify
            result = await trading.sell_token(req.address, req.sell_pct)
            if result.get("success"):
                await account_notify.notify_close(req.address, req.sell_pct, result)
            if result.get("success") and result.get("action") == "sell":
                reduce_position(req.address, req.sell_pct)
            return {"ok": result["success"], **result}
        except Exception as e:
            raise HTTPException(502, str(e))

    @app.post("/api/test-trade")
    async def api_test_trade(req: TestTradeRequest):
        try:
            result = await trading.test_roundtrip(req.address, req.amount)
            return {"ok": result["success"], **result}
        except Exception as e:
            raise HTTPException(502, str(e))

    @app.get("/api/radar")
    async def api_radar(limit: int = Query(20, ge=1, le=50)):
        return {"ok": True, "data": await gmgn_intel.get_radar(limit)}

    @app.get("/api/settings")
    async def api_settings():
        from plugins import gmgn_intel
        from plugins.cobo_local import caw_available, load_from_caw
        s = get_settings()
        bal = await _with_timeout(
            wallet_balance.get_balance(),
            5,
            {"configured": False, "message": "钱包查询超时", "cobo_source": "timeout"},
        )
        caw = load_from_caw() if caw_available() else None
        return {
            "ok": True,
            "data": {
                "app_name": APP_NAME,
                "telegram_configured": s.telegram_configured,
                "deepseek_configured": s.deepseek_configured,
                "ai_provider": s.ai_label,
                "ai_model": s.ai_model,
                "cobo_configured": s.cobo_configured,
                "caw_available": caw_available(),
                "cobo_source": bal.get("cobo_source") or (caw or {}).get("source", "none"),
                "volume_threshold": s.volume_threshold,
                "scan_interval": s.scan_interval,
                "gmgn_configured": gmgn_intel.cli_ready(),
                "web_port": s.web_port,
                "wallet": bal,
                "scan": {
                    "interval": s.scan_interval,
                    "pool_count": len(get_recent_pools()),
                    "candidate_count": len(get_candidates()),
                    "mode": "four.meme API (NEW/PROGRESS/DEX) + GMGN supplement",
                },
            },
        }

    return app


async def run_web_server() -> None:
    import uvicorn
    settings = get_settings()
    config = uvicorn.Config(create_app(), host="0.0.0.0", port=settings.web_port, log_level="info")
    await uvicorn.Server(config).serve()
