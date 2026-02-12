import useSWR from "swr";
import type { Position } from "@/lib/types";

const fetcher = (url: string) => fetch(url).then((r) => r.json());

export function useOpenPositions() {
  const { data, error, isLoading } = useSWR<Position[]>(
    "/api/positions?status=open",
    fetcher,
    { refreshInterval: 5000 }
  );
  return { positions: Array.isArray(data) ? data : [], error, isLoading };
}

export function useClosedPositions(limit = 50, offset = 0) {
  const { data, error, isLoading } = useSWR<Position[]>(
    `/api/positions?status=closed&limit=${limit}&offset=${offset}`,
    fetcher,
    { refreshInterval: 10000 }
  );
  return { positions: Array.isArray(data) ? data : [], error, isLoading };
}
