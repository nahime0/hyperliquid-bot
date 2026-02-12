import useSWR from "swr";
import type { BalanceSnapshot } from "@/lib/types";

const fetcher = (url: string) => fetch(url).then((r) => r.json());

export function useEquity(limit = 500) {
  const { data, error, isLoading } = useSWR<BalanceSnapshot[]>(
    `/api/equity?limit=${limit}`,
    fetcher,
    { refreshInterval: 30000 }
  );
  return { snapshots: data ?? [], error, isLoading };
}
