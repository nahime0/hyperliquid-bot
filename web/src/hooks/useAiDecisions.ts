import useSWR from "swr";
import type { AiDecision } from "@/lib/types";

const fetcher = (url: string) => fetch(url).then((r) => r.json());

interface AiDecisionsResponse {
  decisions: AiDecision[];
  total: number;
  limit: number;
  offset: number;
}

export function useAiDecisions(limit = 50, offset = 0) {
  const { data, error, isLoading } = useSWR<AiDecisionsResponse>(
    `/api/ai-decisions?limit=${limit}&offset=${offset}`,
    fetcher,
    { refreshInterval: 10000 }
  );
  return {
    decisions: data?.decisions ?? [],
    total: data?.total ?? 0,
    error,
    isLoading,
  };
}

interface AiStatsData {
  total_decisions: number;
  executed_count: number;
  execution_rate: number;
  total_cost_usd: number;
  avg_confidence: number;
  by_tier: { tier: string; cnt: number }[];
  by_action: { action: string; cnt: number }[];
}

export function useAiStats() {
  const { data, error, isLoading } = useSWR<AiStatsData>(
    "/api/ai-decisions?stats=true",
    fetcher,
    { refreshInterval: 30000 }
  );
  return { stats: data, error, isLoading };
}
