import useSWR from "swr";
import type { Trade } from "@/lib/types";

const fetcher = (url: string) => fetch(url).then((r) => r.json());

export function useTrades(limit = 50, offset = 0) {
  const { data, error, isLoading } = useSWR<Trade[]>(
    `/api/trades?limit=${limit}&offset=${offset}`,
    fetcher,
    { refreshInterval: 10000 }
  );
  return { trades: Array.isArray(data) ? data : [], error, isLoading };
}
