"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  api,
  type AgentItem,
  type AiThought,
  type PoolItem,
  type PositionItem,
  type RadarItem,
  type WalletBalance,
} from "@/lib/api";

type SignalItem = {
  symbol: string;
  score: number;
  source?: string;
  reason?: string;
  address?: string;
};

export function useDashboardData() {
  const [wallet, setWallet] = useState<WalletBalance | null>(null);
  const [pools, setPools] = useState<PoolItem[]>([]);
  const [candidates, setCandidates] = useState<PoolItem[]>([]);
  const [positions, setPositions] = useState<PositionItem[]>([]);
  const [signals, setSignals] = useState<SignalItem[]>([]);
  const [radar, setRadar] = useState<RadarItem[]>([]);
  const [radarOk, setRadarOk] = useState(false);
  const [agents, setAgents] = useState<AgentItem[]>([]);
  const [aiThoughts, setAiThoughts] = useState<AiThought[]>([]);
  const [aiInsight, setAiInsight] = useState("");
  const [aiTrader, setAiTrader] = useState<{ enabled: boolean; decisions: Record<string, unknown>[] } | null>(null);
  const [pactStatus, setPactStatus] = useState("unknown");
  const [lastSync, setLastSync] = useState(0);
  const [settings, setSettings] = useState<{
    deepseek_configured?: boolean;
    cobo_configured?: boolean;
    gmgn_configured?: boolean;
    scan_interval?: number;
    scan?: { interval: number; pool_count: number; candidate_count: number; mode: string };
  } | null>(null);
  const [ready, setReady] = useState(false);

  const visibleRef = useRef(true);
  const fastPollRef = useRef(false);

  const applyPortfolio = useCallback((pf: Awaited<ReturnType<typeof api.portfolio>> | null) => {
    if (!pf?.data) return;
    setWallet(pf.data.wallet);
    setPositions(pf.data.positions);
    setPactStatus(pf.data.pact.status);
    setLastSync(pf.data.ts);
  }, []);

  const refreshPortfolio = useCallback(async () => {
    if (!visibleRef.current) return;
    const pf = await api.portfolio().catch(() => null);
    applyPortfolio(pf);
  }, [applyPortfolio]);

  const refreshPools = useCallback(async () => {
    if (!visibleRef.current) return;
    const [p, c] = await Promise.all([
      api.pools().catch(() => ({ data: [] as PoolItem[] })),
      api.candidates().catch(() => ({ data: [] as PoolItem[] })),
    ]);
    setPools(p.data);
    setCandidates(c.data);
  }, []);

  const refreshSignals = useCallback(async () => {
    if (!visibleRef.current) return;
    const [sig, rad] = await Promise.all([
      api.signals().catch(() => ({ data: [], opportunities: [] })),
      api.radar().catch(() => ({ data: { items: [] as RadarItem[], available: false } })),
    ]);
    const sigList = [...sig.data, ...(sig.opportunities ?? [])]
      .filter(
        (s, i, arr) =>
          arr.findIndex((x) => (x as { address?: string }).address === (s as { address?: string }).address) === i
      )
      .map((s) => ({
        symbol: s.symbol,
        score: s.score,
        source: (s as { source?: string }).source,
        reason: (s as { reason?: string }).reason,
        address: (s as { address?: string }).address,
      }));
    setSignals(sigList);
    setRadar(rad.data.items);
    setRadarOk(rad.data.available);
  }, []);

  const refreshAi = useCallback(async () => {
    if (!visibleRef.current) return;
    const [ai, at, ag] = await Promise.all([
      api.aiFeed().catch(() => ({ data: { thoughts: [], insight: "", rules: [], learning: { rules: [], avoid: [] } } })),
      api.aiTrader().catch(() => ({ data: { enabled: false, decisions: [], trades: [], open_positions: 0 } })),
      api.agents().catch(() => ({ data: [] as AgentItem[] })),
    ]);
    setAiThoughts(ai.data.thoughts);
    setAiInsight(ai.data.insight);
    setAiTrader(at.data);
    setAgents(ag.data);
  }, []);

  const refreshSettings = useCallback(async () => {
    const st = await api.settings().catch(() => null);
    setSettings(st?.data ?? null);
  }, []);

  const refreshAll = useCallback(async () => {
    await Promise.all([refreshPortfolio(), refreshPools(), refreshSignals(), refreshAi(), refreshSettings()]);
  }, [refreshPortfolio, refreshPools, refreshSignals, refreshAi, refreshSettings]);

  const setFastPoll = useCallback((on: boolean) => {
    fastPollRef.current = on;
  }, []);

  useEffect(() => {
    const onVis = () => {
      visibleRef.current = !document.hidden;
      if (visibleRef.current) refreshPortfolio();
    };
    document.addEventListener("visibilitychange", onVis);

    (async () => {
      try {
        await Promise.allSettled([refreshPortfolio(), refreshPools(), refreshSettings()]);
      } finally {
        setReady(true);
      }
      setTimeout(() => {
        refreshSignals();
        refreshAi();
      }, 600);
    })();

    const portfolioIv = setInterval(() => {
      if (!visibleRef.current) return;
      refreshPortfolio();
    }, 5000);

    const poolsIv = setInterval(() => {
      if (!visibleRef.current) return;
      refreshPools();
    }, 15000);

    const signalsIv = setInterval(() => {
      if (!visibleRef.current) return;
      refreshSignals();
    }, 20000);

    const aiIv = setInterval(() => {
      if (!visibleRef.current) return;
      refreshAi();
    }, 20000);

    const fastIv = setInterval(() => {
      if (!visibleRef.current || !fastPollRef.current) return;
      refreshPortfolio();
    }, 4000);

    return () => {
      document.removeEventListener("visibilitychange", onVis);
      clearInterval(portfolioIv);
      clearInterval(poolsIv);
      clearInterval(signalsIv);
      clearInterval(aiIv);
      clearInterval(fastIv);
    };
  }, [refreshPortfolio, refreshPools, refreshSignals, refreshAi, refreshSettings]);

  return {
    wallet,
    pools,
    candidates,
    positions,
    signals,
    radar,
    radarOk,
    agents,
    aiThoughts,
    aiInsight,
    aiTrader,
    pactStatus,
    lastSync,
    settings,
    ready,
    refreshPortfolio,
    refreshPools,
    refreshAi,
    refreshAll,
    setFastPoll,
  };
}
