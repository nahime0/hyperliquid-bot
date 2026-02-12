import useSWR from "swr";
import type { AggregateStats } from "@/lib/types";

const fetcher = (url: string) => fetch(url).then((r) => r.json());

export function useStats() {
  const { data, error, isLoading } = useSWR<AggregateStats>(
    "/api/stats",
    fetcher,
    { refreshInterval: 10000 }
  );
  return { stats: data, error, isLoading };
}
