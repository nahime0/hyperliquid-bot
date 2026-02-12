import useSWR from "swr";
import type { Event } from "@/lib/types";

const fetcher = (url: string) => fetch(url).then((r) => r.json());

interface EventsResponse {
  events: Event[];
  total: number;
  limit: number;
  offset: number;
}

export function useEvents(params?: {
  limit?: number;
  offset?: number;
  event_type?: string;
  symbol?: string;
  cycle?: number;
}) {
  const qs = new URLSearchParams();
  if (params?.limit) qs.set("limit", String(params.limit));
  if (params?.offset) qs.set("offset", String(params.offset));
  if (params?.event_type) qs.set("event_type", params.event_type);
  if (params?.symbol) qs.set("symbol", params.symbol);
  if (params?.cycle !== undefined) qs.set("cycle", String(params.cycle));

  const { data, error, isLoading } = useSWR<EventsResponse>(
    `/api/events?${qs.toString()}`,
    fetcher,
    { refreshInterval: 5000 }
  );
  return {
    events: data?.events ?? [],
    total: data?.total ?? 0,
    error,
    isLoading,
  };
}
