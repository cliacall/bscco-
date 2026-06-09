"""ZAI / DeepSeek — bscco机器人 交易反思 + 聪明钱自循环"""

import json
import re
from typing import Any

from openai import AsyncOpenAI

from config.settings import get_settings
from config.strategy import APP_NAME

SYSTEM_PROMPT = f"""你是 {APP_NAME}，专注 BSC four.meme 发射的 meme 币。
可交易内盘 bonding curve 和已射出（毕业）的 four.meme 币；不碰纯 Pancake 土狗。
交易需 Cobo App 人工批准。中文简洁输出。"""

REFLECT_OPEN = """分析开单。结合聪明钱快照+历史教训。four.meme 发射币（含已射出）。
返回 JSON：{"why":"","gmgn_align":"","risk":"","confidence":"high|medium|low"}"""

REFLECT_CLOSE = """复盘平仓。提炼下次规则。
返回 JSON：{"outcome":"win|loss|breakeven","root_cause":"","worked":"","failed":"","next_rule":"","lesson":""}"""

EVOLVE_PROMPT = """策略进化。聪明钱快照+four.meme交易复盘。拒绝纯Pancake土狗。
返回 JSON：{"insights":"","adjustments":[{"strategy":"","weight_delta":0.1,"reason":""}],"next_criteria":[],"avoid":[],"disable":[]}"""


class TradingAgent:
    def __init__(self) -> None:
        settings = get_settings()
        self._provider = settings.ai_label
        self._configured = settings.ai_configured
        self._client = (
            AsyncOpenAI(api_key=settings.ai_api_key, base_url=settings.ai_base_url)
            if self._configured else None
        )
        self._model = settings.ai_model

    @property
    def configured(self) -> bool:
        return self._configured

    @property
    def provider(self) -> str:
        return self._provider

    async def analyze(self, prompt: str, context: str = "", max_tokens: int = 800) -> str:
        if not self._configured or not self._client:
            return f"⚠️ 未配置 {self._provider}_API_KEY"
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        if context:
            messages.append({"role": "user", "content": f"背景:\n{context}"})
        messages.append({"role": "user", "content": prompt})
        try:
            resp = await self._client.chat.completions.create(
                model=self._model, messages=messages, max_tokens=max_tokens, temperature=0.5,
            )
            return resp.choices[0].message.content or ""
        except Exception as e:
            return f"❌ AI 失败: {e}"

    async def _structured(self, prompt: str, context: str, fallback: dict) -> dict:
        raw = await self.analyze(prompt, context, max_tokens=600)
        if raw.startswith("❌") or raw.startswith("⚠️"):
            return {**fallback, "_error": raw}
        parsed = _parse_json(raw)
        return parsed if parsed else {**fallback, "_raw": raw[:300]}

    async def reflect_open(self, trade: dict, gmgn_ctx: str, history: str) -> dict:
        ctx = f"开单 {trade.get('symbol')} 策略{trade.get('strategy')}\n聪明钱:\n{gmgn_ctx}\n{history}"
        return await self._structured(REFLECT_OPEN, ctx, {"why": trade.get("strategy", ""), "confidence": "medium"})

    async def reflect_close(self, trade: dict, gmgn_ctx: str, history: str) -> dict:
        pnl = trade.get("pnl", 0)
        ctx = f"平仓 {trade['symbol']} pnl{pnl:+.2f}\n聪明钱:\n{gmgn_ctx}\n{history}"
        return await self._structured(REFLECT_CLOSE, ctx, {
            "outcome": "win" if pnl > 0 else "loss",
            "lesson": f"{trade['symbol']} {pnl:+.2f}",
            "next_rule": "只做 four.meme 发射币",
        })

    async def evolve(self, gmgn_summary: str, trades_summary: str, state_summary: str) -> dict:
        ctx = f"聪明钱:\n{gmgn_summary}\n交易:\n{trades_summary}\n策略:\n{state_summary}"
        return await self._structured(EVOLVE_PROMPT, ctx, {"insights": "", "adjustments": [], "next_criteria": [], "avoid": []})

    async def analyze_token(self, token_data: dict) -> str:
        ctx = (
            f"{token_data.get('symbol')} four.meme一级\n"
            f"分{token_data.get('score')} 价{token_data.get('price_fmt')}\n"
            f"`{token_data.get('address')}`"
        )
        return await self.analyze("简短分析这个一级 meme 币风险和机会。", ctx)


def _parse_json(text: str) -> dict[str, Any] | None:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{[\s\S]*\}", text)
    if m:
        try:
            return json.loads(m.group())
        except json.JSONDecodeError:
            return None
    return None
