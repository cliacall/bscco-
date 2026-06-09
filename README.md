# BSCCO 机器人

昵称：**bscco**

BSCCO 机器人是一套面向 BNB Smart Chain 一级市场的 AI 交易 Demo。它把 four.meme 新创建检测、GMGN 聪明钱学习、ZAI/DeepSeek 决策、Cobo 手机确认交易和 Telegram 播报串成一个轻量 Python 控制台，让演示流程可以从“发现新币”一路走到“AI 学习画像”和“人工确认下单”。

> 风险提示：本项目用于 Demo、研究和比赛展示，不构成投资建议。真实交易前请使用小额资金、人工确认和独立风控。

## Demo 截图

第一张：主界面，展示余额、新创建候选、交易终端、Pact 状态和新币雷达。

![主界面](zai/docs/screenshots/1.png)

第二张：启动 AI 机器人与强制测试决策，展示自动决策、手动触发和最近动作。

![启动功能](zai/docs/screenshots/2.png)

第三张：AI 学习板块，展示规则学习、回避规则和交易复盘沉淀。

![学习板块](zai/docs/screenshots/3.png)

第四张：GMGN 聪明钱自动学习，支持填写批量学习数量，从排行、内盘、聪明钱候选里批量深挖。

![GMGN 自动学习](zai/docs/screenshots/4.png)

第五张：深挖聪明钱钱包，把单个候选钱包转成可读画像和交易风格。

![聪明钱深挖](zai/docs/screenshots/5.png)

第六张：学习数据转化，把钱包行为拆成入场、出场、风控、仓位和回避规则。

![学习数据转化](zai/docs/screenshots/6.png)

## 核心能力

- **一级市场雷达**：扫描 BSC four.meme 新创建、即将毕业和已射出候选。
- **GMGN 聪明钱学习**：从 GMGN 排行、内盘、smart money / KOL 流里提取候选钱包，批量深挖并写入本地画像记忆。
- **AI 决策一次**：提供测试按钮，能强制 AI 对当前候选做一次 buy/skip/sell 决策，便于现场演示。
- **自动开单但不裸奔**：AI 可以发起交易意图，但 Cobo Pact 仍需要手机 App 人工同意，降低私钥外泄和无人值守误操作风险。
- **余额驱动仓位**：默认单笔金额由当前 BNB 余额、预留余额、最大持仓数和上下限共同计算。
- **持仓和平仓**：持仓区支持手动卖出/清仓，成交后同步本地持仓和余额。
- **学习画像图表**：把导师钱包或 GMGN 候选钱包转成动物系棱形画像、分级、胜率、收益、节奏和风险标签。
- **TG 播报**：可通过 Telegram 推送开单、平仓、信号、余额和日终复盘。

## 为什么有优势

1. **演示链路完整**：打开网页即可看到发现、评分、学习、决策、交易、持仓、复盘，不需要临场解释一堆脚本。
2. **AI 不直接接触私钥**：交易由 Cobo Agentic Wallet / Pact 执行，手机确认是最后一道门。
3. **学习不是装饰**：GMGN 候选钱包可以直接深挖，学习结果会写入本地规则和画像，影响后续 AI 判断。
4. **适合比赛切换**：默认接入 ZAI，同时保留 DeepSeek 配置，`AI_PROVIDER` 一改即可切换。
5. **轻量 Python 后端**：核心 Demo 用 FastAPI 单页运行，避免笨重前端链路影响提交展示。
6. **隐私可控**：`.env`、运行数据、钱包状态、持仓、日志默认不提交，只提交代码、示例配置和截图。

## 需要什么

- Python 3.10+
- BNB Smart Chain 钱包或 Cobo Agentic Wallet
- Cobo App，用于 Pact 授权和每笔交易手机确认
- `gmgn-cli` 与 `GMGN_API_KEY`，用于拉取 GMGN 市场和聪明钱数据
- ZAI API Key，默认用于 AI 决策和画像提炼
- DeepSeek API Key，可选，用于另一套比赛或备用模型
- Telegram Bot，可选，用于开单/平仓/信号播报
- BSC RPC，可用默认公开 RPC，也可以换成自己的稳定 RPC

## 快速启动

```bash
cd /Users/seche/Desktop/vscode/zai
cp .env.example .env
```

在 `.env` 里填写需要的 key 后启动 Demo 面板：

```bash
./start.sh
```

打开：

```text
http://localhost:8888
```

默认 `./start.sh` 只启动最快的 Web/API Demo。要启动完整机器人循环：

```bash
FULL_BOT=1 AI_AUTO_TRADE=true ./start.sh
```

## AI Provider

默认走 ZAI：

```env
AI_PROVIDER=zai
ZAI_API_KEY=your_zai_api_key
ZAI_BASE_URL=https://api.z.ai/api/paas/v4/
ZAI_MODEL=glm-5.1
```

切 DeepSeek：

```env
AI_PROVIDER=deepseek
DEEPSEEK_API_KEY=your_deepseek_api_key
DEEPSEEK_MODEL=deepseek-chat
```

## 关键环境变量

```env
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
TELEGRAM_NOTIFY_CHAT_ID=

AI_PROVIDER=zai
ZAI_API_KEY=
DEEPSEEK_API_KEY=

COBO_API_KEY=
COBO_WALLET_ID=
BSC_RPC_URL=https://bsc-dataseed.binance.org
WALLET_ADDRESS=

GMGN_API_KEY=
GMGN_PULL_INTERVAL=300

AI_AUTO_TRADE=true
AI_MAX_POSITIONS=3
AI_RESERVE_BNB=0.002
AI_TRADE_BALANCE_PCT=0.25
AI_MIN_BUY_BNB=0.001
AI_MAX_BUY_BNB=0.05
```

## 安全说明

- 不要把 `.env`、私钥、API key、钱包 ID、真实持仓数据上传到 GitHub。
- `.gitignore` 已忽略 `.env`、`.env.*`、`data/*.json`、日志、缓存和前端构建产物。
- Cobo Pact 用 scoped 授权，交易需要手机 App 人工确认。
- AI 自动交易适合小额 Demo，真实运行请设置余额预留、单笔上限和最大持仓。
- 如果 token 或密钥曾经发到聊天、群或截图里，请立即 revoke/rotate。

## 结构

```text
main.py                完整机器人入口
start.sh               Demo 快速启动脚本
web/server.py          FastAPI API 与页面服务
web/templates/index.html
plugins/ai_trader.py   AI 决策和余额驱动仓位
plugins/gmgn_learning.py
plugins/mentor_wallet.py
plugins/trading.py     Cobo / four.meme 买卖执行
plugins/wallet_balance.py
data/*.example.json    可提交示例数据；真实运行数据默认忽略
```
