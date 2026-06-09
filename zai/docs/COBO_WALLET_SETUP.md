# Cobo Agentic Wallet 配置教程

从零创建钱包、配对手机 App、填入 `.env`，到跑通本项目的 `/buy` 买入功能。

> **核心结论：你不需要、也不应该把私钥交给 Agent。**  
> Agent 只拿 `COBO_API_KEY`，私钥由 Cobo MPC 托管，你在手机 App 里审批每一笔授权。

---

## 一、你需要准备什么

| 需要 | 不需要 |
|------|--------|
| 一台电脑（跑 Agent） | ❌ 私钥 |
| 一部手机（装 Cobo App，做 Owner 审批） | ❌ MetaMask 助记词导入到代码 |
| `COBO_API_KEY` + `COBO_WALLET_ID` | ❌ 把钱包密码写进 `.env` |
| 钱包里有一点 BNB（BSC 主网，用于真实买入测试） | |

### 本项目用到的链

本项目买入走 **BSC 主网**（Cobo chain ID = `BSC_BNB`），调用 PancakeSwap Router 做 swap。

> ⚠️ Cobo 目前**没有 BSC 测试网**，只有 BSC 主网。Demo 请用**极小金额**（如 0.001 BNB）测试，不要用主钱包大额资金。

---

## 二、整体流程

```
安装 caw CLI
    ↓
创建钱包 (caw onboard)
    ↓
手机 App 配对 (caw wallet pair)
    ↓
获取 API Key + Wallet ID
    ↓
创建 BSC 地址 + 充值少量 BNB
    ↓
填入 .env → 启动 Agent
    ↓
/buy 提交 Pact → App 批准 → 链上成交
```

---

## 三、Step 1 — 安装 caw CLI

```bash
# 安装 Cobo Agentic Wallet 命令行工具
curl -fsSL https://raw.githubusercontent.com/CoboGlobal/cobo-agentic-wallet/master/install.sh | bash

# 加入 PATH（建议写入 ~/.zshrc 或 ~/.bashrc）
export PATH="$HOME/.cobo-agentic-wallet/bin:$PATH"

# 验证
caw --version
```

官方文档：[CLI 安装指南](https://www.cobo.com/products/agentic-wallet/manual/developer/cli.md)

---

## 四、Step 2 — 创建钱包

```bash
caw onboard --wait
```

向导会引导你完成钱包创建，直到 `status` 变为 `active`。

此时 Agent 是钱包 Owner，**还没有任何限制**——适合本地调试，但参赛 Demo 建议继续完成配对。

---

## 五、Step 3 — 手机 App 配对（重要）

配对后，**你**成为钱包主人，Agent 只能在 Pact 授权范围内操作。

### 5.1 下载 App

- [App Store](https://apps.apple.com/app/cobo-agentic-wallet)（iOS）
- [Google Play](https://play.google.com/store/apps/details?id=com.cobo.agenticwallet)（Android）

注册 / 登录 Cobo Agentic Wallet App。

### 5.2 生成配对码

```bash
caw wallet pair --code-only
```

终端会输出 `CAW-XXXXX` 格式的 8 位配对码（30 分钟内有效）。

### 5.3 在 App 里输入配对码

1. 打开 Cobo Agentic Wallet App
2. 按提示输入配对码 `CAW-XXXXX`
3. 确认钱包信息 → 点 **Confirm**
4. 等待约 30 秒完成 MPC 密钥分片

### 5.4 检查配对状态

```bash
caw wallet pair-status
```

显示已配对即成功。

### 配对前后对比

| | 配对前 | 配对后 |
|---|--------|--------|
| 主人 | Agent | **你（手机 App）** |
| 花钱方式 | 无限制 | 必须提交 **Pact**，你审批 |
| 超额交易 | 直接执行 | App 弹窗等你点批准 |
| 紧急停止 | 无 | App 里可**冻结 Agent** |

官方文档：[Pair your agent](https://www.cobo.com/products/agentic-wallet/manual/owners/connect-agent-human-app.md)

---

## 六、Step 4 — 获取凭证，填入 .env

```bash
caw wallet current --show-api-key
```

记下输出中的三个值：

| 输出字段 | 对应 .env 变量 |
|----------|----------------|
| `api_key` | `COBO_API_KEY` |
| `wallet_uuid` | `COBO_WALLET_ID` |
| `api_url` | 默认 `https://api.agenticwallet.cobo.com`（一般不用改） |

编辑项目根目录 `.env`：

```env
COBO_API_KEY=你的_api_key
COBO_WALLET_ID=你的_wallet_uuid
BSC_RPC_URL=https://bsc-dataseed.binance.org
```

> **再次强调：`.env` 里只有 API Key，没有私钥。**

---

## 七、Step 5 — 创建 BSC 地址并充值

### 7.1 查看 / 创建 BSC 地址

```bash
# 列出已有地址
caw address list

# 如需新建 BSC 地址
caw address create --chain-id BSC_BNB
```

记下 BSC 地址（`0x` 开头），例如 `0xAbC...123`。

### 7.2 充值 BNB

从任意交易所或你的其他钱包，向上述 BSC 地址转入**少量 BNB**：

- Gas 费：约 0.0003–0.001 BNB / 笔
- 测试买入：建议准备 **0.01–0.05 BNB**

```bash
# 查看余额
caw wallet balance
```

### 7.3 先用 Sepolia 练手（可选）

如果还没准备好 BSC 主网资金，可以先用 Sepolia 测试网熟悉 Pact 流程：

```bash
caw address list                        # 拿 Sepolia 地址
caw faucet deposit --token-id SETH --address <你的-seth-地址>
```

但本项目的 `/buy` 命令走的是 BSC + PancakeSwap，**最终 Demo 必须在 BSC 主网跑通**。

---

## 八、Step 6 — 启动 Agent 并测试买入

### 8.1 启动项目

```bash
cd /path/to/zai
source .venv/bin/activate
python main.py
```

### 8.2 Telegram 测试

在 Telegram 对你的 Bot 发送：

```
/buy 0x代币合约地址 0.001
```

示例（买入 CAKE，金额 0.001 BNB）：

```
/buy 0x0E09FaBB73Bd3Ade0a17ECC321fD13a19e81cE82 0.001
```

### 8.3 Web 仪表盘测试

1. 浏览器打开 http://localhost:8888
2. 左侧点 **⚙️ 设置**
3. 填入合约地址和 BNB 数量
4. 点 **提交买入**

### 8.4 在 App 里批准 Pact

Agent 提交买入后，你的手机会收到通知：

1. 打开 Cobo Agentic Wallet App
2. 查看待审批的 **Pact**（意图类似 `Buy 0.001 BNB worth of token 0x... on PancakeSwap`）
3. 检查金额、合约地址是否合理
4. 点 **Approve（批准）**

批准后，Cobo MPC 签名并广播交易。Bot 会返回 Pact ID，可在 App 的交易记录里查看 **Transaction Hash**。

### 8.5 本项目买入时发生了什么

```
用户 /buy 0xCA... 0.001
        ↓
trading.py 构造 PancakeSwap swapExactETHForTokens calldata
        ↓
client.submit_pact() 提交买入意图
        ↓
Cobo 检查 Policy → 等你 App 批准（或自动通过）
        ↓
MPC 签名 → BSC 链上成交
        ↓
返回 Pact ID + 交易 hash
```

对应代码：`plugins/trading.py`

---

## 九、常见问题

### Q1：需要把私钥导入项目吗？

**不需要。** Agent 通过 API Key 调用 Cobo，签名由 MPC 完成。把私钥写进代码是错误做法，也有安全风险。

### Q2：配对前和配对后有什么区别？

- **配对前**：Agent 是 Owner，花钱无限制，适合本地调试
- **配对后**：你是 Owner，每笔任务需 Pact 授权，适合参赛 Demo

### Q3：提交 Pact 后没反应？

1. 检查手机 App 是否有待审批通知
2. 运行 `caw wallet pair-status` 确认已配对
3. 检查钱包 BNB 余额是否足够（金额 + gas）
4. 查看 `COBO_API_KEY` / `COBO_WALLET_ID` 是否正确

### Q4：交易失败常见原因

| 错误 | 原因 | 处理 |
|------|------|------|
| `401` | API Key 无效 | 重新 `caw wallet current --show-api-key` |
| `403 INSUFFICIENT_PERMISSION` | 钱包已配对但无活跃 Pact | 打开 Cobo App → 批准「bscco-trading」交易授权 |
| `钱包 ID 无效` | WALLET_ID 填错 | 核对 `wallet_uuid` |
| `未找到 BSC 地址` | 没创建 BSC 地址 | `caw address create --chain-id BSC_BNB` |
| Pact 被拒绝 | App 里点了 Reject | 重新发起 `/buy` |
| 余额不足 | BNB 不够 | 充值后再试 |

### Q5：如何立即停止 Agent？

在 Cobo Agentic Wallet App 里 **Freeze（冻结）** 钱包，立即生效，无需等待。

### Q6：参赛 Demo 要展示什么？

Cobo 赛道评审重点：

1. **CAW 是关键组件** — 资金操作必须经过 Cobo，不是摆设
2. **完整资金流程** — 任务触发 → 提交 Pact → 你批准 → 链上成交
3. **可演示性** — Demo 视频里清楚展示 App 批准过程
4. **风险边界** — README 说明金额上限、测试资金、人工审批条件

建议在 Demo 视频里录制：

- Agent 扫描到新池子 → 分析代币
- `/buy` 提交买入
- 手机 App 批准 Pact
- BscScan 上的 Transaction Hash

---

## 十、参赛提交清单

提交 Hackathon 时，README 或 Proposal 建议包含：

- [ ] GitHub Repo 链接
- [ ] `COBO_API_KEY` 使用位置说明（`plugins/trading.py`）
- [ ] Pact 提交 → App 批准 → 链上执行 的流程截图
- [ ] BSC 钱包地址
- [ ] 至少一笔测试交易的 Transaction Hash
- [ ] 3–5 分钟 Demo 视频
- [ ] 安全边界说明（测试金额、审批机制、冻结方式）

---

## 参考链接

- [Cobo Agentic Wallet 介绍](https://www.cobo.com/products/agentic-wallet/manual/start-here/introduction)
- [什么是 Agentic Wallet（为何不用私钥）](https://www.cobo.com/products/agentic-wallet/manual/learn/what-is-agentic-wallet)
- [Developer Quickstart](https://www.cobo.com/products/agentic-wallet/manual/developer/quickstart-overview)
- [Python SDK 文档](https://www.cobo.com/products/agentic-wallet/manual/developer/api-client-python.md)
- [支持的链列表](https://www.cobo.com/products/agentic-wallet/manual/reference/supported-chains.md)
- [Hackathon 报名页](https://casualhackathon.com/hackathons/ai-web3-agentic-builders-hackathon)
