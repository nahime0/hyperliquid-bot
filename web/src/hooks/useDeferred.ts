import useSWR from "swr";
import type { DeferredOpportunity } from "@/lib/types";

const fetcher = (url: string) => fetch(url).then((r) => r.json());

export function useDeferred() {
  const { data, error, isLoading } = useSWR<DeferredOpportunity[]>(
    "/api/deferred",
    fetcher,
    { refreshInterval: 10000 }
  );
  return { deferred: data ?? [], error, isLoading };
}
