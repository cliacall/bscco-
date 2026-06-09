"use client";

import { useCallback, useMemo, useState } from "react";
import { useDashboardData } from "@/hooks/use-dashboard-data";
import { api, type PoolItem, type TokenEval } from "@/lib/api";

type Rail = "home" | "meme" | "signal" | "ai" | "trade";
type MainTab = "trenches" | "trade" | "positions" | "signals" | "ai";
type Stage = "new_creation" | "near_completion" | "graduated";

const STAGE: Record<Stage, string> = {
  new_creation: "新创建",
  near_completion: "即将毕业",
  graduated: "已射出",
};

const RAIL: { id: Rail; icon: string; label: string }[] = [
  { id: "home", icon: "⌂", label: "首页" },
  { id: "meme", icon: "◆", label: "新币" },
  { id: "signal", icon: "◎", label: "信号" },
  { id: "ai", icon: "✦", label: "智能" },
  { id: "trade", icon: "⇄", label: "交易" },
];

function fmtAddr(a: string) {
  return a.length > 10 ? `${a.slice(0, 6)}…${a.slice(-4)}` : a;
}

function pctLabel(n: number) {
  return n === 100 ? "清仓" : `卖 ${n}%`;
}

export function Dashboard() {
  const {
    wallet, pools, candidates, positions, signals, radar, radarOk,
    agents, aiThoughts, aiInsight, aiTrader, pactStatus, lastSync, settings, ready,
    refreshPortfolio, refreshPools, refreshAi, setFastPoll,
  } = useDashboardData();

  const [rail, setRail] = useState<Rail>("home");
  const [stage, setStage] = useState<Stage>("new_creation");
  const [tab, setTab] = useState<MainTab>("trenches");
  const [treeOpen, setTreeOpen] = useState(true);
  const [search, setSearch] = useState("");
  const [ca, setCa] = useState("");
  const [buyAmount, setBuyAmount] = useState("0.05");
  const [tradeMsg, setTradeMsg] = useState("");
  const [tradeLoading, setTradeLoading] = useState(false);
  const [aiTestMsg, setAiTestMsg] = useState("");
  const [aiTestLoading, setAiTestLoading] = useState(false);
  const [tokenEv, setTokenEv] = useState<TokenEval | null>(null);

  const scanSec = String(settings?.scan?.interval ?? settings?.scan_interval ?? 10);

  const poolsByStage = useMemo(() => {
    const m: Record<Stage, PoolItem[]> = { new_creation: [], near_completion: [], graduated: [] };
    for (const p of pools) {
      const s = (p.stage ?? "new_creation") as Stage;
      if (m[s]) m[s].push(p);
    }
    for (const k of Object.keys(m) as Stage[]) {
      m[k].sort((a, b) => (b.created_timestamp ?? 0) - (a.created_timestamp ?? 0) || (b.score ?? 0) - (a.score ?? 0));
    }
    return m;
  }, [pools]);

  const stagePools = useMemo(() => {
    const list = poolsByStage[stage] ?? [];
    if (!search.trim()) return list;
    const q = search.toLowerCase();
    return list.filter((p) => p.symbol.toLowerCase().includes(q) || p.address.toLowerCase().includes(q));
  }, [poolsByStage, stage, search]);

  const pick = useCallback((a: string) => setCa(a), []);

  const doBuy = async (address: string, amount?: number) => {
    const a = address.trim();
    if (!a.startsWith("0x")) { setTradeMsg("无效合约地址"); return; }
    setTradeLoading(true);
    setTradeMsg("提交买入…");
    try {
      const r = await api.buy(a, amount ?? (parseFloat(buyAmount) || 0.05));
      setTradeMsg(r.message || (r.success ? "买入已提交" : "失败"));
      setCa(a);
      setFastPoll(true);
      setTimeout(() => setFastPoll(false), 90000);
      refreshPortfolio();
      refreshPools();
    } catch (e) {
      setTradeMsg(e instanceof Error ? e.message : "买入失败");
    } finally {
      setTradeLoading(false);
    }
  };

  const doSell = async (address: string, pct: number) => {
    setTradeLoading(true);
    setTradeMsg(`卖出 ${pct}%…`);
    try {
      const r = await api.sell(address, pct);
      setTradeMsg(r.message || (r.success ? "卖出已提交" : "失败"));
      setFastPoll(true);
      setTimeout(() => setFastPoll(false), 90000);
      refreshPortfolio();
    } catch (e) {
      setTradeMsg(e instanceof Error ? e.message : "卖出失败");
    } finally {
      setTradeLoading(false);
    }
  };

  const lookup = async () => {
    if (!ca.startsWith("0x")) return;
    try {
      const r = await api.token(ca);
      setTokenEv(r.data);
    } catch (e) {
      setTradeMsg(e instanceof Error ? e.message : "查币失败");
    }
  };

  const runAiTest = async () => {
    setAiTestLoading(true);
    setAiTestMsg("爬取 four.meme 新币 + AI 分析中…");
    try {
      const r = await api.aiTestOnce();
      const d = r.decision as { action?: string; reason?: string; confidence?: string } | undefined;
      setAiTestMsg(
        r.message +
          (d?.reason ? `\n理由: ${d.reason}` : "") +
          (d?.confidence ? `\n置信度: ${d.confidence}` : "")
      );
      refreshAi();
      refreshPools();
      if (r.pool?.address) setCa(r.pool.address);
    } catch (e) {
      setAiTestMsg(e instanceof Error ? e.message : "智能测试失败");
    } finally {
      setAiTestLoading(false);
    }
  };

  const onRail = (id: Rail) => {
    setRail(id);
    if (id === "meme") { setTab("trenches"); setStage("new_creation"); }
    if (id === "signal") setTab("signals");
    if (id === "ai") setTab("ai");
    if (id === "trade") setTab("positions");
    if (id === "home") setTab("trenches");
  };

  if (!ready) {
    return (
      <div className="g-shell flex items-center justify-center">
        <p className="text-[#ffb87a]">加载中…</p>
      </div>
    );
  }

  const poolTotal = pools.length;
  const gainPos = positions.filter((p) => (p.current_gain_pct ?? 0) > 0).length;
  const newCount = poolsByStage.new_creation?.length ?? 0;

  return (
    <div className="g-shell">
      <div className="g-window">
        <div className="g-rail">
          <div className="flex flex-col items-center gap-2">
            {RAIL.map((r) => (
              <button key={r.id} type="button" className={`g-rail-btn${rail === r.id ? " active" : ""}`} title={r.label} onClick={() => onRail(r.id)}>
                {r.icon}
              </button>
            ))}
          </div>
          <span className="g-rail-label">BSCCO 2026</span>
          <button type="button" className="g-rail-btn" title="设置">⚙</button>
        </div>

        <nav className="g-nav">
          <div className="g-nav-profile">
            <div className="flex items-center gap-3">
              <div className="flex h-10 w-10 items-center justify-center rounded-full text-sm font-bold text-[#1a0800]" style={{ background: "linear-gradient(135deg,#ff9a40,#ff6020)" }}>B</div>
              <div className="min-w-0">
                <p className="truncate text-[13px] font-semibold text-white">bscco</p>
                <p className="g-mono truncate text-[rgba(255,180,120,0.45)]">{wallet?.address_fmt || (wallet?.address ? fmtAddr(wallet.address) : "未配置")}</p>
              </div>
            </div>
            {wallet?.configured && (
              <p className="mt-3 text-[15px] font-bold text-white">{wallet.bnb_fmt}<span className="ml-1 text-[11px] font-normal text-[rgba(255,180,120,0.5)]">BNB</span></p>
            )}
          </div>

          <div className="g-nav-section">
            <p className="g-nav-section-title">项目</p>
            {[
              { id: "home" as Rail, label: "总览", icon: "▣" },
              { id: "meme" as Rail, label: "four.meme", icon: "◈" },
              { id: "trade" as Rail, label: "持仓", icon: "◇", badge: positions.length || undefined },
            ].map((item) => (
              <button key={item.id} type="button" className={`g-nav-item${rail === item.id ? " active" : ""}`} onClick={() => onRail(item.id)}>
                <span>{item.icon}</span>{item.label}
                {item.badge != null && item.badge > 0 && <span className="g-nav-badge">{item.badge}</span>}
              </button>
            ))}
          </div>

          <div className="g-nav-section">
            <p className="g-nav-section-title">状态</p>
            {(Object.keys(STAGE) as Stage[]).map((s) => (
              <button key={s} type="button" className={`g-nav-item${stage === s && tab === "trenches" ? " active" : ""}`} onClick={() => { setStage(s); setTab("trenches"); setRail("meme"); }}>
                <span>○</span>{STAGE[s]}<span className="g-nav-badge">{poolsByStage[s]?.length ?? 0}</span>
              </button>
            ))}
            <button type="button" className={`g-nav-item${tab === "signals" ? " active" : ""}`} onClick={() => { setTab("signals"); setRail("signal"); }}>
              <span>◎</span>信号<span className="g-nav-badge">{signals.length}</span>
            </button>
          </div>

          <div className="g-nav-tree">
            <p className="g-nav-section-title px-2">代币</p>
            <div className="g-tree-search-wrap">
              <input className="g-tree-search" placeholder="搜索…" value={search} onChange={(e) => setSearch(e.target.value)} />
            </div>
            <button type="button" className={`g-tree-folder${treeOpen ? " open" : ""}`} onClick={() => setTreeOpen((o) => !o)}>
              <span>{treeOpen ? "▾" : "▸"}</span>{STAGE[stage]}<span className="ml-auto text-[10px] opacity-50">{stagePools.length}</span>
            </button>
            {treeOpen && stagePools.slice(0, 20).map((p) => (
              <button key={p.address} type="button" className={`g-tree-file${ca === p.address ? " active" : ""}`} onClick={() => pick(p.address)}>
                <span className="truncate">{p.symbol}</span>
                {p.score != null && <span className="shrink-0 text-[#ffb87a]">{p.score}</span>}
              </button>
            ))}
          </div>
        </nav>

        <main className="g-main">
          <div className="g-main-head">
            <h1 className="g-main-title">总览</h1>
            <p className="g-main-sub">
              four.meme 自主爬取新币 · 扫描 {scanSec}s
              {lastSync > 0 && ` · ${new Date(lastSync * 1000).toLocaleTimeString()}`}
            </p>
          </div>

          <div className="g-stat-row">
            <div className="g-stat-card">
              <p className="g-stat-label">新创建</p>
              <p className="g-stat-value">{newCount}</p>
              <span className="g-stat-trend">four.meme API · 共 {poolTotal} 个</span>
            </div>
            <div className="g-stat-card">
              <p className="g-stat-label">持仓</p>
              <p className="g-stat-value">{positions.length}</p>
              <span className="g-stat-trend">{gainPos > 0 ? `+${gainPos} 盈利` : "—"}</span>
            </div>
            <div className="g-stat-card">
              <p className="g-stat-label">钱包余额</p>
              <p className="g-stat-value text-[22px]">{wallet?.bnb_fmt ? `${wallet.bnb_fmt} BNB` : wallet?.bnb != null ? `${wallet.bnb} BNB` : "—"}</p>
              <span className="g-stat-trend warn">
                Pact {pactStatus === "active" ? "✓" : pactStatus === "pending_approval" ? "待批准" : "—"} · 智能 {settings?.deepseek_configured ? "✓" : "—"}
              </span>
            </div>
          </div>

          <div className="g-tabs">
            {([
              ["trenches", "新币"],
              ["trade", "交易"],
              ["positions", "持仓"],
              ["signals", "信号"],
              ["ai", "智能"],
            ] as [MainTab, string][]).map(([id, label]) => (
              <button key={id} type="button" className={`g-tab${tab === id ? " active" : ""}`} onClick={() => setTab(id)}>{label}</button>
            ))}
          </div>

          <div className="g-main-body">
            {tab === "trenches" && (
              <div className="g-token-grid">
                {stagePools.length ? stagePools.map((p) => (
                  <div key={p.address} className={`g-token-card${ca === p.address ? " active" : ""}`} onClick={() => pick(p.address)} role="button" tabIndex={0}>
                    <div className="flex items-center justify-between">
                      <span className="font-semibold text-white">{p.symbol}</span>
                      {p.score != null && <span className="text-[13px] font-bold text-[#ffb87a]">{p.score}</span>}
                    </div>
                    <p className="g-mono mt-1 text-[rgba(255,180,120,0.4)]">{fmtAddr(p.address)}</p>
                    <p className="mt-2 text-[11px] text-[rgba(255,200,150,0.5)]">
                      量 {p.volume_fmt || "—"} · 持有人 {p.holder_count ?? "—"}
                      {p.progress != null && ` · ${Math.round(p.progress * 100)}%`}
                    </p>
                    {(p as PoolItem & { source?: string }).source === "fourmeme_api" && stage === "new_creation" && (
                      <span className="mt-1 inline-block rounded px-1.5 py-0.5 text-[9px] text-[#ffb87a] bg-[rgba(255,120,40,0.15)]">平台爬取</span>
                    )}
                    <button type="button" className="g-btn g-btn-primary g-btn-sm mt-3 w-full" disabled={tradeLoading} onClick={(e) => { e.stopPropagation(); doBuy(p.address); }}>
                      买 {buyAmount} BNB
                    </button>
                  </div>
                )) : (
                  <p className="col-span-full py-16 text-center text-[rgba(255,180,120,0.4)]">正在爬取 four.meme…</p>
                )}
              </div>
            )}

            {tab === "trade" && (
              <div className="mx-auto max-w-lg space-y-4">
                <div className="g-glass-card space-y-3">
                  <p className="text-[12px] font-semibold text-[rgba(255,200,150,0.7)]">手动买入</p>
                  <input className="g-input g-mono" value={ca} onChange={(e) => setCa(e.target.value)} placeholder="合约 0x…" />
                  <div className="flex gap-2">
                    <input className="g-input w-24" value={buyAmount} onChange={(e) => setBuyAmount(e.target.value)} />
                    <button type="button" className="g-btn g-btn-primary flex-1" disabled={tradeLoading} onClick={() => doBuy(ca)}>买入</button>
                    <button type="button" className="g-btn g-btn-ghost" disabled={tradeLoading} onClick={lookup}>评分</button>
                  </div>
                  {tradeMsg && <p className="text-[12px] text-[rgba(255,200,150,0.6)]">{tradeMsg}</p>}
                  {tokenEv && <p className="text-[12px] text-[#ffb87a]">{tokenEv.tier_emoji} {tokenEv.symbol} · {tokenEv.score} · {tokenEv.price_fmt}</p>}
                </div>
              </div>
            )}

            {tab === "positions" && (
              <div className="mx-auto max-w-2xl space-y-3">
                <p className="text-[12px] text-[rgba(255,180,120,0.5)]">手动卖出 — 提交后在 Cobo App 批准</p>
                {positions.length ? positions.map((p) => (
                  <div key={p.address} className="g-glass-card">
                    <div className="flex items-start justify-between gap-3">
                      <div>
                        <span className="text-[15px] font-semibold text-white">{p.symbol}</span>
                        {p.status === "pending" && <span className="ml-2 text-[10px] text-yellow-400">确认中</span>}
                        <p className="g-mono mt-1 text-[rgba(255,180,120,0.4)]">{fmtAddr(p.address)}</p>
                        <p className="mt-1 text-[11px] text-[rgba(255,200,150,0.45)]">买入 {p.amount_bnb} BNB{p.token_amount ? ` · 持 ${p.token_amount}` : ""}</p>
                      </div>
                      <span className={`text-[18px] font-bold tabular-nums ${(p.current_gain_pct ?? 0) >= 0 ? "text-emerald-400" : "text-rose-400"}`}>
                        {(p.current_gain_pct ?? 0) >= 0 ? "+" : ""}{p.current_gain_pct ?? 0}%
                      </span>
                    </div>
                    <div className="mt-4 flex flex-wrap gap-2">
                      <button type="button" className="g-btn g-btn-primary g-btn-sm" disabled={tradeLoading || p.status === "pending"} onClick={() => doBuy(p.address, 0.01)}>加仓 0.01</button>
                      {[25, 50, 100].map((pct) => (
                        <button key={pct} type="button" className={`g-btn g-btn-sm ${pct === 100 ? "g-btn-primary" : "g-btn-ghost"}`} disabled={tradeLoading} onClick={() => doSell(p.address, pct)}>
                          {pctLabel(pct)}
                        </button>
                      ))}
                    </div>
                  </div>
                )) : (
                  <p className="py-12 text-center text-[rgba(255,180,120,0.4)]">暂无持仓</p>
                )}
                {tradeMsg && tab === "positions" && <p className="text-[12px] text-[rgba(255,200,150,0.6)]">{tradeMsg}</p>}
              </div>
            )}

            {tab === "signals" && (
              <div className="space-y-2">
                <p className="mb-3 text-[12px] text-[rgba(255,180,120,0.45)]">{radarOk ? "雷达在线" : "等待 GMGN Key"}</p>
                {signals.slice(0, 20).map((s, i) => (
                  <div key={i} className="g-glass-card flex cursor-pointer items-center justify-between py-3" onClick={() => s.address && pick(s.address)} role="button" tabIndex={0}>
                    <span className="text-[13px] text-white">{s.symbol}</span>
                    <span className="font-bold text-[#ffb87a]">{s.score.toFixed(0)}</span>
                  </div>
                ))}
                <p className="mt-4 text-[12px] font-semibold text-[rgba(255,180,120,0.6)]">聪明钱雷达</p>
                {radar.slice(0, 10).map((r) => (
                  <div key={r.address} className="g-glass-card flex items-center justify-between py-3">
                    <button type="button" className="text-left text-[13px] text-white" onClick={() => pick(r.address)}>{r.emoji} {r.symbol}</button>
                    <button type="button" className="g-btn g-btn-primary g-btn-sm" disabled={tradeLoading} onClick={() => doBuy(r.address)}>买</button>
                  </div>
                ))}
                <p className="mt-4 text-[12px] font-semibold text-[rgba(255,180,120,0.6)]">候选 TOP</p>
                {candidates.slice(0, 8).map((p) => (
                  <div key={p.address} className="g-glass-card flex cursor-pointer justify-between py-3" onClick={() => pick(p.address)} role="button" tabIndex={0}>
                    <span>{p.tier_emoji} {p.symbol}</span>
                    <span className="font-bold text-[#ffb87a]">{p.score}</span>
                  </div>
                ))}
              </div>
            )}

            {tab === "ai" && (
              <div className="space-y-4">
                <div className="g-glass-card flex flex-wrap items-center gap-3">
                  <div className="flex-1">
                    <p className="text-[13px] font-semibold text-white">智能测试一次</p>
                    <p className="text-[11px] text-[rgba(255,180,120,0.5)]">爬取 four.meme 最新新币 → 强制 DeepSeek 分析一次（不自动买入）</p>
                  </div>
                  <button type="button" className="g-btn g-btn-primary shrink-0" disabled={aiTestLoading || !settings?.deepseek_configured} onClick={runAiTest}>
                    {aiTestLoading ? "运行中…" : "运行智能测试"}
                  </button>
                </div>
                {aiTestMsg && <pre className="g-glass-card whitespace-pre-wrap text-[12px] text-[rgba(255,200,150,0.75)]">{aiTestMsg}</pre>}

                <div className="grid gap-4 md:grid-cols-3">
                  <div className="g-glass-card">
                    <p className="mb-3 text-[11px] font-semibold uppercase tracking-wider text-[rgba(255,160,90,0.5)]">自主交易</p>
                    {(aiTrader?.decisions ?? []).slice(0, 5).map((d, i) => (
                      <div key={i} className="mb-3 border-b border-[rgba(255,140,60,0.08)] pb-3 last:border-0">
                        <div className="flex justify-between">
                          <span className="text-[13px] text-white">{String(d.symbol)}</span>
                          <span className="text-[10px] uppercase text-[#ffb87a]">{String(d.action)}</span>
                        </div>
                        {d.amount_bnb != null && (
                          <p className="mt-1 text-[11px] text-[#ffb87a]">
                            本笔 {String(d.amount_bnb)} BNB
                            {typeof d.sizing === "object" && d.sizing !== null && "balance_bnb" in d.sizing
                              ? ` · 余额 ${String((d.sizing as { balance_bnb?: unknown }).balance_bnb)}`
                              : ""}
                          </p>
                        )}
                        {d.reason != null && <p className="mt-1 text-[11px] text-[rgba(255,200,150,0.45)]">{String(d.reason)}</p>}
                      </div>
                    ))}
                    {!aiTrader?.decisions?.length && <p className="text-[12px] text-[rgba(255,180,120,0.4)]">等待决策</p>}
                  </div>
                  <div className="g-glass-card">
                    <p className="mb-3 text-[11px] font-semibold uppercase tracking-wider text-[rgba(255,160,90,0.5)]">复盘</p>
                    {aiInsight && <p className="mb-3 text-[12px] leading-relaxed text-[rgba(255,200,150,0.7)]">{aiInsight}</p>}
                    {aiThoughts.slice(0, 4).map((t, i) => (
                      <div key={i} className="mb-2 text-[12px]">
                        <p className="font-medium text-white">{t.symbol}</p>
                        {t.why && <p className="text-[rgba(255,200,150,0.45)]">{t.why}</p>}
                      </div>
                    ))}
                  </div>
                  <div className="g-glass-card">
                    <p className="mb-3 text-[11px] font-semibold uppercase tracking-wider text-[rgba(255,160,90,0.5)]">八席竞技场</p>
                    {agents.map((a) => {
                      const threshold = a.effective_buy_threshold ?? a.buy_threshold;
                      return (
                        <div key={a.id} className="mb-2 flex justify-between gap-3 text-[12px]">
                          <span className="min-w-0 truncate text-[rgba(255,220,190,0.7)]">{a.ticker} {a.name}</span>
                          <span className={a.strategy_enabled === false ? "text-[rgba(255,180,120,0.35)]" : "text-[#ffb87a]"}>
                            {a.strategy_enabled === false ? "停用" : `≥${threshold}`}
                            <span className="ml-1 text-[10px] opacity-60">w{(a.strategy_weight ?? 1).toFixed(1)}</span>
                          </span>
                        </div>
                      );
                    })}
                  </div>
                </div>
              </div>
            )}
          </div>
        </main>
      </div>
    </div>
  );
}
