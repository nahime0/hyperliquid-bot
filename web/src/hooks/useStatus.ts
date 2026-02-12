import useSWR from "swr";
import type { DashboardStatus } from "@/lib/types";

const fetcher = (url: string) => fetch(url).then((r) => r.json());

export function useStatus() {
  const { data, error, isLoading } = useSWR<DashboardStatus>("/api/status", fetcher, {
    refreshInterval: 5000,
  });
  return { status: data, error, isLoading };
}
