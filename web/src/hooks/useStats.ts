import useSWR from "swr";
import type { TradeStats } from "@/lib/types";

const fetcher = (url: string) => fetch(url).then((r) => r.json());

export function useStats() {
  const { data, error, isLoading } = useSWR<TradeStats & { total_pnl_all: number; current_balance: number; peak_balance: number; open_position_count: number }>(
    "/api/stats",
    fetcher,
    { refreshInterval: 10000 }
  );
  return { stats: data, error, isLoading };
}
