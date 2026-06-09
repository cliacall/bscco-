const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8888";
const API_TIMEOUT_MS = 30000;

async function requestWithTimeout(input: RequestInfo | URL, init: RequestInit = {}) {
  const controller = new AbortController();
  let timedOut = false;
  const timeout = globalThis.setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, API_TIMEOUT_MS);
  try {
    return await fetch(input, { ...init, signal: controller.signal });
  } catch (e) {
    const message = e instanceof Error ? e.message : String(e);
    if (timedOut || message.toLowerCase().includes("aborted")) {
      throw new Error("请求超时或被浏览器中断，请刷新后重试；建议打开 http://localhost:8888 使用轻量 Demo。");
    }
    throw e;
  } finally {
    globalThis.clearTimeout(timeout);
  }
}

async function get<T>(path: string): Promise<T> {
  const res = await requestWithTimeout(`${API}${path}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`API ${path} failed: ${res.status}`);
  return res.json();
}

async function getOptional<T>(path: string, fallback: T): Promise<T> {
  try {
    return await get<T>(path);
  } catch {
    return fallback;
  }
}

async function post<T>(path: string, body: Record<string, unknown>): Promise<T> {
  const res = await requestWithTimeout(`${API}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await res.json();
  if (!res.ok) {
    const detail = typeof data.detail === "string" ? data.detail : data.message;
    if (res.status === 404) {
      throw new Error("接口不存在，请重启后端：./start.sh");
    }
    throw new Error(detail || `请求失败 (${res.status})`);
  }
  return data;
}

export interface PoolItem {
  symbol: string;
  address: string;
  token_address?: string;
  score?: number;
  tier_emoji?: string;
  signal?: string;
  stage?: string;
  launchpad?: string;
  smart_degen_count?: number;
  volume_fmt?: string;
  liquidity_fmt?: string;
  discovered_at?: string;
  created_timestamp?: number;
  progress?: number;
  holder_count?: number;
  source?: string;
  top_agent?: { ticker: string; strategy: string; score: number };
}

export interface PositionItem {
  symbol: string;
  address: string;
  entry_price: number;
  amount_bnb: number;
  token_amount?: number;
  current_gain_pct?: number;
  status: string;
  strategy?: string;
  pact_id?: string;
}

export interface PortfolioData {
  wallet: WalletBalance;
  positions: PositionItem[];
  pact: { status: string; pact_id?: string };
  ts: number;
}

export interface WalletBalance {
  configured: boolean;
  address?: string;
  address_fmt?: string;
  updated_at?: number;
  balance_updated_at?: string;
  bnb?: number;
  usd?: number;
  bnb_fmt?: string;
  usd_fmt?: string;
  message?: string;
  cobo_error?: string;
  error?: string;
  source?: string;
}

export interface AgentItem {
  id: string;
  name: string;
  ticker: string;
  strategy: string;
  buy_threshold: number;
  effective_buy_threshold?: number;
  strategy_weight?: number;
  strategy_enabled?: boolean;
  gmgn_boost?: number;
  description: string;
  weights: Record<string, number>;
}

export interface TokenEval {
  symbol: string;
  address: string;
  score: number;
  tier_emoji: string;
  tier_label: string;
  signal: string;
  price_fmt: string;
  agent_scores?: { agent_ticker: string; score: number; signal: string }[];
}

export interface RadarItem {
  symbol: string;
  address: string;
  amount_usd: number;
  wallet_count: number;
  strength: string;
  emoji: string;
  is_cluster: boolean;
  side: string;
  maker?: string;
}

export interface RadarData {
  available: boolean;
  updated_at?: number;
  items: RadarItem[];
  cluster_count: number;
  buy_count: number;
}

export interface AiThought {
  type: "open" | "close";
  symbol: string;
  address: string;
  ts: number;
  why?: string;
  risk?: string;
  confidence?: string;
  gmgn_align?: string;
  outcome?: string;
  root_cause?: string;
  lesson?: string;
  next_rule?: string;
  pnl?: number;
}

export interface AiFeedData {
  thoughts: AiThought[];
  insight: string;
  rules: string[];
  learning: { rules: string[]; avoid: string[]; last_insight?: string };
}

export interface TradeResult {
  ok: boolean;
  success: boolean;
  message: string;
  pact_id?: string;
  action?: string;
  ai_thought?: Record<string, string>;
  steps?: { step: string; ok: boolean; pact_id?: string; message: string }[];
}

export const api = {
  portfolio: () =>
    getOptional<{ ok: boolean; data: PortfolioData }>("/api/portfolio", {
      ok: false,
      data: {
        wallet: { configured: false, message: "未就绪" },
        positions: [],
        pact: { status: "unknown" },
        ts: 0,
      },
    }),
  wallet: () =>
    getOptional<{ ok: boolean; data: WalletBalance }>("/api/wallet", { ok: false, data: { configured: false, message: "钱包 API 未就绪，请重启后端" } }),
  radar: () =>
    getOptional<{ ok: boolean; data: RadarData }>("/api/radar", { ok: false, data: { available: false, items: [], cluster_count: 0, buy_count: 0 } }),
  candidates: () => get<{ ok: boolean; data: PoolItem[] }>("/api/candidates"),
  pools: () => get<{ ok: boolean; data: PoolItem[] }>("/api/pools"),
  positions: () => get<{ ok: boolean; data: PositionItem[] }>("/api/positions"),
  agents: () => get<{ ok: boolean; data: AgentItem[] }>("/api/agents"),
  strategy: () =>
    get<{
      ok: boolean;
      data: {
        name: string;
        focus: string;
        stop_loss_pct: number;
        rules?: string[];
        avoid?: string[];
        runtime_ok?: boolean;
        runtime_errors?: string[];
      };
    }>("/api/strategy"),
  settings: () =>
    get<{
      ok: boolean;
      data: {
        deepseek_configured: boolean;
        cobo_configured: boolean;
        scan_interval: number;
        wallet?: WalletBalance;
        scan?: { interval: number; pool_count: number; candidate_count: number; mode: string };
      };
    }>("/api/settings"),
  signals: () =>
    get<{
      ok: boolean;
      data: { symbol: string; score: number; address: string; strategy?: string; source?: string; stage?: string; reason?: string }[];
      opportunities?: { symbol: string; address: string; source: string; reason: string; score: number; stage: string }[];
    }>("/api/signals"),
  opportunities: () =>
    get<{ ok: boolean; data: { symbol: string; address: string; source: string; reason: string; score: number; stage: string }[] }>(
      "/api/opportunities"
    ),
  pact: () =>
    getOptional<{ ok: boolean; data: { status: string; pact_id?: string; message?: string } }>(
      "/api/pact",
      { ok: false, data: { status: "unknown" } }
    ),
  aiTrader: () =>
    getOptional<{ ok: boolean; data: { enabled: boolean; decisions: Record<string, unknown>[]; trades: Record<string, unknown>[]; open_positions: number } }>(
      "/api/ai-trader",
      { ok: false, data: { enabled: false, decisions: [], trades: [], open_positions: 0 } }
    ),
  aiFeed: () => getOptional<{ ok: boolean; data: AiFeedData }>("/api/ai-feed", { ok: false, data: { thoughts: [], insight: "", rules: [], learning: { rules: [], avoid: [] } } }),
  token: async (address: string) => {
    const res = await requestWithTimeout(`${API}/api/token/${address}`, { cache: "no-store" });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || `查币失败 ${res.status}`);
    return data as { ok: boolean; data: TokenEval };
  },
  buy: (address: string, amount: number) =>
    post<TradeResult>("/api/buy", { address, amount }),
  testTrade: (address: string, amount = 0.0001) =>
    post<TradeResult>("/api/test-trade", { address, amount }),
  sell: (address: string, sell_pct = 100) =>
    post<TradeResult>("/api/sell", { address, sell_pct }),
  aiTestOnce: () =>
    post<{
      ok: boolean;
      message: string;
      pool?: { symbol: string; address: string; stage: string; score?: number; progress?: number; source?: string };
      decision?: Record<string, unknown>;
      ai_configured?: boolean;
    }>("/api/ai-test-once", {}),
  scanNow: () => post<{ ok: boolean; count: number; data: PoolItem[] }>("/api/scan-now", {}),
};
