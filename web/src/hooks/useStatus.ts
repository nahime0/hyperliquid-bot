import useSWR from "swr";
import type { BotStatus } from "@/lib/types";

const fetcher = (url: string) => fetch(url).then((r) => r.json());

export function useStatus() {
  const { data, error, isLoading } = useSWR<BotStatus>("/api/status", fetcher, {
    refreshInterval: 2000,
  });
  return { status: data, error, isLoading };
}
